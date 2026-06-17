"""V4 Strategy 协议 + 自动发现。

公理 A5：可插拔 = 可演进。新加 strategy 只需在 strategies/ 下加一个文件
并把类名注册到 __all__，无需改 orchestrator。
"""
from __future__ import annotations

import importlib
import importlib.util
import inspect
import os
import pkgutil
from typing import List, Protocol

from ..core.event_bus import EventBus
from ..core.models import Action, EngineState


class Strategy(Protocol):
    """所有 strategy 必须实现 evaluate(state) -> List[Action]。

    可选实现 execute(state, action, bus)，让 orchestrator 能在 step 5 自动执行。
    """
    name: str

    def evaluate(self, state: EngineState) -> List[Action]: ...


# 不需要执行的 strategy（如 monitor）只实现 evaluate
# 需要执行副作用的 strategy 额外实现 execute(self, state, action, bus)
class ExecutableStrategy(Strategy, Protocol):
    def execute(self, state: EngineState, action: Action, bus: EventBus) -> None: ...


_STRATEGY_CLASSES = [
    "HealthCheckStrategy",
    "DecisionMatrixStrategy",
    "PRStrategy",
    "ProjectSelectorStrategy",
]


def discover_strategies() -> List[Strategy]:
    """自动发现 strategies/ 下所有策略类（按 _STRATEGY_CLASSES 顺序）。"""
    out: List[Strategy] = []
    strategies_dir = os.path.dirname(os.path.abspath(__file__))
    pkg_name = __name__.rsplit(".", 1)[0]  # 父包名 = v4.strategies
    for mod_info in pkgutil.iter_modules([strategies_dir]):
        if mod_info.name in ("base", "__init__"):
            continue
        full_name = f"{pkg_name}.{mod_info.name}"
        try:
            mod = importlib.import_module(full_name)
        except Exception:
            continue
        for cls_name in _STRATEGY_CLASSES:
            cls = getattr(mod, cls_name, None)
            if cls is None:
                continue
            try:
                # 尝试无参构造；若需参数则跳过
                inst = cls()
                out.append(inst)
            except TypeError:
                # 构造需参数，延迟到 orchestrator 注入
                pass
    return out
