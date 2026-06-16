"""V4 数据模型。

数据类优先（dataclasses），避免 v2 散落的 dict 字符串拼接问题。
所有 V4 内部数据流通这些类型，dict 仅在序列化到进度表/JSON 时短暂出现。
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional


# ─────────────────────────────────────────────────────────────────────
# 状态机枚举（V4 一等公民）
# ─────────────────────────────────────────────────────────────────────
class ContributionState(str, Enum):
    """14 个贡献状态。与 贡献状态机.md / V4架构.md §3.2 严格同步。"""
    BACKLOG = "BACKLOG"
    ANALYZING = "ANALYZING"
    TODO = "TODO"
    CODING = "CODING"
    REVIEWING = "REVIEWING"
    READY = "READY"
    WAIT = "WAIT"
    SUBMITTED = "SUBMITTED"
    CI_RUNNING = "CI_RUNNING"
    UNDER_REVIEW = "UNDER_REVIEW"
    REVISION = "REVISION"
    MERGED = "MERGED"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"


class StrategyMode(str, Enum):
    AGGRESSIVE = "aggressive"
    CONSERVATIVE = "conservative"


class WipStatus(str, Enum):
    NORMAL = "normal"
    WARNING = "warning"
    EXCEEDED = "exceeded"


class ErrorBudgetStatus(str, Enum):
    NORMAL = "normal"
    DEPLETED = "depleted"


# ─────────────────────────────────────────────────────────────────────
# 项目级数据
# ─────────────────────────────────────────────────────────────────────
@dataclass
class ProjectState:
    """单个项目的状态快照。"""
    name: str
    repo: str
    fork: str = ""
    branch: str = ""
    state: ContributionState = ContributionState.BACKLOG
    pr_number: Optional[int] = None
    pr_url: str = ""
    pr_state: str = ""  # open/closed/merged
    pr_age_hours: float = 0.0
    review_count: int = 0
    last_review_check: str = "—"
    stalled_since: str = "—"
    current_node: str = "—"
    checkpoint: str = "—"
    sub_progress_path: str = ""  # 子表路径
    notes: str = ""

    @property
    def is_submitted(self) -> bool:
        return self.state in (
            ContributionState.SUBMITTED,
            ContributionState.CI_RUNNING,
            ContributionState.UNDER_REVIEW,
            ContributionState.REVISION,
        )

    @property
    def is_idle(self) -> bool:
        """未提交 = 需 CODE action。"""
        return self.state in (
            ContributionState.BACKLOG,
            ContributionState.ANALYZING,
            ContributionState.TODO,
            ContributionState.CODING,
            ContributionState.REVIEWING,
            ContributionState.READY,
        )

    @property
    def is_terminal(self) -> bool:
        return self.state in (
            ContributionState.MERGED,
            ContributionState.REJECTED,
            ContributionState.CANCELLED,
        )

    def to_active_row(self) -> List[str]:
        """转 v2 兼容的活跃项目行（保持进度表 schema 稳定）。"""
        return [
            self.name,
            self.repo,
            self.state.value,
            str(self.pr_number) if self.pr_number else "—",
            f"{self.pr_age_hours:.1f}h" if self.pr_age_hours else "—",
            self.last_review_check,
            self.notes or "—",
        ]


# ─────────────────────────────────────────────────────────────────────
# 系统级数据
# ─────────────────────────────────────────────────────────────────────
@dataclass
class SystemMetrics:
    """RED 指标 + WIP/预算状态。"""
    wip_status: WipStatus = WipStatus.NORMAL
    error_budget: ErrorBudgetStatus = ErrorBudgetStatus.NORMAL
    active_prs: int = 0
    repos_with_pr: Dict[str, int] = field(default_factory=dict)
    reviews_pending: int = 0
    weekly_new_prs: int = 0
    lock: bool = False

    @property
    def wip_ok(self) -> bool:
        return self.wip_status != WipStatus.EXCEEDED

    @property
    def budget_ok(self) -> bool:
        return self.error_budget != ErrorBudgetStatus.DEPLETED


@dataclass
class EngineState:
    """V4 引擎全量状态（一次 tick 的输入）。"""
    version: str = "4"
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds")
    )
    strategy_mode: StrategyMode = StrategyMode.AGGRESSIVE
    projects: List[ProjectState] = field(default_factory=list)
    queue: List[Dict[str, str]] = field(default_factory=list)  # [{顺序, 项目, 综合分, 备注}]
    metrics: SystemMetrics = field(default_factory=SystemMetrics)
    head_commit: str = ""
    last_heartbeat_status: str = "unknown"
    last_heartbeat_note: str = ""
    iron_laws_version: str = "v4-1"  # 铁律集版本

    def find_project(self, name: str) -> Optional[ProjectState]:
        for p in self.projects:
            if p.name == name:
                return p
        return None

    @property
    def submitted_projects(self) -> List[ProjectState]:
        return [p for p in self.projects if p.is_submitted]

    @property
    def idle_projects(self) -> List[ProjectState]:
        return [p for p in self.projects if p.is_idle]

    @property
    def has_submitted(self) -> bool:
        return any(p.is_submitted for p in self.projects)

    @property
    def all_stalled(self) -> bool:
        """全部 PR > 7d 无 review。"""
        return all(
            p.is_submitted and p.pr_age_hours > 24 * 7
            for p in self.projects if p.is_submitted
        )


# ─────────────────────────────────────────────────────────────────────
# Action / Step Result
# ─────────────────────────────────────────────────────────────────────
@dataclass
class Action:
    """Strategy 输出的可执行动作。"""
    name: str  # 唯一 ID
    priority: int  # 数字越大越优先
    target_project: str = ""  # 作用于哪个项目；空 = 系统级
    rationale: str = ""  # 人类可读的"为什么"
    payload: Dict[str, Any] = field(default_factory=dict)  # 执行参数

    def __str__(self) -> str:
        return f"Action({self.name}, pri={self.priority}, target={self.target_project or '<sys>'}, why={self.rationale})"


@dataclass
class StepResult:
    """Orchestrator 7 步中每步的结果。"""
    step: str
    ok: bool
    elapsed_ms: int
    payload: Dict[str, Any] = field(default_factory=dict)
    error: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class TickReport:
    """一次 tick 的完整报告（Trae 渲染用）。"""
    version: str = "4"
    started_at: str = ""
    finished_at: str = ""
    overall_ok: bool = True
    steps: List[StepResult] = field(default_factory=list)
    actions_taken: List[Action] = field(default_factory=list)
    events: List[str] = field(default_factory=list)
    new_state_summary: Dict[str, Any] = field(default_factory=dict)
    next_action_hint: str = ""
    head_commit_before: str = ""
    head_commit_after: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "version": self.version,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "overall_ok": self.overall_ok,
            "steps": [s.to_dict() for s in self.steps],
            "actions_taken": [str(a) for a in self.actions_taken],
            "events": self.events,
            "new_state_summary": self.new_state_summary,
            "next_action_hint": self.next_action_hint,
            "head_commit_before": self.head_commit_before,
            "head_commit_after": self.head_commit_after,
        }
