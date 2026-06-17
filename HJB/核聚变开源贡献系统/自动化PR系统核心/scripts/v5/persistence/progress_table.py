"""V5 进度表持久化（读 进度表.md + 项目/<name>/进度表.md → EngineState）。"""
from __future__ import annotations

import os
import re
import subprocess
from datetime import datetime
from typing import Dict, List, Optional

from core.models import EngineState, Paper, Project, ProjectState


# 进度表路径（相对 HJB 仓库根）
ROOT = "核聚变开源贡献系统"
MAIN_TABLE = f"{ROOT}/进度表.md"
PROJECT_TABLE_DIR = f"{ROOT}/项目"
PAPER_LOG = f"{ROOT}/V5/PAPER_LOG/papers_2024plus.json"


def _read_file(path: str) -> str:
    if not os.path.isfile(path):
        return ""
    with open(path, encoding="utf-8", errors="ignore") as f:
        return f.read()


def _head_commit() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"], text=True
        ).strip()
    except Exception:
        return ""


def load_state(project_names: List[str], projects_meta: List[dict], local_paths: Dict[str, str]) -> EngineState:
    """从主表 + 子表 + paper log 加载 typed EngineState。

    projects_meta: [{"name", "repo", "language", "keywords", "notes"}, ...]
    local_paths: {"OpenReactor": "/workspace/HJB/项目/OpenReactor", ...}
    """
    main_md = _read_file(MAIN_TABLE)
    state = EngineState(
        version="5",
        timestamp=datetime.utcnow().isoformat() + "Z",
        head_commit=_head_commit(),
    )

    # 主表 strategy_mode / queue
    for line in main_md.splitlines():
        if line.startswith("STRATEGY_MODE:"):
            state.strategy_mode = line.split(":", 1)[1].strip()
        if line.startswith("ACTIVE_PROJECTS:"):
            for name in line.split(":", 1)[1].strip().split(","):
                name = name.strip()
                if name:
                    state.queue.append(name)
        if line.startswith("HEAD_COMMIT_SHA:"):
            pass  # 已被 _head_commit 覆盖
        if line.startswith("LAST_HEARTBEAT:"):
            state.last_heartbeat = line.split(":", 1)[1].strip()
        if line.startswith("LAST_HEARTBEAT_NOTE:"):
            state.last_heartbeat_note = line.split(":", 1)[1].strip()
        if line.startswith("LAST_HEARTBEAT_STATUS:"):
            state.last_heartbeat_status = line.split(":", 1)[1].strip()

    # V5 强制保守模式（质量优先策略，P10 铁律）
    if state.strategy_mode != "conservative":
        state.strategy_mode = "conservative"

    # 加载项目
    for meta in projects_meta:
        name = meta["name"]
        proj = Project(
            name=name,
            repo=meta["repo"],
            language=meta.get("language", "Unknown"),
            sandbox_runnable=meta.get("sandbox_runnable", False),
            local_path=local_paths.get(name, ""),
            keywords=meta.get("keywords", []),
            notes=meta.get("notes", ""),
        )
        # 子表读 PROJECT_STATE
        sub_path = f"{PROJECT_TABLE_DIR}/{name}/进度表.md"
        sub_md = _read_file(sub_path)
        for line in sub_md.splitlines():
            if "PROJECT_STATE:" in line:
                # 解析 `PROJECT_STATE: BACKLOG`
                try:
                    proj.state = ProjectState(line.split(":", 1)[1].strip().lower())
                except ValueError:
                    proj.state = ProjectState.BACKLOG
                break
        state.projects.append(proj)

    # 加载 paper log
    if os.path.isfile(PAPER_LOG):
        import json
        raw = json.load(open(PAPER_LOG, encoding="utf-8"))
        for proj_name, papers in raw.items():
            for p in papers[:6]:
                if "error" in p:
                    continue
                state.papers.append(Paper(
                    arxiv_id=p["id"].rsplit("/", 1)[-1].split("v")[0],
                    title=p["title"],
                    authors=p["authors"],
                    year=p["year"],
                    summary=p.get("summary", ""),
                    primary_category=p.get("primary_category", ""),
                    pdf_url=p.get("pdf_url", ""),
                    matched_projects=[proj_name],
                ))

    return state
