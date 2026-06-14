"""V4 项目选择器（v2 project_rotator.py 升级版）。

队列 → 下一个 INIT 项目。
"""
from __future__ import annotations

from typing import List, Optional

from ..core.event_bus import EventBus, Events
from ..core.models import Action, ContributionState, EngineState


class ProjectSelectorStrategy:
    name = "project_selector"

    def evaluate(self, state: EngineState) -> List[Action]:
        """仅当无 idle 项目时启动下一个队列项目。"""
        if state.idle_projects:
            return []  # 已有 idle 项目 → decision_matrix 处理
        if not state.queue:
            return []
        # 选队首
        first = state.queue[0]
        name = first.get("项目", "").strip()
        if not name:
            return []
        return [Action(
            name="project_selector",
            priority=50,
            rationale=f"无 idle 项目，从队列选下一：{name}（{first.get('综合分', '?')}）",
            payload={"project": name, "score": first.get("综合分", "")},
        )]

    def execute(self, state: EngineState, action: Action, bus: EventBus) -> None:
        """把队列项目晋升为活跃。"""
        from ..core.models import ProjectState
        name = action.payload.get("project", "")
        if not name:
            return
        if state.find_project(name):
            return  # 已在活跃
        # V4 简化：占位 ProjectState（具体 fork/clone 由 ENGINE 处理）
        new_proj = ProjectState(
            name=name,
            repo=action.payload.get("repo", ""),
            state=ContributionState.INIT,
            current_node="P1.1",
        )
        state.projects.append(new_proj)
        bus.emit(Event(Events.PROJECT_INITED, {"name": name}))
