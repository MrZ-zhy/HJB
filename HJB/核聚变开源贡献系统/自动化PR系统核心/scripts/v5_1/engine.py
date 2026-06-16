"""V5.1 CLI 入口。

子命令：
  tick          执行 1 个 V5.1 tick（核心）
  init <pr_id>  初始化 1 个 PRWorktree（含 sub-task DAG）
  worktrees     列出所有 PRWorktree + 进度
  progress      打印全局进度
  validate      状态机自检
  promote <id>  人类 gate：标记 human_approved=true
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

# 让 `python3 v5_1/engine.py` 能 import 兄弟模块
_PKG_PARENT = Path(__file__).resolve().parent.parent
if str(_PKG_PARENT) not in sys.path:
    sys.path.insert(0, str(_PKG_PARENT))


def cmd_tick(args: argparse.Namespace) -> int:
    from core.orchestrator import Orchestrator
    orch = Orchestrator()
    result = orch.tick(dry_run=args.dry_run)
    out = {
        "version": "5.1",
        "started_at": orch.started_at,
        "finished_at": __import__("datetime").datetime.utcnow().isoformat() + "Z",
        "overall_ok": result.overall_ok,
        "steps": result.steps,
        "actions_taken": result.actions_taken,
        "subtasks_completed": result.subtasks_completed,
        "subtasks_failed": result.subtasks_failed,
        "events": result.events,
        "next_action_hint": result.next_action_hint,
    }
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0 if result.overall_ok else 1


def cmd_init(args: argparse.Namespace) -> int:
    """初始化 1 个新 PRWorktree。"""
    from core.orchestrator import DEFAULT_PROJECTS_META
    from persistence.worktree_state import init_worktree
    meta = DEFAULT_PROJECTS_META.get(args.project)
    if not meta:
        print(json.dumps({"error": f"未知项目: {args.project}"}, ensure_ascii=False, indent=2))
        return 1
    wt = init_worktree(
        pr_id=args.pr_id,
        project=args.project,
        paper_id=args.paper_id,
        paper_title=args.paper_title,
        pr_type=args.pr_type,
        target_repo=meta["repo"],
        target_files=args.target_files or [],
        test_dir=meta.get("test_dir", "tests"),
    )
    print(json.dumps({
        "ok": True,
        "pr_id": wt.pr_id,
        "subtasks_count": len(wt.subtasks),
        "estimated_total_ticks": wt.estimated_total_ticks,
        "state": wt.state.value,
    }, ensure_ascii=False, indent=2))
    return 0


def cmd_worktrees(_: argparse.Namespace) -> int:
    from persistence.worktree_state import list_all_worktrees
    wts = list_all_worktrees()
    out = []
    for wt in wts:
        done, total, pct = wt.progress()
        out.append({
            "pr_id": wt.pr_id,
            "project": wt.project,
            "paper_arxiv_id": wt.paper_arxiv_id,
            "pr_type": wt.pr_type,
            "state": wt.state.value,
            "subtasks": f"{done}/{total} ({pct*100:.0f}%)",
            "estimated_total_ticks": wt.estimated_total_ticks,
            "quality_pass": sum(1 for _, ok in wt.quality.checklist() if ok),
            "quality_total": len(wt.quality.checklist()),
            "human_approved": wt.quality.human_approved,
        })
    print(json.dumps({"worktrees": out, "count": len(out)}, ensure_ascii=False, indent=2))
    return 0


def cmd_progress(_: argparse.Namespace) -> int:
    from persistence.worktree_state import list_all_worktrees
    from core.models import PRState, SubTaskStatus
    wts = list_all_worktrees()
    by_state = {}
    total_done = 0
    total_all = 0
    for wt in wts:
        by_state.setdefault(wt.state.value, 0)
        by_state[wt.state.value] += 1
        for s in wt.subtasks:
            total_all += 1
            if s.status == SubTaskStatus.DONE:
                total_done += 1
    print(json.dumps({
        "worktree_count": len(wts),
        "by_state": by_state,
        "subtask_progress": f"{total_done}/{total_all}",
        "pct": f"{total_done/total_all*100:.1f}%" if total_all else "0%",
    }, ensure_ascii=False, indent=2))
    return 0


def cmd_validate(_: argparse.Namespace) -> int:
    from core.state_machine import self_check
    ok, msg = self_check()
    print(json.dumps({"ok": ok, "msg": msg}, ensure_ascii=False, indent=2))
    return 0 if ok else 1


def cmd_promote(args: argparse.Namespace) -> int:
    """人类 gate：标记 human_approved=true。"""
    from persistence.worktree_state import load_state, save_state
    wt = load_state(args.pr_id)
    if not wt:
        print(json.dumps({"error": f"worktree {args.pr_id} 不存在"}, ensure_ascii=False, indent=2))
        return 1
    wt.quality.human_approved = True
    save_state(wt)
    print(json.dumps({
        "ok": True,
        "pr_id": wt.pr_id,
        "human_approved": True,
        "now_ready": wt.quality.is_ready_to_submit(),
    }, ensure_ascii=False, indent=2))
    return 0


def main(argv: list = None) -> int:
    ap = argparse.ArgumentParser(description="V5.1 积累型 PR 引擎")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_tick = sub.add_parser("tick", help="执行 1 个 V5.1 tick")
    p_tick.add_argument("--dry-run", action="store_true")
    p_tick.set_defaults(func=cmd_tick)

    p_init = sub.add_parser("init", help="初始化 1 个 PRWorktree")
    p_init.add_argument("pr_id")
    p_init.add_argument("--project", required=True)
    p_init.add_argument("--paper-id", required=True)
    p_init.add_argument("--paper-title", required=True)
    p_init.add_argument("--pr-type", required=True, choices=["T1", "T2", "T5"])
    p_init.add_argument("--target-files", nargs="+", default=[])
    p_init.set_defaults(func=cmd_init)

    sub.add_parser("worktrees", help="列出所有 PRWorktree + 进度").set_defaults(func=cmd_worktrees)
    sub.add_parser("progress", help="打印全局进度").set_defaults(func=cmd_progress)
    sub.add_parser("validate", help="状态机自检").set_defaults(func=cmd_validate)

    p_promote = sub.add_parser("promote", help="人类 gate：标记 human_approved=true")
    p_promote.add_argument("pr_id")
    p_promote.set_defaults(func=cmd_promote)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
