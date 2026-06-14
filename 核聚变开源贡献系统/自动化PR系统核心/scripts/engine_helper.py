#!/usr/bin/env python3
"""
engine_helper.py - 自动化 PR 系统的核心数据操作工具

【治本目标】
1. sed `|` 分隔符冲突 -> 用 Python 字符串/AST 操作，零转义
2. 多阶段 commit 导致 SHA 滞后 -> 单次原子写入 + 提交 + push
3. 占位符泄漏（"待本次提交"）-> 写入时即用真实值
4. 多 commit ping-pong -> 一次 commit 内完成所有更新

【使用方式】
  python3 engine_helper.py parse                    # 解析主进度表 -> JSON
  python3 engine_helper.py set KEY=VAL [KEY=VAL..]  # 原子更新主表字段
  python3 engine_helper.py set-section SECTION KEY=VAL [KEY=VAL..]  # 限定 section
  python3 engine_helper.py heartbeat MSG            # 心跳：更新 + 提交 + push
  python3 engine_helper.py list-active              # 列活跃项目（来自主表 多项目并进机制 v2.1）
  python3 engine_helper.py list-queue               # 列未启动队列
  python3 engine_helper.py add-active NAME REPO [STATE] [PR] [AGE] [LAST_REVIEW] [NEXT_STEP]
  python3 engine_helper.py update-active NAME KEY=VAL [KEY=VAL..]
  python3 engine_helper.py remove-from-queue NAME
  python3 engine_helper.py project-parse NAME       # 解析子进度表 -> JSON
  python3 engine_helper.py project-get NAME FIELD   # 读子表字段
  python3 engine_helper.py project-set NAME FIELD=VAL [FIELD=VAL..]  # 原子更新子表字段

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
# 多项目并进机制 v2.1
# ─────────────────────────────────────────────────────────────────────
# 进度表中：
#   ## 多项目并进机制 v2.1
#   ### 活跃项目状态
#   | 项目 | 仓库 | 状态 | PR | PR 龄期 | 上次 review 检查 | 下一步 |
#   ### 未启动项目队列
#   | 顺序 | 项目 | 综合分 | 备注 |
#
# 本节函数都基于「多列表格」的简单解析（不依赖 _TABLE_RE 的 2 列假设）。

_ACTIVE_HEADER = ("项目", "仓库", "状态", "PR", "PR 龄期", "上次 review 检查", "下一步")
_QUEUE_HEADER = ("顺序", "项目", "综合分", "备注")


def _read_multicol_section(path: Path, h2_title: str, h3_title: str) -> List[List[str]]:
    """读 ## h2_title 段下 ### h3_title 子段的多列表格，返回行列表（不含表头/分隔）。"""
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    in_h2 = False
    in_h3 = False
    rows: List[List[str]] = []
    for line in lines:
        if line.startswith("## ") and h2_title in line:
            in_h2 = True
            in_h3 = False
            continue
        if in_h2 and line.startswith("### ") and h3_title in line:
            in_h3 = True
            continue
        if in_h2 and line.startswith("### "):
            in_h3 = False  # 离开了目标子段
        if in_h2 and line.startswith("## "):
            break  # 离开 h2 段
        if not in_h3 or not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        # 跳表头/分隔
        if not rows and cells and cells[0] in _ACTIVE_HEADER[:1]:
            continue
        if cells and re.match(r"^[\s|:-]+$", cells[0]):
            continue
        rows.append(cells)
    return rows


