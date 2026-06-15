"""V5.1 PRWorktree 持久化：每个 PR 的 sub-tasks + quality 状态。

V5.1 关键：状态必须 git-tracked（每次 tick commit + push），中断后可恢复。
"""
from __future__ import annotations

import json
import os
from typing import Dict, List, Optional

from core.models import (
    PRState, PRWorktree, QualityCriteria, SubTask, SubTaskStatus, SubTaskType
)
from pr_worktree.decomposer import decompose


WORKTREE_BASE = "核聚变开源贡献系统/V5_1/WORKTREES"


def worktree_dir(pr_id: str, root: str = ".") -> str:
    return os.path.join(root, WORKTREE_BASE, pr_id)


def ensure_dir(pr_id: str, root: str = ".") -> str:
    d = worktree_dir(pr_id, root)
    os.makedirs(os.path.join(d, "notes"), exist_ok=True)
    return d


def save_state(wt: PRWorktree, root: str = ".") -> str:
    """把 PRWorktree 序列化为 state.json。"""
    d = ensure_dir(wt.pr_id, root)
    fp = os.path.join(d, "state.json")
    with open(fp, "w", encoding="utf-8") as f:
        json.dump(wt.to_dict(), f, ensure_ascii=False, indent=2)
    return fp


def load_state(pr_id: str, root: str = ".") -> Optional[PRWorktree]:
    """从 state.json 恢复。"""
    fp = os.path.join(worktree_dir(pr_id, root), "state.json")
    if not os.path.isfile(fp):
        return None
    raw = json.load(open(fp, encoding="utf-8"))
    wt = PRWorktree(
        pr_id=raw["pr_id"],
        project=raw["project"],
        paper_arxiv_id=raw["paper_arxiv_id"],
        paper_title=raw["paper_title"],
        pr_type=raw["pr_type"],
        target_repo=raw["target_repo"],
        target_files=raw["target_files"],
        state=PRState(raw["state"]),
        created_at=raw["created_at"],
        updated_at=raw["updated_at"],
        notes_dir=raw.get("notes_dir", ""),
        pr_branch=raw.get("pr_branch", ""),
        pr_number=raw.get("pr_number"),
        pr_url=raw.get("pr_url", ""),
        estimated_total_ticks=raw.get("estimated_total_ticks", 0),
    )
    # 恢复 subtasks
    for s in raw.get("subtasks", []):
        st = SubTask(
            id=s["id"],
            pr_id=s["pr_id"],
            type=SubTaskType(s["type"]),
            description=s["description"],
            depends_on=s.get("depends_on", []),
            status=SubTaskStatus(s["status"]),
            output_files=s.get("output_files", []),
            verification=s.get("verification", ""),
            estimated_ticks=s.get("estimated_ticks", 1),
            actual_ticks=s.get("actual_ticks", 0),
            started_at=s.get("started_at", ""),
            finished_at=s.get("finished_at", ""),
            notes=s.get("notes", ""),
        )
        wt.subtasks.append(st)
    # 恢复 quality
    q = raw.get("quality", {})
    wt.quality = QualityCriteria(
        all_subtasks_done=q.get("all_subtasks_done", False),
        tests_pass=q.get("tests_pass", False),
        lint_pass=q.get("lint_pass", False),
        type_check_pass=q.get("type_check_pass", False),
        self_critique_pass=q.get("self_critique_pass", False),
        paper_cited=q.get("paper_cited", False),
        pr_body_complete=q.get("pr_body_complete", False),
        human_approved=q.get("human_approved", False),
    )
    return wt


def init_worktree(pr_id: str, project: str, paper_id: str, paper_title: str,
                  pr_type: str, target_repo: str, target_files: List[str],
                  test_dir: str = "tests", root: str = ".") -> PRWorktree:
    """初始化 1 个新 PRWorktree（含 sub-task DAG）。"""
    from datetime import datetime
    wt = PRWorktree(
        pr_id=pr_id,
        project=project,
        paper_arxiv_id=paper_id,
        paper_title=paper_title,
        pr_type=pr_type,
        target_repo=target_repo,
        target_files=target_files,
        state=PRState.DECOMPOSING,
        created_at=datetime.utcnow().isoformat() + "Z",
        updated_at=datetime.utcnow().isoformat() + "Z",
        notes_dir=f"{WORKTREE_BASE}/{pr_id}/notes",
        pr_branch=f"pr/{project}/{paper_id}",
    )
    # 分解 sub-tasks
    wt.subtasks = decompose(pr_id, pr_type, paper_id, project, target_files, test_dir)
    wt.estimated_total_ticks = sum(s.estimated_ticks for s in wt.subtasks)
    # 进入 accumulating
    wt.state = PRState.ACCUMULATING
    wt.updated_at = datetime.utcnow().isoformat() + "Z"
    # 持久化
    save_state(wt, root)
    return wt


def list_all_worktrees(root: str = ".") -> List[PRWorktree]:
    """列出所有 WORKTREES/ 下的 state.json。"""
    base = os.path.join(root, WORKTREE_BASE)
    if not os.path.isdir(base):
        return []
    out: List[PRWorktree] = []
    for entry in sorted(os.listdir(base)):
        wt = load_state(entry, root)
        if wt:
            out.append(wt)
    return out
