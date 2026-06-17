"""V4 编排器（7 步工作流）。

7 步：
  Step 1: ENV_PREPARE   - git pull --rebase + token
  Step 2: PREFLIGHT     - 5 项健康检查
  Step 3: LOAD_STATE    - parse 主表 + 子表
  Step 4: STATE_DECIDE  - strategies evaluate
  Step 5: EXECUTE       - 调 action
  Step 6: PERSIST       - 原子写 + commit + push
  Step 7: REPORT        - 输出 JSON 报告

公理 A1：一次 tick = 一次原子 commit。
公理 A3：失败可隔离（per-strategy try/except）。
"""
from __future__ import annotations

import json
import os
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from .event_bus import Event, EventBus, Events
from .models import (
    Action,
    EngineState,
    StepResult,
    StrategyMode,
    TickReport,
)
from .state_machine import GuardContext, IllegalTransition, StateMachine


REPO_ROOT = Path(os.environ.get("HJB_ROOT", "/workspace/HJB/HJB"))
PROG_PATH = REPO_ROOT / "核聚变开源贡献系统" / "进度表.md"
BRANCH = os.environ.get("HJB_BRANCH", "trae/solo-agent-TbCBsF")
V4_ROOT = Path(__file__).resolve().parent.parent  # scripts/v4/


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _run_git(args: List[str], cwd: Path = REPO_ROOT) -> subprocess.CompletedProcess:
    return subprocess.run(["git"] + args, cwd=cwd, capture_output=True, text=True)


# ─────────────────────────────────────────────────────────────────────
# Step 实现
# ─────────────────────────────────────────────────────────────────────
def step_env_prepare(bus: EventBus) -> StepResult:
    t0 = time.time()
    try:
        if not (REPO_ROOT / ".git").exists():
            return StepResult(
                step="env_prepare", ok=False, elapsed_ms=int((time.time()-t0)*1000),
                error=f"not a git repo: {REPO_ROOT}"
            )
        # 拉取
        _run_git(["fetch", "origin"])
        # 工作区干净？
        porcelain = _run_git(["status", "--porcelain"]).stdout.strip()
        if porcelain:
            # 不失败，但记录
            pass
        # HEAD sha
        head = _run_git(["rev-parse", "--short", "HEAD"]).stdout.strip()
        return StepResult(
            step="env_prepare", ok=True, elapsed_ms=int((time.time()-t0)*1000),
            payload={"head": head, "branch": BRANCH, "dirty": bool(porcelain)}
        )
    except Exception as e:
        return StepResult(
            step="env_prepare", ok=False, elapsed_ms=int((time.time()-t0)*1000),
            error=str(e)
        )


def step_preflight(bus: EventBus) -> StepResult:
    """调 strategies/health_check.py 的 HealthCheckStrategy。"""
    t0 = time.time()
    try:
        from ..strategies.health_check import HealthCheckStrategy
        from ..persistence.progress_table import ProgressTableRepo
        prog_repo = ProgressTableRepo()
        prog_data = prog_repo.parse()
        strat = HealthCheckStrategy(prog_data=prog_data, prog_repo=prog_repo)
        result = strat.evaluate_health()
        if not result.ok:
            bus.emit(Event(Events.PREFLIGHT_FAILED, payload=result.payload))
        else:
            bus.emit(Event(Events.PREFLIGHT_OK, payload=result.payload))
        return StepResult(
            step="preflight", ok=result.ok, elapsed_ms=int((time.time()-t0)*1000),
            payload=result.payload, error=result.error
        )
    except Exception as e:
        return StepResult(
            step="preflight", ok=False, elapsed_ms=int((time.time()-t0)*1000),
            error=str(e)
        )


def step_load_state(bus: EventBus) -> tuple[StepResult, Optional[EngineState]]:
    t0 = time.time()
    try:
        from ..persistence.progress_table import ProgressTableRepo
        from ..persistence.project_progress import ProjectProgressRepo
        prog_repo = ProgressTableRepo()
        proj_repo = ProjectProgressRepo()
        state = prog_repo.to_engine_state(proj_repo)
        return (
            StepResult(
                step="load_state", ok=True,
                elapsed_ms=int((time.time()-t0)*1000),
                payload={
                    "active_projects": [p.name for p in state.projects],
                    "queue_len": len(state.queue),
                    "strategy_mode": state.strategy_mode.value,
                    "wip": state.metrics.wip_status.value,
                    "budget": state.metrics.error_budget.value,
                }
            ),
            state
        )
    except Exception as e:
        return (
            StepResult(
                step="load_state", ok=False,
                elapsed_ms=int((time.time()-t0)*1000),
                error=str(e)
            ),
            None
        )


def step_state_decide(bus: EventBus, state: EngineState) -> tuple[StepResult, List[Action]]:
    """调所有 strategy 收集 actions，按 priority 排序。"""
    t0 = time.time()
    actions: List[Action] = []
    errors: List[str] = []
    try:
        from ..strategies.base import discover_strategies
        for strat in discover_strategies():
            try:
                acts = strat.evaluate(state)
                actions.extend(acts)
            except Exception as e:
                # 公理 A3：失败可隔离
                errors.append(f"{strat.name}: {e}")
        actions.sort(key=lambda a: a.priority, reverse=True)
        return (
            StepResult(
                step="state_decide", ok=True,
                elapsed_ms=int((time.time()-t0)*1000),
                payload={"actions_count": len(actions), "strategy_errors": errors}
            ),
            actions
        )
    except Exception as e:
        return (
            StepResult(
                step="state_decide", ok=False,
                elapsed_ms=int((time.time()-t0)*1000),
                error=str(e)
            ),
            []
        )


