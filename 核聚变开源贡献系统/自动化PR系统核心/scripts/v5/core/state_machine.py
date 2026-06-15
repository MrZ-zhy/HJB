"""V5 状态机（基于 V4 core/state_machine.py 思想，扩展论文/PR 维度）。

V5 在 V4 的 14 状态之上，新增两个项目级状态：
  - AWAITING_GATE: 已生成 pre-PR 报告，等待人工 gate
  - PAPER_MATCHING: 论文匹配中
"""
from __future__ import annotations

from typing import Dict, Set, Tuple


class IllegalTransition(Exception):
    pass


# V5 允许的项目状态转换（邻接表）
ALLOWED: Dict[str, Set[str]] = {
    "backlog":            {"paper_matching", "analyzing"},
    "analyzing":          {"paper_matching", "backlog"},
    "paper_matching":     {"pr_drafting", "backlog"},     # 论文命中 → 起草 PR；未命中 → 回 backlog
    "pr_drafting":        {"awaiting_gate", "backlog"},   # 生成 pre-PR 报告 → 等 gate；失败 → 回 backlog
    "awaiting_gate":      {"pr_submitting", "backlog", "closed"},  # 人工批准 → 提交；拒绝 → 回/关
    "pr_submitting":      {"submitted", "closed"},
    "submitted":          {"revision", "merged", "closed", "stalled"},
    "revision":           {"submitted", "merged", "closed"},
    "stalled":            {"submitted", "closed", "backlog"},
    "merged":             {"backlog"},
    "closed":             {"backlog"},
    "analyzing":          {"paper_matching", "backlog"},
}


def is_legal(frm: str, to: str) -> bool:
    if frm == to:
        return True
    return to in ALLOWED.get(frm, set())


def assert_legal(frm: str, to: str) -> None:
    if not is_legal(frm, to):
        raise IllegalTransition(f"{frm} -> {to} 非法（V5 状态机约束）")


def all_states() -> Set[str]:
    return set(ALLOWED.keys())


def self_check() -> Tuple[bool, str]:
    """V5 状态机自检：所有状态都可从 backlog 出发并能回到 backlog（强连通）。"""
    visited = set()
    stack = ["backlog"]
    while stack:
        s = stack.pop()
        if s in visited:
            continue
        visited.add(s)
        for nxt in ALLOWED.get(s, set()):
            stack.append(nxt)
    missing = all_states() - visited
    if missing:
        return False, f"不可达状态: {missing}"
    return True, "V5 状态机自检通过"
