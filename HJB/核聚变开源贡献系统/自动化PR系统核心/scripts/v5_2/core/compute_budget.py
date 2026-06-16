"""V5.2 Compute Budget：控制每 tick 多少算力 / 多少 sub-task 推进。

V5.2 与 V5.1 根本差异：
  - V5.1: 每次 tick 必推进 1 个 sub-task 到 DONE
  - V5.2: 每次 tick 推进 1 次 iteration（不论 DONE/未 DONE）
    - 如果该 sub-task 已 DONE：不选
    - 如果该 sub-task quality < threshold：细化（+1 iteration）
    - 如果该 sub-task 未开始：新开

  用户在 PR 周期内调用 N 次系统：
    - N 少：可能连一个 sub-task 都做不完（浅）
    - N 多：可以给同一个 sub-task 多轮 iteration 深化
    - **PR 数量不变，只单 sub-task 质量变高**

ComputeDensity 模式：
  - QUICK:   1 sub-task/tick, max_iter=1 (V5.1 行为)
  - DEFAULT: 1 sub-task/tick, max_iter=type default（推荐）
  - DEEP:    1 sub-task/tick, max_iter=type default × 2
  - BURST:   3 sub-tasks/tick, max_iter=1 each
"""
from __future__ import annotations

from core.models import ComputeDensity, SubTaskType, DEFAULT_PARAMS


def effective_max_iterations(type_value: str, density: str) -> int:
    """根据 compute_density 返回 sub-task 的实际 max_iterations。"""
    base = DEFAULT_PARAMS.get(type_value, {}).get("max_iterations", 1)
    if density == ComputeDensity.QUICK.value:
        return 1
    if density == ComputeDensity.DEEP.value:
        return base * 2
    if density == ComputeDensity.BURST.value:
        return 1
    # DEFAULT
    return base


def sub_tasks_per_tick(density: str) -> int:
    """每 tick 处理的 sub-task 数。"""
    if density == ComputeDensity.BURST.value:
        return 3
    return 1


def density_description(density: str) -> str:
    descs = {
        "quick":   "V5.1 兼容模式：1 sub-task/tick, max_iter=1（每 tick 完成 1 个浅 sub-task）",
        "default": "推荐：1 sub-task/tick, max_iter=type default（每 tick 深化 1 个 sub-task）",
        "deep":    "高智模式：1 sub-task/tick, max_iter=type default × 2（每 tick 给同 sub-task 2x 算力）",
        "burst":   "高吞吐：3 sub-tasks/tick, max_iter=1（每 tick 推进 3 个浅 sub-task）",
    }
    return descs.get(density, "未知 density")
