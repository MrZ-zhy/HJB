"""V5 事件总线（轻量版；保留 V4 接口兼容）。"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Dict, List


@dataclass
class Event:
    name: str
    ts: str = field(default_factory=lambda: datetime.utcnow().isoformat() + "Z")
    payload: Dict[str, Any] = field(default_factory=dict)

    def __str__(self) -> str:
        keys = list(self.payload.keys())
        return f"Event({self.name}@{self.ts}, payload_keys={keys})"


class EventBus:
    def __init__(self) -> None:
        self._listeners: Dict[str, List[Callable[[Event], None]]] = defaultdict(list)
        self.events: List[Event] = []

    def on(self, event_name: str, fn: Callable[[Event], None]) -> None:
        self._listeners[event_name].append(fn)

    def emit(self, event: Event) -> None:
        self.events.append(event)
        for fn in self._listeners.get(event.name, []):
            try:
                fn(event)
            except Exception:
                pass  # 错误隔离（axiom A3）

    def clear(self) -> None:
        self.events = []