def _write_multicol_row(path: Path, h2_title: str, h3_title: str, header: tuple,
                        match_col: int, match_val: str, new_row: List[str]) -> bool:
    """在多列表格里按 match_col 匹配 match_val，把该行替换为 new_row。返回是否找到并替换。"""
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    in_h2 = False
    in_h3 = False
    found = False
    out: List[str] = []
    for line in lines:
        if line.startswith("## ") and h2_title in line:
            in_h2 = True
            in_h3 = False
            out.append(line)
            continue
        if in_h2 and line.startswith("### ") and h3_title in line:
            in_h3 = True
            out.append(line)
            continue
        if in_h2 and line.startswith("### "):
            in_h3 = False
        if in_h2 and line.startswith("## "):
            in_h2 = False
        if in_h3 and line.startswith("|"):
            cells = [c.strip() for c in line.strip("|").split("|")]
            if (not found
                and len(cells) > match_col
                and cells[match_col] == match_val
                and cells[0] != header[0]  # 不是表头
                and not re.match(r"^[\s|:-]+$", cells[0])):
                # 替换
                out.append("| " + " | ".join(new_row) + " |")
                found = True
                continue
        out.append(line)
    if found:
        path.write_text("\n".join(out) + "\n", encoding="utf-8")
    return found


def _append_multicol_row(path: Path, h2_title: str, h3_title: str, new_row: List[str]) -> None:
    """在多列表格末尾追加一行（找到表头后下一行插入）。"""
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    in_h2 = False
    in_h3 = False
    out: List[str] = []
    last_data_line_idx = -1  # in_h3 段内最后一个数据行的 out 索引
    for i, line in enumerate(lines):
        if line.startswith("## ") and h2_title in line:
            in_h2 = True
            in_h3 = False
        if in_h2 and line.startswith("### ") and h3_title in line:
            in_h3 = True
        if in_h2 and line.startswith("### ") and h3_title not in line and in_h3:
            in_h3 = False
        if in_h2 and line.startswith("## ") and h2_title not in line:
            in_h2 = False
        out.append(line)
        if in_h3 and line.startswith("|") and not re.match(r"^[\s|:-]+$", line.strip("|").split("|")[0] if "|" in line else ""):
            last_data_line_idx = len(out) - 1
    if last_data_line_idx >= 0:
        out.insert(last_data_line_idx + 1, "| " + " | ".join(new_row) + " |")
    else:
        out.append("| " + " | ".join(new_row) + " |")
    path.write_text("\n".join(out) + "\n", encoding="utf-8")


def list_active_projects(path: Path = PROG_PATH) -> List[Dict[str, str]]:
    """列出所有活跃项目。返回 [{项目, 仓库, 状态, PR, PR 龄期, 上次 review 检查, 下一步}, ...]"""
    rows = _read_multicol_section(path, "多项目并进机制 v2.1", "活跃项目状态")
    if not rows:
        return []
    out = []
    for r in rows:
        if len(r) < len(_ACTIVE_HEADER):
            r = r + [""] * (len(_ACTIVE_HEADER) - len(r))
        out.append(dict(zip(_ACTIVE_HEADER, r[:len(_ACTIVE_HEADER)])))
    return out


def get_active_project(name: str, path: Path = PROG_PATH) -> Optional[Dict[str, str]]:
    for p in list_active_projects(path):
        if p.get("项目", "").strip() == name:
            return p
    return None


def update_active_project(name: str, updates: Dict[str, str], path: Path = PROG_PATH) -> bool:
    """更新活跃项目表里指定 name 的若干字段。返回是否找到并更新。"""
    cur = get_active_project(name, path)
    if not cur:
        return False
    new_row = [cur.get(k, "") for k in _ACTIVE_HEADER]
    for k, v in updates.items():
        if k in _ACTIVE_HEADER:
            new_row[_ACTIVE_HEADER.index(k)] = v
    return _write_multicol_row(path, "多项目并进机制 v2.1", "活跃项目状态",
                               _ACTIVE_HEADER, 0, name, new_row)


def add_active_project(name: str, repo: str, state: str = "INIT",
                       pr: str = "—", age: str = "—",
                       last_review: str = "—", next_step: str = "—",
                       path: Path = PROG_PATH) -> None:
    """追加一个活跃项目行。"""
    if get_active_project(name, path):
        return  # 已存在
    row = [name, repo, state, pr, age, last_review, next_step]
    _append_multicol_row(path, "多项目并进机制 v2.1", "活跃项目状态", row)


