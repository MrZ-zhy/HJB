"""V5.2 CLI 入口。

V5.2 相对 V5.1 的新命令：
  tick           执行 1 个 V5.2 tick（核心，密度由 --density 控制）
  init           初始化 1 个 PRWorktree（含 sub-task DAG + 迭代参数）
  worktrees      列出所有 PRWorktree + 进度
  progress       打印全局进度
  validate       状态机自检
  promote        人类 gate：标记 human_approved=true
  ───────────── V5.2 新增 ─────────────
  refine <id>    手动指定 1 个 sub-task 强制细化（不走调度）
  density        查看/修改当前 compute_density
  subtask-list   列出 1 个 PRWorktree 的全部 sub-tasks + quality
  show <id>      显示 1 个 PRWorktree 的 quality 详情
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

# 让 `python3 v5_2/engine.py` 能 import 兄弟模块
_PKG_PARENT = Path(__file__).resolve().parent.parent
if str(_PKG_PARENT) not in sys.path:
    sys.path.insert(0, str(_PKG_PARENT))


# V5.2 持久化：当前 density 写到一个本地文件
_DENSITY_FILE = ".v5_2_density"


def _read_density() -> str:
    if os.path.isfile(_DENSITY_FILE):
        try:
            return open(_DENSITY_FILE).read().strip()
        except Exception:
            pass
    return "default"


def _write_density(d: str) -> None:
    with open(_DENSITY_FILE, "w") as f:
        f.write(d)


def cmd_tick(args: argparse.Namespace) -> int:
    from core.orchestrator import Orchestrator
    density = args.density or _read_density()
    orch = Orchestrator(density=density)
    result = orch.tick(dry_run=args.dry_run)
    out = {
        "version": "5.2",
        "density": density,
        "started_at": orch.started_at,
        "finished_at": datetime.utcnow().isoformat() + "Z",
        "overall_ok": result.overall_ok,
        "steps": result.steps,
        "actions_taken": result.actions_taken,
        "iterations_completed": result.iterations_completed,
        "iterations_refining": result.iterations_refining,
        "iterations_failed": result.iterations_failed,
        "events": result.events,
        "next_action_hint": result.next_action_hint,
    }
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0 if result.overall_ok else 1


def cmd_init(args: argparse.Namespace) -> int:
    """初始化 1 个新 V5.2 PRWorktree。"""
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
    # 计算 V5.2 元信息
    total_max_iter = sum(s.max_iterations for s in wt.subtasks)
    print(json.dumps({
        "ok": True,
        "version": "5.2",
        "pr_id": wt.pr_id,
        "subtasks_count": len(wt.subtasks),
        "estimated_total_ticks": wt.estimated_total_ticks,
        "total_max_iterations": total_max_iter,
        "state": wt.state.value,
        "first_subtask_id": wt.subtasks[0].id if wt.subtasks else None,
    }, ensure_ascii=False, indent=2))
    return 0


def cmd_worktrees(_: argparse.Namespace) -> int:
    from core.compute_budget import density_description
    from core.quality_gate import quality_summary
    from persistence.worktree_state import list_all_worktrees
    wts = list_all_worktrees()
    out = []
    for wt in wts:
        summary = quality_summary(wt)
        out.append({
            "pr_id": wt.pr_id,
            "project": wt.project,
            "paper_arxiv_id": wt.paper_arxiv_id,
            "pr_type": wt.pr_type,
            "state": wt.state.value,
            "subtasks": f"{summary['subtasks_done']}/{summary['subtasks_total']}",
            "refining": summary["subtasks_refining"],
            "failed": summary["subtasks_failed"],
            "total_iterations": summary["total_iterations"],
            "avg_quality": round(summary["avg_quality"], 1),
            "min_quality": round(summary["min_quality"], 1),
            "max_quality": round(summary["max_quality"], 1),
            "density": wt.compute_density,
            "is_ready_to_submit": summary["is_ready_to_submit"],
        })
    print(json.dumps({
        "worktrees": out,
        "count": len(out),
        "current_density": _read_density(),
        "density_description": density_description(_read_density()),
    }, ensure_ascii=False, indent=2))
    return 0


def cmd_progress(_: argparse.Namespace) -> int:
    from core.models import SubTaskStatus
    from persistence.worktree_state import list_all_worktrees
    wts = list_all_worktrees()
    by_state = {}
    total_done = 0
    total_all = 0
    total_iters = 0
    for wt in wts:
        by_state.setdefault(wt.state.value, 0)
        by_state[wt.state.value] += 1
        for s in wt.subtasks:
            total_all += 1
            if s.status == SubTaskStatus.DONE:
                total_done += 1
            total_iters += s.iterations_done
    print(json.dumps({
        "version": "5.2",
        "worktree_count": len(wts),
        "by_state": by_state,
        "subtask_progress": f"{total_done}/{total_all}",
        "pct": f"{total_done/total_all*100:.1f}%" if total_all else "0%",
        "total_iterations": total_iters,
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


# ──────────────────────────────────────────────────────────────
# V5.2 新增命令
# ──────────────────────────────────────────────────────────────

def cmd_refine(args: argparse.Namespace) -> int:
    """手动细化 1 个 sub-task（不走调度，强制执行 1 次 iteration）。"""
    from core.models import SubTaskStatus
    from core.orchestrator import DEFAULT_PROJECTS_META
    from persistence.worktree_state import load_state, save_state
    from pr_worktree.executor import execute_subtask_iteration

    wt = load_state(args.pr_id)
    if not wt:
        print(json.dumps({"error": f"worktree {args.pr_id} 不存在"}, ensure_ascii=False, indent=2))
        return 1
    st = wt.subtask_by_id(args.task_id)
    if not st:
        print(json.dumps({"error": f"sub-task {args.task_id} 不存在"}, ensure_ascii=False, indent=2))
        return 1
    if st.status == SubTaskStatus.DONE:
        print(json.dumps({"error": f"sub-task {args.task_id} 已 DONE，无需细化"}, ensure_ascii=False, indent=2))
        return 1
    if st.iterations_done >= st.max_iterations:
        print(json.dumps({
            "error": f"sub-task {args.task_id} 已达 max_iter={st.max_iterations}，无法再细化",
            "current_quality": st.quality_score,
            "threshold": st.quality_threshold,
        }, ensure_ascii=False, indent=2))
        return 1

    meta = DEFAULT_PROJECTS_META.get(wt.project, {})
    ctx = {
        "paper_id": wt.paper_arxiv_id,
        "project": wt.project,
        "project_path": meta.get("local", ""),
        "target_files": wt.target_files,
        "notes_dir": wt.notes_dir,
        "worktree": wt,
    }
    ok, quality, msg = execute_subtask_iteration(st, ctx)
    save_state(wt)
    print(json.dumps({
        "version": "5.2",
        "pr_id": args.pr_id,
        "task_id": args.task_id,
        "iteration": st.iterations_done,
        "max_iterations": st.max_iterations,
        "quality_score": round(quality, 2),
        "quality_threshold": st.quality_threshold,
        "status": st.status.value,
        "result": msg,
        "history_count": len(st.refinement_history),
    }, ensure_ascii=False, indent=2))
    return 0 if ok or "REFINING" in msg else 1


def cmd_density(args: argparse.Namespace) -> int:
    """查看或修改当前 compute_density。"""
    from core.compute_budget import density_description, sub_tasks_per_tick, effective_max_iterations
    from core.models import DEFAULT_PARAMS

    current = _read_density()
    if args.set:
        if args.set not in ["quick", "default", "deep", "burst"]:
            print(json.dumps({"error": f"未知 density: {args.set}；可选 quick/default/deep/burst"},
                             ensure_ascii=False, indent=2))
            return 1
        _write_density(args.set)
        current = args.set

    # 列出每种 SubTaskType 在当前 density 下的 effective max_iterations
    sample_types = ["read_paper", "analyze_code", "write_test", "self_critique"]
    eff = {t: effective_max_iterations(t, current) for t in sample_types}
    print(json.dumps({
        "version": "5.2",
        "current_density": current,
        "description": density_description(current),
        "sub_tasks_per_tick": sub_tasks_per_tick(current),
        "effective_max_iterations_for_sample_types": eff,
        "default_params": DEFAULT_PARAMS,
    }, ensure_ascii=False, indent=2))
    return 0


def cmd_subtask_list(args: argparse.Namespace) -> int:
    """列出 1 个 PRWorktree 的全部 sub-tasks + quality。"""
    from persistence.worktree_state import load_state
    wt = load_state(args.pr_id)
    if not wt:
        print(json.dumps({"error": f"worktree {args.pr_id} 不存在"}, ensure_ascii=False, indent=2))
        return 1
    out = []
    for s in wt.subtasks:
        out.append({
            "id": s.id,
            "type": s.type.value,
            "status": s.status.value,
            "iterations_done": s.iterations_done,
            "max_iterations": s.max_iterations,
            "quality_score": round(s.quality_score, 2),
            "quality_threshold": s.quality_threshold,
            "needs_refinement": s.needs_refinement(),
            "depends_on": s.depends_on,
            "verify_prompt": s.verify_prompt,
            "refinement_count": len(s.refinement_history),
        })
    print(json.dumps({
        "version": "5.2",
        "pr_id": args.pr_id,
        "project": wt.project,
        "state": wt.state.value,
        "subtasks": out,
    }, ensure_ascii=False, indent=2))
    return 0


def cmd_show(args: argparse.Namespace) -> int:
    """显示 1 个 PRWorktree 的 quality 详情。"""
    from core.quality_gate import checklist_text, quality_summary
    from persistence.worktree_state import load_state
    wt = load_state(args.pr_id)
    if not wt:
        print(json.dumps({"error": f"worktree {args.pr_id} 不存在"}, ensure_ascii=False, indent=2))
        return 1
    summary = quality_summary(wt)
    print(json.dumps({
        "version": "5.2",
        "pr_id": wt.pr_id,
        "project": wt.project,
        "paper_arxiv_id": wt.paper_arxiv_id,
        "pr_type": wt.pr_type,
        "state": wt.state.value,
        "quality_summary": summary,
        "checklist_text": checklist_text(wt.quality),
        "checklist": [{"name": n, "ok": ok} for n, ok in wt.quality.checklist()],
    }, ensure_ascii=False, indent=2))
    return 0


def main(argv: list = None) -> int:
    ap = argparse.ArgumentParser(description="V5.2 迭代深化 PR 引擎")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_tick = sub.add_parser("tick", help="执行 1 个 V5.2 tick（核心）")
    p_tick.add_argument("--dry-run", action="store_true")
    p_tick.add_argument("--density", choices=["quick", "default", "deep", "burst"],
                        help="覆盖当前 compute_density（不修改持久化）")
    p_tick.set_defaults(func=cmd_tick)

    p_init = sub.add_parser("init", help="初始化 1 个 V5.2 PRWorktree")
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

    # V5.2 新增
    p_refine = sub.add_parser("refine", help="手动细化 1 个 sub-task（强制 1 次 iteration）")
    p_refine.add_argument("pr_id")
    p_refine.add_argument("task_id")
    p_refine.set_defaults(func=cmd_refine)

    p_density = sub.add_parser("density", help="查看/修改 compute_density")
    p_density.add_argument("--set", choices=["quick", "default", "deep", "burst"],
                           help="设置新 density（写入 .v5_2_density）")
    p_density.set_defaults(func=cmd_density)

    p_sl = sub.add_parser("subtask-list", help="列出 1 个 PRWorktree 的全部 sub-tasks + quality")
    p_sl.add_argument("pr_id")
    p_sl.set_defaults(func=cmd_subtask_list)

    p_show = sub.add_parser("show", help="显示 1 个 PRWorktree 的 quality 详情")
    p_show.add_argument("pr_id")
    p_show.set_defaults(func=cmd_show)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
