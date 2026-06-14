"""V4 主进度表读写。

继承 v2.1 engine_helper 的治本 regex 修复（comment marker = ;; + last-occurrence split）。
新增 to_engine_state / write_engine_state 把进度表 ↔ EngineState 转换集中。
"""
from __future__ import annotations

import os
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from ..core.models import (
    ContributionState,
    EngineState,
    ErrorBudgetStatus,
    ProjectState,
    StrategyMode,
    SystemMetrics,
    WipStatus,
)
from . import git_ops

REPO_ROOT = Path(os.environ.get("HJB_ROOT", "/workspace/HJB/HJB"))
PROG_PATH = REPO_ROOT / "核聚变开源贡献系统" / "进度表.md"
BRANCH = os.environ.get("HJB_BRANCH", "trae/solo-agent-TbCBsF")


# ─────────────────────────────────────────────────────────────────────
# 治本 regex（继承 v2.1 engine_helper）
# ─────────────────────────────────────────────────────────────────────
_TABLE_RE = re.compile(r"^\|\s*(?P<key>[^|]+?)\s*\|\s*(?P<val>.+?)\s*\|$")
_SEP_RE = re.compile(r"^[\s|:-]+$")
_H2_RE = re.compile(r"^##\s+(?P<title>.+?)\s*$")
_H3_RE = re.compile(r"^###\s+(?P<title>.+?)\s*$")
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


# ─────────────────────────────────────────────────────────────────────
# 进度表 → EngineState
# ─────────────────────────────────────────────────────────────────────
_ACTIVE_HEADER = ("项目", "仓库", "状态", "PR", "PR 龄期", "上次 review 检查", "下一步")
_QUEUE_HEADER = ("顺序", "项目", "综合分", "备注")


def parse(path: Path = PROG_PATH) -> Dict[str, Dict[str, str]]:
    """解析主进度表为 {section: {field: value}} 字典。

    顶部 ╔║╚ 装饰框行归到 _artifacts 段。
    代码块（``` ... ```）内的 | 不会被误识别为表格。
    代码块内形如 `KEY: VALUE` 的行被解析到 _codeblock 段。
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


def _read_multicol_section(path: Path, h2_title: str, h3_title: str) -> List[List[str]]:
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
            in_h3 = False
        if in_h2 and line.startswith("## "):
            break
        if not in_h3 or not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if not rows and cells and cells[0] in _ACTIVE_HEADER[:1]:
            continue
        if cells and re.match(r"^[\s|:-]+$", cells[0]):
            continue
        rows.append(cells)
    return rows


def _active_projects_to_state(path: Path = PROG_PATH) -> List[ProjectState]:
    rows = _read_multicol_section(path, "多项目并进机制 v4", "活跃项目状态")
    projects: List[ProjectState] = []
    for r in rows:
        if len(r) < len(_ACTIVE_HEADER):
            r = r + [""] * (len(_ACTIVE_HEADER) - len(r))
        d = dict(zip(_ACTIVE_HEADER, r[:len(_ACTIVE_HEADER)]))
        # PR 龄期
        age_str = d.get("PR 龄期", "—")
        age_h = 0.0
        if age_str.endswith("h") and age_str[:-1].replace(".", "").isdigit():
            try:
                age_h = float(age_str[:-1])
            except ValueError:
                pass
        pr_n = None
        if d.get("PR", "—").startswith("#"):
            try:
                pr_n = int(d["PR"][1:])
            except ValueError:
                pass
        projects.append(ProjectState(
            name=d.get("项目", "").strip(),
            repo=d.get("仓库", "").strip(),
            state=_to_state(d.get("状态", "BACKLOG")),
            pr_number=pr_n,
            pr_age_hours=age_h,
            last_review_check=d.get("上次 review 检查", "—"),
            notes=d.get("下一步", "—"),
        ))
    return projects


def _to_state(s: str) -> ContributionState:
    s = s.strip()
    for st in ContributionState:
        if st.value == s:
            return st
    return ContributionState.BACKLOG


def _queue_to_state(path: Path = PROG_PATH) -> List[Dict[str, str]]:
    rows = _read_multicol_section(path, "多项目并进机制 v4", "未启动项目队列")
    out = []
    for r in rows:
        if r and r[0] in _QUEUE_HEADER:
            continue
        if len(r) < len(_QUEUE_HEADER):
            r = r + [""] * (len(_QUEUE_HEADER) - len(r))
        out.append(dict(zip(_QUEUE_HEADER, r[:len(_QUEUE_HEADER)])))
    return out


# ─────────────────────────────────────────────────────────────────────
# EngineState → 进度表
# ─────────────────────────────────────────────────────────────────────
def _flush_codeblock_fields(state: EngineState) -> Dict[str, str]:
    """把 EngineState 关键字段映射到 codeblock 字段。"""
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    sha = git_ops.current_sha()
    submitted = [p.name for p in state.submitted_projects]
    next_action = "MAINTAIN" if state.has_submitted else "DEVELOP"
    return {
        "V4_VERSION": "4",
        "NEXT_ACTION": next_action,
        "STRATEGY_MODE": state.strategy_mode.value,
        "WIP_STATUS": state.metrics.wip_status.value,
        "ERROR_BUDGET_STATUS": state.metrics.error_budget.value,
        "LOCK": "true" if state.metrics.lock else "false",
        "HEAD_COMMIT_SHA": sha,
        "LAST_HEARTBEAT": now,
        "LAST_HEARTBEAT_COMMIT": sha,
        "LAST_HEARTBEAT_STATUS": "ok",
        "LAST_HEARTBEAT_NOTE": f"v4 tick @ {now}; active={len(state.projects)} submitted={len(submitted)}",
        "ACTIVE_PROJECTS": ",".join(p.name for p in state.projects) or "—",
        "SUBMITTED_PROJECTS": ",".join(submitted) or "—",
    }


def _atomic_update_fields(updates: Dict[str, str], path: Path = PROG_PATH) -> Dict[str, str]:
    """治本：原子更新进度表字段（继承 v2.1 engine_helper.update_fields 治本 regex）。"""
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
    if not new_text.endswith("\n") and text.endswith("\n"):
        new_text += "\n"
    path.write_text(new_text, encoding="utf-8")
    return hits


def _write_active_projects(projects: List[ProjectState], path: Path = PROG_PATH) -> None:
    """重写多项目并进机制 v4 段下的 ### 活跃项目状态 表。"""
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    out: List[str] = []
    in_h2 = False
    in_h3 = False
    header_written = False
    for line in lines:
        if line.startswith("## ") and "多项目并进机制 v4" in line:
            in_h2 = True
            in_h3 = False
            out.append(line)
            continue
        if in_h2 and line.startswith("### ") and "活跃项目状态" in line:
            in_h3 = True
            out.append(line)
            # 写表头
            if not header_written:
                out.append("| " + " | ".join(_ACTIVE_HEADER) + " |")
                out.append("|" + "|".join(["---"] * len(_ACTIVE_HEADER)) + "|")
                header_written = True
            # 写新行
            for p in projects:
                out.append("| " + " | ".join(p.to_active_row()) + " |")
            continue
        if in_h2 and line.startswith("### ") and in_h3:
            in_h3 = False
        if in_h2 and line.startswith("## ") and "多项目并进机制 v4" not in line:
            in_h2 = False
        if in_h3 and line.startswith("|"):
            continue  # 跳过旧行
        out.append(line)
    path.write_text("\n".join(out) + "\n", encoding="utf-8")