def list_queue(path: Path = PROG_PATH) -> List[Dict[str, str]]:
    """列出未启动项目队列。"""
    rows = _read_multicol_section(path, "多项目并进机制 v2.1", "未启动项目队列")
    out = []
    for r in rows:
        # 过滤表头行
        if r and r[0] in _QUEUE_HEADER:
            continue
        if len(r) < len(_QUEUE_HEADER):
            r = r + [""] * (len(_QUEUE_HEADER) - len(r))
        out.append(dict(zip(_QUEUE_HEADER, r[:len(_QUEUE_HEADER)])))
    return out


def remove_from_queue(name: str, path: Path = PROG_PATH) -> bool:
    """从未启动队列移除指定项目（已晋升为活跃项目时调用）。"""
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    in_h2 = False
    in_h3 = False
    out: List[str] = []
    found = False
    for line in lines:
        if line.startswith("## ") and "多项目并进机制 v2.1" in line:
            in_h2 = True
            in_h3 = False
            out.append(line)
            continue
        if in_h2 and line.startswith("### ") and "未启动项目队列" in line:
            in_h3 = True
            out.append(line)
            continue
        if in_h2 and line.startswith("### ") and in_h3:
            in_h3 = False
        if in_h2 and line.startswith("## ") and "多项目并进机制 v2.1" not in line:
            in_h2 = False
        if in_h3 and line.startswith("|"):
            cells = [c.strip() for c in line.strip("|").split("|")]
            if (len(cells) >= 2 and cells[1] == name
                and not re.match(r"^[\s|:-]+$", cells[0])
                and cells[0] != "顺序"):
                found = True
                continue  # 跳过这行
        out.append(line)
    if found:
        path.write_text("\n".join(out) + "\n", encoding="utf-8")
    return found


# ─────────────────────────────────────────────────────────────────────
# Per-Project 子进度表（多项目并进机制 v2.1）
# ─────────────────────────────────────────────────────────────────────
# 每项目一个 `核聚变开源贡献系统/项目/<name>/进度表.md`（git-tracked，rule #13 满足）。
# 子表是简化版的进度表：只含 ## 基本信息 / ## 链状开发进度 / ## 检查点 /
# ## PR 收益与策略反馈 / ## 项目历史 / ## 维护者最近活动。
# 子表有自己的 ```codeblock``` 操作指令区（每个项目独立）。

PROJECTS_ROOT = REPO_ROOT / "核聚变开源贡献系统" / "项目"


def project_progress_path(name: str) -> Path:
    """子进度表绝对路径：核聚变开源贡献系统/项目/<name>/进度表.md"""
    return PROJECTS_ROOT / name / "进度表.md"


def _read_project_sections(path: Path) -> Tuple[Dict[str, Dict[str, str]], Dict[str, str]]:
    """复用主表 parse_progress 逻辑（子表结构相同，只是少了 system-level 段）。"""
    return parse_progress(path)


def project_parse(name: str) -> Dict:
    """读子表 -> 结构化 JSON（同主表 parse 格式）。"""
    p = project_progress_path(name)
    if not p.exists():
        raise FileNotFoundError(f"project sub-progress not found: {p}")
    return {"name": name, "path": str(p), **_read_project_sections(p)}


def project_get(name: str, field: str) -> Optional[str]:
    """读子表某字段（支持 `基本信息.项目名称` 这种 dotted path，或顶层 `KEY`）。"""
    data = project_parse(name)
    # 1) codeblock
    if field in data.get("_codeblock", {}):
        return data["_codeblock"][field]
    # 2) dotted: SECTION.KEY
    if "." in field:
        section, key = field.split(".", 1)
        return data.get(section, {}).get(key)
    # 3) top-level section dump（少见）：返回首个匹配段里所有 key 的 join
    return None


