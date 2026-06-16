"""V4 贡献状态机（代码，不只是文档）。

实现 核聚变开源贡献系统/自动化PR系统核心/贡献状态机.md 的所有合法转换。
任何 (from_state, to_state) 转换都先过这里；非法 → 抛 IllegalTransition。

公理 A2：状态机是代码，不是文档。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Dict, Optional, Set, Tuple

from .models import ContributionState, EngineState, WipStatus, ErrorBudgetStatus


# ─────────────────────────────────────────────────────────────────────
# 守卫上下文
# ─────────────────────────────────────────────────────────────────────
@dataclass
class GuardContext:
    """转换守卫需要的最小上下文（避免传整个 EngineState）。"""
    wip_ok: bool = True
    budget_ok: bool = True
    proactive_path_eligible: bool = False  # 上游 0 open issue
    no_ci_configured: bool = False
    critic_issues_critical: int = 0
    revision_attempts: int = 0
    review_feedback_present: bool = False
    pr_closed: bool = False
    wip_exceeded: bool = False

    @classmethod
    def from_engine(cls, state: EngineState) -> "GuardContext":
        return cls(
            wip_ok=state.metrics.wip_ok,
            budget_ok=state.metrics.budget_ok,
        )


# ─────────────────────────────────────────────────────────────────────
# 转换表（每条带守卫）
# ─────────────────────────────────────────────────────────────────────
# 格式: (from, to) -> guard(ctx) -> bool
# 没有守卫的转换：guard=lambda ctx: True
_LEGAL: Dict[Tuple[ContributionState, ContributionState], Callable[[GuardContext], bool]] = {}


def _register(frm: ContributionState, to: ContributionState,
              guard: Callable[[GuardContext], bool] = lambda c: True) -> None:
    _LEGAL[(frm, to)] = guard


# 路径 1: BACKLOG → ANALYZING / TODO
_register(ContributionState.BACKLOG, ContributionState.ANALYZING,
          guard=lambda c: c.wip_ok)
_register(ContributionState.BACKLOG, ContributionState.TODO,
          guard=lambda c: c.proactive_path_eligible)  # 主动缺陷扫描路径
_register(ContributionState.BACKLOG, ContributionState.CANCELLED)

# 路径 2: ANALYZING → TODO / CANCELLED
_register(ContributionState.ANALYZING, ContributionState.TODO,
          guard=lambda c: c.wip_ok)
_register(ContributionState.ANALYZING, ContributionState.CANCELLED)

# 路径 3: TODO → CODING
_register(ContributionState.TODO, ContributionState.CODING,
          guard=lambda c: c.wip_ok)

# 路径 4: CODING → REVIEWING / TODO
_register(ContributionState.CODING, ContributionState.REVIEWING,
          guard=lambda c: c.critic_issues_critical == 0)
_register(ContributionState.CODING, ContributionState.TODO,
          guard=lambda c: c.revision_attempts >= 3)

# 路径 5: REVIEWING → READY / CODING
_register(ContributionState.REVIEWING, ContributionState.READY,
          guard=lambda c: c.critic_issues_critical == 0)
_register(ContributionState.REVIEWING, ContributionState.CODING,
          guard=lambda c: c.critic_issues_critical > 0 and c.revision_attempts < 3)
_register(ContributionState.REVIEWING, ContributionState.TODO,
          guard=lambda c: c.revision_attempts >= 3)

# 路径 6: READY → SUBMITTED / WAIT
_register(ContributionState.READY, ContributionState.SUBMITTED,
          guard=lambda c: c.wip_ok and c.budget_ok)
_register(ContributionState.READY, ContributionState.WAIT,
          guard=lambda c: not c.wip_ok or not c.budget_ok)
_register(ContributionState.READY, ContributionState.READY,
          guard=lambda c: not c.wip_ok)  # 保持 WAIT 类

# 路径 7: WAIT → SUBMITTED
_register(ContributionState.WAIT, ContributionState.SUBMITTED,
          guard=lambda c: c.wip_ok and c.budget_ok)

# 路径 8: SUBMITTED → CI_RUNNING / UNDER_REVIEW（无 CI 配置时跳过 CI）
_register(ContributionState.SUBMITTED, ContributionState.CI_RUNNING,
          guard=lambda c: not c.no_ci_configured)
_register(ContributionState.SUBMITTED, ContributionState.UNDER_REVIEW,
          guard=lambda c: c.no_ci_configured)

# 路径 9: CI_RUNNING → UNDER_REVIEW / REVISION / CODING
_register(ContributionState.CI_RUNNING, ContributionState.UNDER_REVIEW)  # CI 通过
_register(ContributionState.CI_RUNNING, ContributionState.REVISION,
          guard=lambda c: c.critic_issues_critical > 0 and c.revision_attempts < 3)
_register(ContributionState.CI_RUNNING, ContributionState.CODING,
          guard=lambda c: c.critic_issues_critical > 0 and c.revision_attempts < 3)

# 路径 10: UNDER_REVIEW → MERGED / REVISION / REJECTED
_register(ContributionState.UNDER_REVIEW, ContributionState.MERGED)
_register(ContributionState.UNDER_REVIEW, ContributionState.REVISION,
          guard=lambda c: c.review_feedback_present)
_register(ContributionState.UNDER_REVIEW, ContributionState.REJECTED,
          guard=lambda c: c.pr_closed)

# 路径 11: REVISION → REVIEWING / REJECTED
_register(ContributionState.REVISION, ContributionState.REVIEWING,
          guard=lambda c: c.critic_issues_critical == 0)
_register(ContributionState.REVISION, ContributionState.REJECTED,
          guard=lambda c: c.revision_attempts >= 3)

# 终态不可逆（公理 A2 衍生）
# MERGED / REJECTED / CANCELLED 之后没有 outbound 转换（用 _TERMINAL 表达）


_TERMINAL: Set[ContributionState] = {
    ContributionState.MERGED,
    ContributionState.REJECTED,
    ContributionState.CANCELLED,
}


# ─────────────────────────────────────────────────────────────────────
# 公开 API
# ─────────────────────────────────────────────────────────────────────
class IllegalTransition(Exception):
    pass


class StateMachine:
    """贡献状态机（项目级）。

    用法：
        sm = StateMachine()
        sm.transition(project, ContributionState.CODING, ctx)
    """

    def is_legal(self, frm: ContributionState, to: ContributionState,
                 ctx: GuardContext) -> bool:
        if frm in _TERMINAL:
            return False
        guard = _LEGAL.get((frm, to))
        if guard is None:
            return False
        return guard(ctx)

    def legal_targets(self, frm: ContributionState,
                      ctx: GuardContext) -> Set[ContributionState]:
        if frm in _TERMINAL:
            return set()
        return {to for (ff, to), guard in _LEGAL.items() if ff == frm and guard(ctx)}

    def transition(self, project, to: ContributionState,
                   ctx: GuardContext) -> None:
        """应用转换。非法 → 抛 IllegalTransition。"""
        frm = project.state
        if not self.is_legal(frm, to, ctx):
            raise IllegalTransition(
                f"{project.name}: {frm.value} -> {to.value} 不合法或守卫失败"
            )
        project.state = to
