#!/usr/bin/env python3
"""
engine_helper.py - 自动化 PR 系统的核心数据操作工具

【治本目标】
1. sed `|` 分隔符冲突 -> 用 Python 字符串/AST 操作，零转义
2. 多阶段 commit 导致 SHA 滞后 -> 单次原子写入 + 提交 + push
3. 占位符泄漏（"待本次提交"）-> 写入时即用真实值
4. 多 commit ping-pong -> 一次 commit 内完成所有更新

【使用方式】
  python3 engine_helper.py parse                    # 解析进度表 -> JSON
  python3 engine_helper.py set KEY=VAL [KEY=VAL..]  # 原子更新字段
  python3 engine_helper.py set-section SECTION KEY=VAL [KEY=VAL..]  # 限定 section
  python3 engine_helper.py heartbeat MSG            # 心跳：更新 + 提交 + push

【设计原则】
- 无外部依赖（只用 stdlib）
- 幂等：同一命令多次执行结果一致
- 写后再读验证：确保更新真的落盘
- 不删除/重写未在 updates 中出现的字段
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, Optional

REPO_ROOT = Path(os.environ.get("HJB_ROOT", "/workspace/HJB"))
PROG_PATH = REPO_ROOT / "核聚变开源贡献系统" / "进度表.md"
BRANCH = os.environ.get("HJB_BRANCH", "trae/solo-agent-TbCBsF")

# ─────────────────────────────────────────────────────────────────────
# 解析
# ─────────────────────────────────────────────────────────────────────
_TABLE_RE = re.compile(r"^\|\s*(?P<key>[^|]+?)\s*\|\s*(?P<val>.+?)\s*\|$")
_SEP_RE = re.compile(r"^[\s|:-]+$")
_H2_RE = re.compile(r"^##\s+(?P<title>.+?)\s*$")
_CODE_FENCE = "```"
# 注：value 形如 "...  ;; 注释" 时才把 ;; 视为注释。
# 不用 # 是因为 # 会与 value 里的 "#3" "#修复" 等自由文本冲突，触发非贪婪截断 bug。
# ;; 在 codeblock 里没自然出现，专作 comment marker。
#
# 关键陷阱：之前的实现用 (?P<val>.+?)(?:\s+;;.*)?$ 非贪婪匹配，会找**第一个** ;; 截断。
# 即便换 marker 也无法解决 —— 因为 value 里若含 marker 本身就被误判。
# 治本方案：value 贪婪匹配整行，然后用 _strip_trailing_comment 找**最后一个** marker 切分。
_KV_IN_CODE_RE = re.compile(r"^(?P<key>[A-Z_][A-Z0-9_]*)\s*:\s*(?P<rest>.+)$")
_KV_COMMENT_MARKER_RE = re.compile(r"\s+;;\s+[A-Za-z\u4e00-\u9fff]")


def _strip_trailing_comment(rest: str) -> str:
    """找**最后一个** comment marker 位置并切掉其后的注释。
    保证 value 中即使含 ;; 也只会被当作"最后那个"才视为注释起点。
    """
    matches = list(_KV_COMMENT_MARKER_RE.finditer(rest))
    if matches:
        return rest[: matches[-1].start()].rstrip()
    return rest.rstrip()


def _is_artifact_h2(title: str) -> bool:
    """顶部 ╔ ║ ╚ 装饰框行不是真 section。"""
    return any(title.startswith(c) for c in ("╔", "║", "╚"))


def parse_progress(path: Path = PROG_PATH) -> Dict[str, Dict[str, str]]:
    """解析进度表为 {section: {field: value}} 字典。

    表格分隔行（|---|---|）自动跳过。
    代码块（``` ... ```）内的 | 不会被误识别为表格。
    代码块内形如 `KEY: VALUE` 的行被解析到 _codeblock 段。
    顶部 ╔║╚ 装饰框行归到 _artifacts 段，不污染主 section。
    """
    text = path.read_text(encoding="utf-8")
    sections: Dict[str, Dict[str, str]] = {"_root": {}}
    current = "_root"
    in_code = False

    for line in text.splitlines():
        if line.strip().startswith(_CODE_FENCE):
            in_code = not in_code
            current = "_codeblock" if in_code else "_root"
            sections.setdefault(current, {})
            continue
        if in_code:
            m = _KV_IN_CODE_RE.match(line.strip())
            if m:
                # 贪婪捕获整行后，用 _strip_trailing_comment 找**最后一个** marker 切注释
                value = _strip_trailing_comment(m.group("rest"))
                sections[current][m.group("key")] = value
            continue
        m = _H2_RE.match(line)
        if m:
            title = m.group("title").strip()
            if _is_artifact_h2(title):
                current = "_artifacts"
            else:
                current = title
            sections.setdefault(current, {})
            continue
        m = _TABLE_RE.match(line)
        if m:
            key, val = m.group("key").strip(), m.group("val").strip()
            if _SEP_RE.match(key):
                continue
            sections.setdefault(current, {})[key] = val
    return sections


# ─────────────────────────────────────────────────────────────────────
# 原子更新
# ─────────────────────────────────────────────────────────────────────
def _flatten(updates: Dict) -> Dict[str, str]:
    flat: Dict[str, str] = {}
    for k, v in updates.items():
        if isinstance(v, dict):
            for fk, fv in v.items():
                flat[str(fk)] = str(fv)
        else:
            flat[str(k)] = str(v)
    return flat


def update_fields(
    updates: Dict,
    path: Path = PROG_PATH,
    section: Optional[str] = None,
) -> Dict[str, str]:
    """原子更新表格/codeblock 字段。
    updates: {field: val} 或 {section: {field: val}}
    section: 限定只更新此 section 下的字段（None = 全部 section 第一个匹配）
    """
    flat = _flatten(updates)
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    out_lines: list[str] = []
    in_code = False
    current = "_root"
    hits: Dict[str, str] = {}

    for line in lines:
        if line.strip().startswith(_CODE_FENCE):
            in_code = not in_code
            current = "_codeblock" if in_code else "_root"
            out_lines.append(line)
            continue
        if in_code:
            stripped = line.strip()
            m = _KV_IN_CODE_RE.match(stripped)
            if m:
                key = m.group("key")
                in_scope = (section is None) or (current == section)
                if in_scope and key in flat:
                    indent = line[: len(line) - len(line.lstrip())]
                    new_val = flat.pop(key)
                    line = f"{indent}{key}: {new_val}"
                    hits[key] = new_val
            out_lines.append(line)
            continue
        m_h2 = _H2_RE.match(line)
        if m_h2:
            title = m_h2.group("title").strip()
            current = "_artifacts" if _is_artifact_h2(title) else title
            out_lines.append(line)
            continue
        m = _TABLE_RE.match(line)
        if m:
            key = m.group("key").strip()
            if _SEP_RE.match(key):
                out_lines.append(line)
                continue
            in_scope = (section is None) or (current == section)
            if in_scope and key in flat:
                new_val = flat.pop(key)
                line = f"| {key} | {new_val} |"
                hits[key] = new_val
        out_lines.append(line)

    new_text = "\n".join(out_lines) + "\n"
    if not new_text.endswith("\n") and text.endswith("\n"):
        new_text += "\n"
    path.write_text(new_text, encoding="utf-8")
    return hits


def verify_field(field: str, expected: str, path: Path = PROG_PATH) -> bool:
    data = parse_progress(path)
    for sec, kv in data.items():
        if field in kv:
            return kv[field] == expected
    return False


# ─────────────────────────────────────────────────────────────────────
# 心跳
# ─────────────────────────────────────────────────────────────────────
def _run(cmd: list, cwd: Path = REPO_ROOT):
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)


def heartbeat(msg: str = "engine: heartbeat", extra_updates: Optional[Dict] = None,
              path: Path = PROG_PATH) -> str:
    cur_sha = _run(["git", "rev-parse", "--short", "HEAD"]).stdout.strip()
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    updates = {
        "LAST_HEARTBEAT": now,
        "LAST_HEARTBEAT_COMMIT": cur_sha,
        "HEAD commit SHA": cur_sha,
        "LAST_HEARTBEAT_STATUS": "ok",
    }
    if extra_updates:
        updates.update(_flatten(extra_updates))
    update_fields(updates, path)
    _run(["git", "add", "-A"])
    res = _run(["git", "commit", "-m", msg])
    if res.returncode != 0 and "nothing to commit" not in (res.stdout + res.stderr):
        return f"commit_failed: {res.stderr.strip()}"
    push = _run(["git", "push", "origin", BRANCH])
    if push.returncode != 0:
        return f"push_failed: {push.stderr.strip()}"
    new_sha = _run(["git", "rev-parse", "--short", "HEAD"]).stdout.strip()
    return new_sha


# ─────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────
def _parse_kv(args):
    out = {}
    for a in args:
        if "=" not in a:
            print(f"error: bad arg {a!r}, want KEY=VAL", file=sys.stderr)
            sys.exit(2)
        k, v = a.split("=", 1)
        out[k] = v
    return out


def main(argv):
    if len(argv) < 2:
        print(__doc__)
        return 1
    cmd = argv[1]
    if cmd == "parse":
        print(json.dumps(parse_progress(), ensure_ascii=False, indent=2))
        return 0
    if cmd == "set":
        if len(argv) < 3:
            print("usage: set KEY=VAL [KEY=VAL..]", file=sys.stderr)
            return 2
        hits = update_fields(_parse_kv(argv[2:]))
        data = parse_progress()
        ok = True
        for k, v in hits.items():
            actual = next((kv.get(k) for kv in data.values() if k in kv), None)
            if actual != v:
                print(f"verify_failed: {k} expected={v!r} actual={actual!r}", file=sys.stderr)
                ok = False
        if not ok:
            return 3
        print(f"ok: {len(hits)} field(s) updated and verified: {list(hits.keys())}")
        return 0
    if cmd == "set-section":
        if len(argv) < 4:
            print("usage: set-section SECTION KEY=VAL [KEY=VAL..]", file=sys.stderr)
            return 2
        hits = update_fields(_parse_kv(argv[3:]), section=argv[2])
        print(f"ok: section={argv[2]!r} hits={list(hits.keys())}")
        return 0
    if cmd == "heartbeat":
        msg = argv[2] if len(argv) > 2 else "engine: heartbeat"
        sha = heartbeat(msg)
        print(f"heartbeat: {sha}")
        return 0 if not sha.startswith(("commit_failed", "push_failed")) else 4
    print(f"unknown cmd: {cmd!r}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