# ─────────────────────────────────────────────────────────────────────
# ProgressTableRepo（V4 仓库模式）
# ─────────────────────────────────────────────────────────────────────
class ProgressTableRepo:
    """主进度表读写仓库。"""

    def __init__(self, path: Path = PROG_PATH) -> None:
        self.path = path

    def parse(self) -> Dict[str, Dict[str, str]]:
        return parse(self.path)

    def to_engine_state(self, project_repo) -> EngineState:
        sections = self.parse()
        code = sections.get("_codeblock", {})
        # 系统级
        wip = WipStatus(code.get("WIP_STATUS", "normal").strip())
        budget = ErrorBudgetStatus(code.get("ERROR_BUDGET_STATUS", "normal").strip())
        mode = StrategyMode(code.get("STRATEGY_MODE", "aggressive").strip())
        lock = code.get("LOCK", "false").strip() == "true"
        # 活跃项目
        active = _active_projects_to_state(self.path)
        # 注入子表数据
        for proj in active:
            sub = project_repo.try_load(proj.name)
            if sub:
                proj.fork = sub.get("FORK", proj.fork)
                proj.branch = sub.get("BRANCH", proj.branch)
                proj.current_node = sub.get("CURRENT_NODE", proj.current_node)
                proj.checkpoint = sub.get("LAST_CHECKPOINT", proj.checkpoint)
                proj.review_count = int(sub.get("PR_REVIEW_COUNT", "0") or "0")
                proj.sub_progress_path = str(project_repo.path_for(proj.name))
                if sub.get("STALLED_SINCE") and sub["STALLED_SINCE"] != "—":
                    proj.stalled_since = sub["STALLED_SINCE"]
        # 队列
        queue = _queue_to_state(self.path)
        # 指标
        metrics = SystemMetrics(
            wip_status=wip,
            error_budget=budget,
            active_prs=sum(1 for p in active if p.is_submitted),
            repos_with_pr={p.repo: 1 for p in active if p.is_submitted},
            reviews_pending=0,
            weekly_new_prs=0,
            lock=lock,
        )
        return EngineState(
            version=code.get("V4_VERSION", "4").strip(),
            strategy_mode=mode,
            projects=active,
            queue=queue,
            metrics=metrics,
            head_commit=git_ops.current_sha(),
            last_heartbeat_status=code.get("LAST_HEARTBEAT_STATUS", "unknown").strip(),
            last_heartbeat_note=code.get("LAST_HEARTBEAT_NOTE", "").strip(),
            iron_laws_version=code.get("IRON_LAWS_VERSION", "v4-1").strip(),
        )

    def write_engine_state(self, state: EngineState) -> None:
        """原子写主表：codeblock 字段 + 活跃项目表。"""
        updates = _flush_codeblock_fields(state)
        _atomic_update_fields(updates, self.path)
        _write_active_projects(state.projects, self.path)