def project_set(name: str, updates: Dict[str, str]) -> bool:
    """原子更新子表字段。updates: {KEY: VAL} 或 {SECTION.KEY: VAL} 或 {SECTION: {KEY: VAL}}。
    返回是否全部更新成功。"""
    p = project_progress_path(name)
    if not p.exists():
        raise FileNotFoundError(f"project sub-progress not found: {p}")
    flat: Dict[str, str] = {}
    section_updates: Dict[str, Dict[str, str]] = {}
    for k, v in updates.items():
        if "." in k:
            section, key = k.split(".", 1)
            section_updates.setdefault(section, {})[key] = v
        else:
            flat[k] = v
    if flat:
        update_fields(flat, p)
    for sec, kvs in section_updates.items():
        update_fields(kvs, p, section=sec)
    return True


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
    if cmd == "list-active":
        projs = list_active_projects()
        print(json.dumps(projs, ensure_ascii=False, indent=2))
        return 0
    if cmd == "list-queue":
        q = list_queue()
        print(json.dumps(q, ensure_ascii=False, indent=2))
        return 0
    if cmd == "add-active":
        # add-active NAME REPO [STATE] [PR] [AGE] [LAST_REVIEW] [NEXT_STEP]
        if len(argv) < 4:
            print("usage: add-active NAME REPO [STATE] [PR] [AGE] [LAST_REVIEW] [NEXT_STEP]", file=sys.stderr)
            return 2
        a = argv[2:]
        add_active_project(a[0], a[1], a[2] if len(a) > 2 else "INIT",
                           a[3] if len(a) > 3 else "—",
                           a[4] if len(a) > 4 else "—",
                           a[5] if len(a) > 5 else "—",
                           a[6] if len(a) > 6 else "—")
        print(f"ok: added active project {a[0]!r}")
        return 0
    if cmd == "update-active":
        # update-active NAME KEY=VAL [KEY=VAL..]
        if len(argv) < 4:
            print("usage: update-active NAME KEY=VAL [KEY=VAL..]", file=sys.stderr)
            return 2
        name = argv[2]
        updates = _parse_kv(argv[3:])
        ok = update_active_project(name, updates)
        if not ok:
            print(f"error: project {name!r} not found in active list", file=sys.stderr)
            return 3
        print(f"ok: updated active project {name!r}: {list(updates.keys())}")
        return 0
    if cmd == "remove-from-queue":
        if len(argv) < 3:
            print("usage: remove-from-queue NAME", file=sys.stderr)
            return 2
        ok = remove_from_queue(argv[2])
        if not ok:
            print(f"error: project {argv[2]!r} not in queue", file=sys.stderr)
            return 3
        print(f"ok: removed {argv[2]!r} from queue")
        return 0
    if cmd == "project-parse":
        if len(argv) < 3:
            print("usage: project-parse NAME", file=sys.stderr)
            return 2
        try:
            data = project_parse(argv[2])
        except FileNotFoundError as e:
            print(f"error: {e}", file=sys.stderr)
            return 3
        print(json.dumps(data, ensure_ascii=False, indent=2))
        return 0
    if cmd == "project-get":
        if len(argv) < 4:
            print("usage: project-get NAME FIELD", file=sys.stderr)
            return 2
        try:
            v = project_get(argv[2], argv[3])
        except FileNotFoundError as e:
            print(f"error: {e}", file=sys.stderr)
            return 3
        if v is None:
            print(f"(field {argv[3]!r} not found)", file=sys.stderr)
            return 4
        print(v)
        return 0
    if cmd == "project-set":
        if len(argv) < 4:
            print("usage: project-set NAME KEY=VAL [KEY=VAL..]", file=sys.stderr)
            return 2
        updates = _parse_kv(argv[3:])
        try:
            project_set(argv[2], updates)
        except FileNotFoundError as e:
            print(f"error: {e}", file=sys.stderr)
            return 3
        print(f"ok: updated project {argv[2]!r}: {list(updates.keys())}")
        return 0
    print(f"unknown cmd: {cmd!r}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