def step_execute(bus: EventBus, state: EngineState, actions: List[Action]) -> StepResult:
    """执行 actions。V4 增强：decision_matrix 是 name-based 路由器，对未知 action 兜底。"""
    t0 = time.time()
    executed: List[str] = []
    errors: List[str] = []
    try:
        from ..strategies.base import discover_strategies
        from ..strategies.decision_matrix import DecisionMatrixStrategy
        strat_map = {s.name: s for s in discover_strategies()}
        # 显式注入 decision_matrix 作为 fallback router（即使 discover 没拿到）
        strat_map.setdefault("decision_matrix", DecisionMatrixStrategy())
        for action in actions:
            if action.name in ("monitor",) or action.name.startswith("pr_"):
                # monitor / pr 类可并发执行
                pass
            else:
                # 核心 action 单选（V4 简化：第一个非 monitor）
                pass
            # 优先用 action.name 直接找；找不到则用 decision_matrix 路由器
            strat = strat_map.get(action.name) or strat_map.get("decision_matrix")
            if not strat or not hasattr(strat, "execute"):
                continue
            try:
                strat.execute(state, action, bus)  # type: ignore[attr-defined]
                executed.append(action.name)
            except Exception as e:
                errors.append(f"{action.name}: {e}")
        return StepResult(
            step="execute", ok=len(errors) == 0,
            elapsed_ms=int((time.time()-t0)*1000),
            payload={"executed": executed, "errors": errors}
        )
    except Exception as e:
        return StepResult(
            step="execute", ok=False, elapsed_ms=int((time.time()-t0)*1000),
            error=str(e)
        )


def step_persist(bus: EventBus, state: EngineState, msg: str) -> StepResult:
    """原子写 + commit + push。"""
    t0 = time.time()
    try:
        from ..persistence.progress_table import ProgressTableRepo
        from ..persistence.git_ops import commit_and_push
        repo = ProgressTableRepo()
        repo.write_engine_state(state)
        sha = commit_and_push(msg)
        return StepResult(
            step="persist", ok=bool(sha) and not sha.startswith(("commit_failed", "push_failed")),
            elapsed_ms=int((time.time()-t0)*1000),
            payload={"sha": sha}
        )
    except Exception as e:
        return StepResult(
            step="persist", ok=False,
            elapsed_ms=int((time.time()-t0)*1000),
            error=str(e)
        )


# ─────────────────────────────────────────────────────────────────────
# 编排器
# ─────────────────────────────────────────────────────────────────────
class Orchestrator:
    """7 步工作流驱动。"""

    def __init__(self) -> None:
        self.bus = EventBus()

    def tick(self, dry_run: bool = False) -> TickReport:
        started = _now()
        head_before = _run_git(["rev-parse", "--short", "HEAD"]).stdout.strip()
        report = TickReport(version="4", started_at=started, head_commit_before=head_before)

        # Step 1: env_prepare
        s = step_env_prepare(self.bus)
        report.steps.append(s)
        if not s.ok:
            report.overall_ok = False
            report.finished_at = _now()
            self.bus.emit(Event(Events.TICK_FAILED, payload={"step": "env_prepare"}))
            return report

        # Step 2: preflight
        s = step_preflight(self.bus)
        report.steps.append(s)
        if not s.ok:
            # 不 exit：仍可写 heartbeat 标 preflight_failed
            self.bus.emit(Event(Events.TICK_FAILED, payload={"step": "preflight"}))

        # Step 3: load_state
        s, state = step_load_state(self.bus)
        report.steps.append(s)
        if not s.ok or state is None:
            report.overall_ok = False
            report.finished_at = _now()
            return report

        # Step 4: state_decide
        s, actions = step_state_decide(self.bus, state)
        report.steps.append(s)
        report.actions_taken = actions

        # Step 5: execute
        if actions:
            s = step_execute(self.bus, state, actions)
            report.steps.append(s)

        # Step 6: persist
        next_action = actions[0].name if actions else "MAINTAIN"
        cur_node = state.find_project(actions[0].target_project).current_node if actions and actions[0].target_project else "—"
        msg = f"engine(v4): {datetime.now(timezone.utc).strftime('%Y-%m-%d [%H:%M]')} {next_action} {cur_node}"
        if not dry_run:
            s = step_persist(self.bus, state, msg)
            report.steps.append(s)
            if s.ok and s.payload.get("sha"):
                report.head_commit_after = s.payload["sha"]

        # Step 7: report
        report.finished_at = _now()
        report.events = [str(e) for e in self.bus.history()]
        report.new_state_summary = {
            "active": [p.name for p in state.projects],
            "submitted": [p.name for p in state.submitted_projects],
            "idle": [p.name for p in state.idle_projects],
            "wip": state.metrics.wip_status.value,
            "budget": state.metrics.error_budget.value,
            "mode": state.strategy_mode.value,
        }
        report.next_action_hint = next_action
        report.overall_ok = all(s.ok for s in report.steps) and report.overall_ok

        if report.overall_ok:
            self.bus.emit(Event(Events.TICK_OK, payload=report.new_state_summary))
        return report
