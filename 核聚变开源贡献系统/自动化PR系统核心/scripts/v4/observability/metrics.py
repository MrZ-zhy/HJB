"""V4 RED 指标 + SLO 燃烧率。

当前 V4 简化版：仅在内存中计算 snapshot，输出给 Trae 报告。
未来可接入 Prometheus / OpenTelemetry。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List

from ..core.models import EngineState


@dataclass
class MetricsSnapshot:
    timestamp: str
    active_projects: int
    submitted_projects: int
    idle_projects: int
    prs_total: int
    prs_stalled: int
    strategy_mode: str
    wip_status: str
    error_budget: str
    lock: bool

    def to_dict(self) -> Dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "active_projects": self.active_projects,
            "submitted_projects": self.submitted_projects,
            "idle_projects": self.idle_projects,
            "prs_total": self.prs_total,
            "prs_stalled": self.prs_stalled,
            "strategy_mode": self.strategy_mode,
            "wip_status": self.wip_status,
            "error_budget": self.error_budget,
            "lock": self.lock,
        }


def snapshot(state: EngineState) -> MetricsSnapshot:
    submitted = state.submitted_projects
    stalled = [p for p in submitted if p.pr_age_hours > 24 * 7]
    return MetricsSnapshot(
        timestamp=state.timestamp,
        active_projects=len(state.projects),
        submitted_projects=len(submitted),
        idle_projects=len(state.idle_projects),
        prs_total=len(submitted),
        prs_stalled=len(stalled),
        strategy_mode=state.strategy_mode.value,
        wip_status=state.metrics.wip_status.value,
        error_budget=state.metrics.error_budget.value,
        lock=state.metrics.lock,
    )
