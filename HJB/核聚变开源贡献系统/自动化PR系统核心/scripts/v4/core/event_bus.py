"""V4 事件总线。

解耦 strategy 与 persistence / observability：
- Strategy 发出 Event（如 ReviewReceived / BranchStale）
- Persistence 订阅 Event 写进度表
- Observability 订阅 Event 写日志
- 不直接依赖。

V4 简化版：内存总线 + tick 边界自动 flush。
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Type


@dataclass
class Event:
    """V4 事件基类。"""
    name: str
    payload: Dict[str, Any] = field(default_factory=dict)
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds")
    )

    def __str__(self) -> str:
        return f"Event({self.name}@{self.timestamp}, payload_keys={list(self.payload.keys())})"


# ── 预定义事件类型 ──
class Events:
    """事件名常量（避免散落字符串）。"""
    PREFLIGHT_FAILED = "preflight.failed"
    PREFLIGHT_OK = "preflight.ok"
    REVIEW_RECEIVED = "pr.review_received"
    PR_MERGED = "pr.merged"
    PR_CLOSED = "pr.closed"
    PR_STALLED = "pr.stalled"
    BRANCH_STALE = "branch.stale"
    CHECKPOINT_SAVED = "checkpoint.saved"
    STRATEGY_MODE_CHANGED = "strategy.mode_changed"
    WIP_EXCEEDED = "wip.exceeded"
    ERROR_BUDGET_DEPLETED = "error_budget.depleted"
    PROJECT_INITED = "project.inited"
    TICK_FAILED = "tick.failed"
    TICK_OK = "tick.ok"


class EventBus:
    """内存事件总线。

    用法：
        bus = EventBus()
        @bus.subscribe(Events.REVIEW_RECEIVED)
        def _on_review(evt: Event):
            ...
        bus.emit(Event(Events.REVIEW_RECEIVED, {"pr": 6}))
    """

    def __init__(self) -> None:
        self._handlers: Dict[str, List[Callable[[Event], None]]] = defaultdict(list)
        self._log: List[Event] = []

    def subscribe(self, event_name: str) -> Callable[[Callable[[Event], None]], Callable[[Event], None]]:
        """装饰器形式订阅。"""
        def deco(fn: Callable[[Event], None]) -> Callable[[Event], None]:
            self._handlers[event_name].append(fn)
            return fn
        return deco

    def emit(self, event: Event) -> None:
        """发出事件，立即同步触发所有订阅者。"""
        self._log.append(event)
        for fn in self._handlers.get(event.name, []):
            try:
                fn(event)
            except Exception as e:  # 订阅者失败不能污染 emit
                # 用 stderr 即可（observability 模块会捕获）
                import sys
                print(f"[event_bus] handler failed for {event.name}: {e}", file=sys.stderr)

    def history(self) -> List[Event]:
        return list(self._log)

    def clear(self) -> None:
        self._log.clear()
