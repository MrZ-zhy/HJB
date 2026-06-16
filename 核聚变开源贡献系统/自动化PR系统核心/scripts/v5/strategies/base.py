"""V5 Strategy 协议 + 自动发现。"""
from __future__ import annotations

from typing import List, Protocol

from core.models import Action, EngineState


class Strategy(Protocol):
    name: str

    def evaluate(self, state: EngineState) -> List[Action]:
        ...


def discover_strategies() -> List[Strategy]:
    """自动发现所有 v5.strategies.* 里的 Strategy 子类实例。"""
    import importlib
    import pkgutil
    import strategies  # noqa: F401

    out: List[Strategy] = []
    for _finder, name, _ispkg in pkgutil.iter_modules(strategies.__path__):
        if name.startswith("_") or name in {"base"}:
            continue
        mod = importlib.import_module(f"strategies.{name}")
        # 模块内所有类，含 Strategy 协议方法的实例化
        for attr_name in dir(mod):
            attr = getattr(mod, attr_name)
            if isinstance(attr, type) and attr_name.endswith("Strategy") and attr_name != "Strategy":
                try:
                    inst = attr()
                    if hasattr(inst, "evaluate"):
                        out.append(inst)
                except Exception:
                    pass
    return out
