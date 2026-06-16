"""V4 per-project 子进度表读写。

继承 v2.1 engine_helper.project_parse / project_get / project_set 的所有能力。
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Dict, Optional

REPO_ROOT = Path(os.environ.get("HJB_ROOT", "/workspace/HJB/HJB"))
PROJECTS_ROOT = REPO_ROOT / "核聚变开源贡献系统" / "项目"

# 复用 v2.1 的 regex 治本修复
_TABLE_RE = re.compile(r"^\|\s*(?P<key>[^|]+?)\s*\|\s*(?P<val>.+?)\s*\|$")
_SEP_RE = re.compile(r"^[\s|:-]+$")
_H2_RE = re.compile(r"^##\s+(?P<title>.+?)\s*$")
_CODE_FENCE = "```"
_KV_IN_CODE_RE = re.compile(r"^(?P<key>[A-Z_][A-Z0-9_]*)\s*:\s*(?P<rest>.+)$")
_KV_COMMENT_MARKER_RE = re.compile(r"\s+;;\s+[A-Za-z\u4e00-\u9fff]")


def _strip_trailing_comment(rest: str) -> str:
    matches = list(_KV_COMMENT_MARKER_RE.finditer(rest))
    if matches:
        return rest[: matches[-1].start()].rstrip()
    return rest.rstrip()


def _is_artifact_h2(title: str) -> bool:
    return any(title.startswith(c) for c in ("╔", "║", "╚"))


def _parse_sub(path: Path) -> Dict[str, Dict[str, str]]:
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


def _atomic_update_sub(updates: Dict[str, str], path: Path) -> Dict[str, str]:
    """治本：原子更新子表字段。"""
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
                if key in updates:
                    indent = line[: len(line) - len(line.lstrip())]
                    new_val = updates.pop(key)
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
            if key in updates:
                new_val = updates.pop(key)
                line = f"| {key} | {new_val} |"
                hits[key] = new_val
        out_lines.append(line)
    new_text = "\n".join(out_lines) + "\n"
    path.write_text(new_text, encoding="utf-8")
    return hits


class ProjectProgressRepo:
    """per-project 子进度表仓库。"""

    def __init__(self, root: Path = PROJECTS_ROOT) -> None:
        self.root = root

    def path_for(self, name: str) -> Path:
        return self.root / name / "进度表.md"

    def try_load(self, name: str) -> Optional[Dict[str, str]]:
        """加载子表 codeblock（轻量，不解析全表）。"""
        p = self.path_for(name)
        if not p.exists():
            return None
        return _parse_sub(p).get("_codeblock", {})

    def parse(self, name: str) -> Dict[str, Dict[str, str]]:
        p = self.path_for(name)
        if not p.exists():
            raise FileNotFoundError(f"project sub-progress not found: {p}")
        return _parse_sub(p)

    def update(self, name: str, updates: Dict[str, str]) -> Dict[str, str]:
        p = self.path_for(name)
        if not p.exists():
            raise FileNotFoundError(f"project sub-progress not found: {p}")
        return _atomic_update_sub(updates, p)

    def list_projects(self) -> list[str]:
        if not self.root.exists():
            return []
        return sorted(
            d.name for d in self.root.iterdir()
            if d.is_dir() and (d / "进度表.md").exists()
        )
