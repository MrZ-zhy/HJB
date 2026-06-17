"""V5.2 PRWorktree 持久化：每个 PR 的 sub-tasks + quality 状态 + 迭代历史。

V5.2 升级（核心）：
  - 持久化 refinement_history（每次 iteration 详细记录）
  - 持久化 quality_score / iterations_done / max_iterations / quality_threshold / verify_prompt
  - 持久化 compute_density（PRWorktree 级别）
  - 恢复时全部读回，下次 tick 可继续深化（**中断恢复 V5.2 核心**）

V5.2 first-principles 修复：WORKTREE_BASE 改为**绝对路径**（从 __file__ 计算），
不再依赖 cwd。V5.1 之前用 cwd-relative 路径，导致 engine 在 `/workspace/` 跑
能找到 worktree 但 import 失败；或在 `v5_2/` 跑能 import 但找不到 worktree。
统一改成绝对路径后，cwd 不再是隐性依赖。
"""
from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from core.models import (
    PRState, PRWorktree, QualityCriteria, RefinementRecord, SubTask, SubTaskStatus, SubTaskType
)
from pr_worktree.decomposer import decompose


# V5.2 first-principles 修复：自动定位 HJB git 仓库根（与 orchestrator.py 同步）
# 旧版用 `Path(__file__).resolve().parent × 6` 硬编码层数，在不同部署位置会错：
#   - /workspace/核聚变开源贡献系统/.../persistence/worktree_state.py  → 6 层 = /workspace/ → WORKTREES 在 /workspace/... ✗
#   - /workspace/HJB/HJB/核聚变开源贡献系统/.../persistence/worktree_state.py → 6 层 = /workspace/HJB/ → WORKTREES 在 /workspace/HJB/... ✓
# 修法：从 __file__ 上溯找最近包含 .git/ 的目录；找不到再 fallback。
def _find_hjb_repo(start: Path) -> "Path | None":
    cur = start.resolve()
    for _ in range(10):
        if (cur / ".git").exists():
            return cur
        if cur.parent == cur:
            return None
        cur = cur.parent
    return None

_hjb_auto = _find_hjb_repo(Path(__file__))
_HJB_REPO = (
    __import__("os").environ.get("HJB_REPO_ROOT")
    or (str(_hjb_auto) if _hjb_auto else None)
    or str(Path(__file__).resolve().parent.parent.parent.parent.parent.parent / "HJB")
)
# WORKTREES 必须在 HJB git 仓库里（被 git tracked & pushed）
WORKTREE_BASE = str(Path(_HJB_REPO) / "核聚变开源贡献系统" / "V5_2" / "WORKTREES")
# 兼容旧调用：_REPO_ROOT 仍然导出
_REPO_ROOT = str(Path(_HJB_REPO).parent)


def worktree_dir(pr_id: str, root: str = ".") -> str:
    # WORKTREE_BASE 已是绝对路径，os.path.join 在绝对路径前会丢弃 root
    return os.path.join(root, WORKTREE_BASE, pr_id)


def ensure_dir(pr_id: str, root: str = ".") -> str:
    d = worktree_dir(pr_id, root)
    os.makedirs(os.path.join(d, "notes"), exist_ok=True)
    return d


def save_state(wt: PRWorktree, root: str = ".") -> str:
    """把 PRWorktree 序列化为 state.json（V5.2：含全部迭代字段）。"""
    d = ensure_dir(wt.pr_id, root)
    fp = os.path.join(d, "state.json")
    with open(fp, "w", encoding="utf-8") as f:
        json.dump(wt.to_dict(), f, ensure_ascii=False, indent=2)
    return fp


def load_state(pr_id: str, root: str = ".") -> Optional[PRWorktree]:
    """从 state.json 恢复（V5.2：含全部迭代字段）。"""
    fp = os.path.join(worktree_dir(pr_id, root), "state.json")
    if not os.path.isfile(fp):
        return None
    raw = json.load(open(fp, encoding="utf-8"))
    # V5.2 first-principles 修复：把旧版 relative notes_dir 升级为绝对路径
    # （V5.1 之前写出的 notes_dir 形如 "核聚变开源贡献系统/V5_2/WORKTREES/..."，
    #   在新 cwd 下会找不到；统一为绝对路径后，cwd 无关）
    # 用 _HJB_REPO 拼（不是 _REPO_ROOT，避免路径少一层 /HJB）
    _notes_dir = raw.get("notes_dir", "")
    if _notes_dir and not os.path.isabs(_notes_dir):
        _notes_dir = str(Path(_HJB_REPO) / _notes_dir)
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
        notes_dir=_notes_dir,
        pr_branch=raw.get("pr_branch", ""),
        pr_number=raw.get("pr_number"),
        pr_url=raw.get("pr_url", ""),
        estimated_total_ticks=raw.get("estimated_total_ticks", 0),
        compute_density=raw.get("compute_density", "default"),
    )
    # 恢复 subtasks（含 V5.2 字段）
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
            # V5.2 字段
            max_iterations=s.get("max_iterations", 1),
            iterations_done=s.get("iterations_done", 0),
            quality_score=s.get("quality_score", 0.0),
            quality_threshold=s.get("quality_threshold", 70.0),
            verify_prompt=s.get("verify_prompt", ""),
        )
        # 恢复 refinement_history
        for rec in s.get("refinement_history", []):
            st.refinement_history.append(RefinementRecord(
                iteration=rec.get("iteration", 0),
                started_at=rec.get("started_at", ""),
                finished_at=rec.get("finished_at", ""),
                output_files_written=rec.get("output_files_written", []),
                output_summary=rec.get("output_summary", ""),
                quality_score=rec.get("quality_score", 0.0),
                compute_used=rec.get("compute_used", ""),
                notes=rec.get("notes", ""),
            ))
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
        avg_subtask_quality=q.get("avg_subtask_quality", 0.0),
        min_subtask_quality=q.get("min_subtask_quality", 0.0),
    )
    return wt


def init_worktree(pr_id: str, project: str, paper_id: str, paper_title: str,
                  pr_type: str, target_repo: str, target_files: List[str],
                  test_dir: str = "tests", root: str = ".") -> PRWorktree:
    """初始化 1 个新 V5.2 PRWorktree（含 sub-task DAG + 迭代参数）。"""
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
        # V5.2 first-principles 修复：notes_dir 改为绝对路径（用 HJB repo 拼），
        # 不再是 cwd-relative，否则 executor 在不同 cwd 下会写到错的位置
        notes_dir=str(Path(_HJB_REPO) / "核聚变开源贡献系统" / "V5_2" / "WORKTREES" / pr_id / "notes"),
        pr_branch=f"pr/{project}/{paper_id}",
        compute_density="default",
    )
    # 分解 sub-tasks（V5.2 decomposer 自动注入 max_iterations / quality_threshold）
    wt.subtasks = decompose(pr_id, pr_type, paper_id, project, target_files, test_dir)
    wt.estimated_total_ticks = sum(s.estimated_ticks for s in wt.subtasks)
    wt.state = PRState.ACCUMULATING
    wt.updated_at = datetime.utcnow().isoformat() + "Z"
    save_state(wt, root)
    return wt


def list_all_worktrees(root: str = ".") -> List[PRWorktree]:
    """列出所有 V5_2/WORKTREES/ 下的 state.json。"""
    base = os.path.join(root, WORKTREE_BASE)
    if not os.path.isdir(base):
        return []
    out: List[PRWorktree] = []
    for entry in sorted(os.listdir(base)):
        wt = load_state(entry, root)
        if wt:
            out.append(wt)
    return out
