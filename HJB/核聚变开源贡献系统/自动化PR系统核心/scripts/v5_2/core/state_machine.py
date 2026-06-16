"""V5.1 状态机（13 状态，PRWorktree 维度）。

核心扩展自 V5：
  - DECOMPOSING: 正在把 PR 拆解为 sub-tasks
  - ACCUMULATING: 核心状态——PR 正在被 sub-tasks 积累推进
  - SELF_REVIEW: 子任务都做完了，进入自批评
  - READY_TO_SUBMIT: 自批评通过，等人类 gate

V5 状态机 11 状态 + 新增 2 = 13 状态。
"""
from __future__ import annotations

from typing import Dict, Set, Tuple


class IllegalTransition(Exception):
    pass


# V5.1 允许的状态转换
ALLOWED: Dict[str, Set[str]] = {
    "backlog":            {"decomposing", "analyzing"},
    "analyzing":          {"decomposing", "backlog"},
    "decomposing":        {"accumulating", "backlog"},   # 拆解成功 → 积累；失败 → 回 backlog
    "accumulating":       {"self_review", "stalled", "backlog"},  # sub-task 全 done → 自批评；长时间没进展 → stalled
    "self_review":        {"ready_to_submit", "accumulating"},     # 批评通过 → ready；批评发现 gap → 回到 accumulating 修
    "ready_to_submit":    {"awaiting_gate", "accumulating"},      # 人类 gate → 等；人类否决 → 回修
    "awaiting_gate":      {"pr_submitting", "accumulating", "closed"},
    "pr_submitting":      {"submitted", "closed"},
    "submitted":          {"revision", "merged", "closed", "stalled"},
    "revision":           {"submitted", "merged", "closed"},
    "stalled":            {"accumulating", "backlog", "closed"},
    "merged":             {"backlog"},
    "closed":             {"backlog"},
    "analyzing":          {"decomposing", "backlog"},
}


def is_legal(frm: str, to: str) -> bool:
    if frm == to:
        return True
    return to in ALLOWED.get(frm, set())


def assert_legal(frm: str, to: str) -> None:
    if not is_legal(frm, to):
        raise IllegalTransition(f"{frm} -> {to} 非法（V5.1 状态机约束）")


def all_states() -> Set[str]:
    return set(ALLOWED.keys())


def self_check() -> Tuple[bool, str]:
    """V5.1 状态机自检。"""
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
    return True, "V5.1 状态机自检通过"
