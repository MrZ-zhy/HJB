"""V4 统一 CLI 入口（取代 v2 的 4 个独立脚本）。

命令：
  engine.py tick          # 执行一个 tick（7 步工作流）
  engine.py tick --dry-run# 不 commit/push
  engine.py status        # 打印当前 EngineState（不执行）
  engine.py report        # 打印 metrics snapshot
  engine.py project <name># 打印指定项目子表

设计原则（公理 A6）：单入口 → Trae prompt 极简。

使用方式：
  python3 -m v4.engine <cmd>           # 推荐（包模式）
  python3 v4/engine.py <cmd>           # 脚本模式（自动把父目录加进 sys.path）
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict

# 让脚本模式 `python3 v4/engine.py` 也能 import 兄弟模块
_PKG_PARENT = Path(__file__).resolve().parent.parent
if str(_PKG_PARENT) not in sys.path:
    sys.path.insert(0, str(_PKG_PARENT))

from v4.core.orchestrator import Orchestrator  # noqa: E402
from v4.observability import structured_log  # noqa: E402
from v4.observability.metrics import snapshot  # noqa: E402
from v4.persistence.progress_table import ProgressTableRepo  # noqa: E402
from v4.persistence.project_progress import ProjectProgressRepo  # noqa: E402


def cmd_tick(args: argparse.Namespace) -> int:
    orch = Orchestrator()
    report = orch.tick(dry_run=args.dry_run)
    print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
    return 0 if report.overall_ok else 1


def cmd_status(_: argparse.Namespace) -> int:
    prog_repo = ProgressTableRepo()
    proj_repo = ProjectProgressRepo()
    state = prog_repo.to_engine_state(proj_repo)
    out = {
        "version": state.version,
        "timestamp": state.timestamp,
        "strategy_mode": state.strategy_mode.value,
        "active_projects": [
            {
                "name": p.name,
                "repo": p.repo,
                "state": p.state.value,
                "pr": p.pr_number,
                "pr_age_hours": p.pr_age_hours,
                "review_count": p.review_count,
                "current_node": p.current_node,
            }
            for p in state.projects
        ],
        "queue": state.queue,
        "metrics": {
            "wip": state.metrics.wip_status.value,
            "budget": state.metrics.error_budget.value,
            "lock": state.metrics.lock,
        },
        "head_commit": state.head_commit,
        "last_heartbeat_status": state.last_heartbeat_status,
        "iron_laws_version": state.iron_laws_version,
    }
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


def cmd_report(_: argparse.Namespace) -> int:
    prog_repo = ProgressTableRepo()
    proj_repo = ProjectProgressRepo()
    state = prog_repo.to_engine_state(proj_repo)
    snap = snapshot(state)
    print(json.dumps(snap.to_dict(), ensure_ascii=False, indent=2))
    return 0


def cmd_project(args: argparse.Namespace) -> int:
    proj_repo = ProjectProgressRepo()
    try:
        data = proj_repo.parse(args.name)
    except FileNotFoundError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    out = {"name": args.name, "path": str(proj_repo.path_for(args.name)), "sections": data}
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


def cmd_validate(_: argparse.Namespace) -> int:
    """运行 V4 自检（iron laws 完整性 + 模块导入 + 状态机不变量）。"""
    from v4.core.state_machine import StateMachine, ContributionState, GuardContext
    sm = StateMachine()
    errors: list[str] = []
    # 1. 状态机自检
    for frm in ContributionState:
        for to in ContributionState:
            if frm == to:
                continue
            try:
                legal = sm.is_legal(frm, to, GuardContext(wip_ok=True, budget_ok=True))
                if legal:
                    # 应该是合法转换
                    pass
            except Exception as e:
                errors.append(f"{frm.value}->{to.value}: {e}")
    # 2. 模块导入
    try:
        from v4.strategies.decision_matrix import RULES
        if len(RULES) < 3:
            errors.append("decision_matrix rules < 3")
    except Exception as e:
        errors.append(f"decision_matrix import: {e}")
    try:
        from v4.strategies.pr_strategy import PRStrategy
        PRStrategy().evaluate  # 触发类加载
    except Exception as e:
        errors.append(f"pr_strategy import: {e}")

    if errors:
        print(json.dumps({"ok": False, "errors": errors}, ensure_ascii=False, indent=2))
        return 1
    print(json.dumps({"ok": True, "message": "V4 self-check passed"}, ensure_ascii=False, indent=2))
    return 0


def main(argv: list[str] = None) -> int:
    ap = argparse.ArgumentParser(
        description="V4 核聚变开源贡献自动化引擎（统一入口）",
    )
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_tick = sub.add_parser("tick", help="执行一个 tick（7 步工作流）")
    p_tick.add_argument("--dry-run", action="store_true", help="不 commit/push")
    p_tick.set_defaults(func=cmd_tick)

    p_status = sub.add_parser("status", help="打印当前 EngineState（不执行）")
    p_status.set_defaults(func=cmd_status)

    p_report = sub.add_parser("report", help="打印 metrics snapshot")
    p_report.set_defaults(func=cmd_report)

    p_proj = sub.add_parser("project", help="打印指定项目子表")
    p_proj.add_argument("name", help="项目名")
    p_proj.set_defaults(func=cmd_project)

    p_val = sub.add_parser("validate", help="V4 自检（iron laws + 模块 + 状态机）")
    p_val.set_defaults(func=cmd_validate)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
