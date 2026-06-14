"""V4 多项目并进决策矩阵（v2.1 文档→代码）。

5 级决策（从高到低）：
  REVISION > CODE > MONITOR > STALLED > BUGFIX

每条规则可热插拔（公理 A5）。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, List

from ..core.event_bus import Event, EventBus, Events
from ..core.models import Action, ContributionState, EngineState


@dataclass
class DecisionRule:
    """单条决策规则。"""
    name: str
    priority: int
    applies: Callable[[EngineState], bool]
    action_factory: Callable[[EngineState], Action]


def _has_revision(state: EngineState) -> bool:
    for p in state.projects:
        if p.state == ContributionState.REVISION:
            return True
    return False


def _has_idle(state: EngineState) -> bool:
    return bool(state.idle_projects)


def _all_submitted(state: EngineState) -> bool:
    return state.has_submitted and not state.idle_projects


def _all_stalled(state: EngineState) -> bool:
    """所有 submitted PR > 7d 无 review。"""
    submitted = [p for p in state.projects if p.is_submitted]
    if not submitted:
        return False
    return all(p.pr_age_hours > 24 * 7 for p in submitted)


def _has_critical_bug(state: EngineState) -> bool:
    """LAST_HEARTBEAT_STATUS = commit_failed/push_failed/preflight_failed_* 视为 critical。"""
    s = state.last_heartbeat_status.lower()
    return any(s.startswith(prefix) for prefix in ("commit_failed", "push_failed", "preflight_failed"))


# ── 规则表（priority 降序） ──
RULES: List[DecisionRule] = [
    DecisionRule(
        name="revision",
        priority=200,
        applies=_has_revision,
        action_factory=lambda s: Action(
            name="revision",
            priority=200,
            target_project=next((p.name for p in s.projects if p.state == ContributionState.REVISION), ""),
            rationale="有项目处于 REVISION 状态 → 处理 review 反馈",
        ),
    ),
    DecisionRule(
        name="code",
        priority=100,
        applies=_has_idle,
        action_factory=lambda s: Action(
            name="code",
            priority=100,
            target_project=s.idle_projects[0].name,
            rationale=f"有 idle 项目 {s.idle_projects[0].name} → 继续开发（{s.idle_projects[0].current_node}）",
        ),
    ),
    DecisionRule(
        name="monitor",
        priority=60,
        applies=_all_submitted,
        action_factory=lambda s: Action(
            name="monitor",
            priority=60,
            rationale=f"所有项目均 submitted → 巡检 + 系统维护（{len(s.submitted_projects)} PR）",
        ),
    ),
    DecisionRule(
        name="stalled",
        priority=40,
        applies=_all_stalled,
        action_factory=lambda s: Action(
            name="stalled",
            priority=40,
            rationale="所有 PR > 7d 无 review → 巡检 + 考虑主动 comment ping 维护者",
        ),
    ),
    DecisionRule(
        name="bugfix",
        priority=20,
        applies=_has_critical_bug,
        action_factory=lambda s: Action(
            name="bugfix",
            priority=20,
            rationale=f"严重系统 bug（last_heartbeat_status={s.last_heartbeat_status}）→ 优先修系统",
        ),
    ),
]


class DecisionMatrixStrategy:
    name = "decision_matrix"

    def evaluate(self, state: EngineState) -> List[Action]:
        out: List[Action] = []
        for rule in RULES:
            try:
                if rule.applies(state):
                    out.append(rule.action_factory(state))
            except Exception:
                # 规则失败不污染（公理 A3）
                pass
        # 只返回最高 priority 的 action（V4 简化：单选核心 + monitor 可叠加）
        if not out:
            return []
        out.sort(key=lambda a: a.priority, reverse=True)
        return [out[0]]

    def execute(self, state: EngineState, action: Action, bus: EventBus) -> None:
        """V4 简化：核心 action 的副作用是触发 P1.1 / 处理 review 等业务，
        由 orchestrator 在 step 5 调对应 strategy.execute 链。
        decision_matrix 自身只 emit 决策事件供 observability。"""
        bus.emit(Event(Events.TICK_OK, {"decision": action.name, "target": action.target_project}))
