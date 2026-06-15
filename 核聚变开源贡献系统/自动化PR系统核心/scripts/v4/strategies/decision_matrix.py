"""V4 多项目并进决策矩阵（v2.1 文档→代码）。

5 级决策（从高到低）：
  REVISION > CODE > MONITOR > STALLED > BUGFIX

每条规则可热插拔（公理 A5）。

V4 增强：execute() 改为 name-based 路由器（5 个 action 都有显式处理）：
  - code     → _route_code（按 P1.1 / P2.x 节点分发到子处理器）
  - monitor  → _route_monitor（巡检 submitted PR）
  - stalled  → _route_stalled（ping 维护者建议）
  - revision → _route_revision（处理 review 反馈）
  - bugfix   → _route_bugfix（critical bug 优先修系统）

所有副作用：
  - 外部命令（git / gh）失败时仅记日志（用户选择），不抛错
  - GitHub API 调用全部走 _github_api 共享辅助
  - 状态机转换失败时降级为 warning event，不影响 tick overall_ok
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Dict, List, Optional

from ..core.event_bus import Event, EventBus, Events
from ..core.models import Action, ContributionState, EngineState
from ..core.state_machine import GuardContext, StateMachine
from ..persistence.project_progress import ProjectProgressRepo
from . import _github_api as gh


# ─────────────────────────────────────────────────────────────────────
# 决策规则（5 条）
# ─────────────────────────────────────────────────────────────────────
@dataclass
class DecisionRule:
    """单条决策规则。"""
    name: str
    priority: int
    applies: Callable[[EngineState], bool]
    action_factory: Callable[[EngineState], Action]


def _has_revision(state: EngineState) -> bool:
    for p in state.projects:
        if p.state == ContributionState.REVISION:
            return True
    return False


def _has_idle(state: EngineState) -> bool:
    return bool(state.idle_projects)


def _all_submitted(state: EngineState) -> bool:
    return state.has_submitted and not state.idle_projects


def _all_stalled(state: EngineState) -> bool:
    """所有 submitted PR > 7d 无 review。"""
    submitted = [p for p in state.projects if p.is_submitted]
    if not submitted:
        return False
    return all(p.pr_age_hours > 24 * 7 for p in submitted)


def _has_critical_bug(state: EngineState) -> bool:
    """LAST_HEARTBEAT_STATUS = commit_failed/push_failed/preflight_failed_* 视为 critical。"""
    s = state.last_heartbeat_status.lower()
    return any(s.startswith(prefix) for prefix in ("commit_failed", "push_failed", "preflight_failed"))


RULES: List[DecisionRule] = [
    DecisionRule(
        name="revision",
        priority=200,
        applies=_has_revision,
        action_factory=lambda s: Action(
            name="revision",
            priority=200,
            target_project=next((p.name for p in s.projects if p.state == ContributionState.REVISION), ""),
            rationale="有项目处于 REVISION 状态 → 处理 review 反馈",
        ),
    ),
    DecisionRule(
        name="code",
        priority=100,
        applies=_has_idle,
        action_factory=lambda s: Action(
            name="code",
            priority=100,
            target_project=s.idle_projects[0].name,
            rationale=f"有 idle 项目 {s.idle_projects[0].name} → 继续开发（{s.idle_projects[0].current_node}）",
        ),
    ),
    DecisionRule(
        name="monitor",
        priority=60,
        applies=_all_submitted,
        action_factory=lambda s: Action(
            name="monitor",
            priority=60,
            rationale=f"所有项目均 submitted → 巡检 + 系统维护（{len(s.submitted_projects)} PR）",
        ),
    ),
    DecisionRule(
        name="stalled",
        priority=40,
        applies=_all_stalled,
        action_factory=lambda s: Action(
            name="stalled",
            priority=40,
            rationale="所有 PR > 7d 无 review → 巡检 + 考虑主动 comment ping 维护者",
        ),
    ),
    DecisionRule(
        name="bugfix",
        priority=20,
        applies=_has_critical_bug,
        action_factory=lambda s: Action(
            name="bugfix",
            priority=20,
            rationale=f"严重系统 bug（last_heartbeat_status={s.last_heartbeat_status}）→ 优先修系统",
        ),
    ),
]


# ─────────────────────────────────────────────────────────────────────
# 决策 + 路由策略
# ─────────────────────────────────────────────────────────────────────
class DecisionMatrixStrategy:
    name = "decision_matrix"

    def evaluate(self, state: EngineState) -> List[Action]:
        out: List[Action] = []
        for rule in RULES:
            try:
                if rule.applies(state):
                    out.append(rule.action_factory(state))
            except Exception:
                # 规则失败不污染（公理 A3）
                pass
        if not out:
            return []
        out.sort(key=lambda a: a.priority, reverse=True)
        return [out[0]]

    def execute(self, state: EngineState, action: Action, bus: EventBus) -> None:
        """V4 name-based 路由器：5 个 action 都有显式处理。"""
        handler = {
            "code": self._route_code,
            "monitor": self._route_monitor,
            "stalled": self._route_stalled,
            "revision": self._route_revision,
            "bugfix": self._route_bugfix,
        }.get(action.name)
        if handler is None:
            bus.emit(Event("router.unknown",
                           {"action": action.name, "reason": "no handler registered"}))
            return
        try:
            handler(state, action, bus)
        except Exception as e:
            # 用户选择：允许外部命令 + 失败仅记日志
            bus.emit(Event("router.error",
                           {"action": action.name, "error": str(e)[:300]}))

    # ─────────────────────────────────────────────────────────────────
    # 路由实现
    # ─────────────────────────────────────────────────────────────────
    def _route_code(self, state: EngineState, action: Action, bus: EventBus) -> None:
        """code action：按 current_node 分发到 P1.1 / P2.x 子处理器。"""
        name = action.target_project
        proj = state.find_project(name) if name else None
        if not proj:
            bus.emit(Event("code.error",
                           {"project": name or "?", "reason": "project not found in active set"}))
            return
        node = proj.current_node or "P1.1"
        if node == "P1.1":
            self._code_p1_1(state, proj, bus)
        elif node.startswith("P1.2"):
            self._code_p1_2(state, proj, bus)
        elif node.startswith("P2."):
            self._code_p2_stub(state, proj, bus, node)
        elif node.startswith("P3."):
            self._code_p3_stub(state, proj, bus, node)
        else:
            bus.emit(Event("code.skipped",
                           {"project": proj.name, "node": node, "reason": "node not auto-handled"}))

    def _route_monitor(self, state: EngineState, action: Action, bus: EventBus) -> None:
        """monitor：扫描 submitted PR 的状态变化。"""
        token = os.environ.get("GITHUB_TOKEN", "")
        if not token:
            bus.emit(Event("monitor.skipped", {"reason": "missing GITHUB_TOKEN"}))
            return
        for proj in state.submitted_projects:
            if not proj.pr_number or not proj.repo:
                continue
            try:
                owner, name = proj.repo.split("/", 1)
                status, body = gh.get_repo(owner, name, token)
                if status == 200 and isinstance(body, dict):
                    bus.emit(Event("monitor.checked",
                                   {"project": proj.name, "pr": proj.pr_number,
                                    "stars": body.get("stargazers_count"),
                                    "open_issues": body.get("open_issues_count")}))
            except Exception as e:
                bus.emit(Event("monitor.error",
                               {"project": proj.name, "error": str(e)[:200]}))

    def _route_stalled(self, state: EngineState, action: Action, bus: EventBus) -> None:
        """stalled：所有 PR 长期无 review → emit 建议 + 写 sub-table TODO。"""
        for proj in state.submitted_projects:
            bus.emit(Event("stalled.detected",
                           {"project": proj.name, "pr": proj.pr_number,
                            "age_hours": proj.pr_age_hours,
                            "recommendation": "ping 维护者 or close + reopen"}))
        # 不自动 ping（用户未授权外部副作用），只记录建议

    def _route_revision(self, state: EngineState, action: Action, bus: EventBus) -> None:
        """revision：处于 REVISION 状态的项目需要处理 review 反馈。
        当前为 stub —— 真实 review 处理需要外部 agent 介入。
        """
        for proj in state.projects:
            if proj.state == ContributionState.REVISION:
                bus.emit(Event("revision.detected",
                               {"project": proj.name, "pr": proj.pr_number,
                                "recommendation": "拉取 review 评论、修改、push"}))

    def _route_bugfix(self, state: EngineState, action: Action, bus: EventBus) -> None:
        """bugfix：critical 系统 bug → 写 LAST_HEARTBEAT_NOTE 提醒。"""
        bus.emit(Event("bugfix.critical",
                       {"last_heartbeat_status": state.last_heartbeat_status,
                        "recommendation": "下个 tick 应先修系统再继续推进"}))

    # ─────────────────────────────────────────────────────────────────
    # code 子处理器
    # ─────────────────────────────────────────────────────────────────
    def _code_p1_1(self, state: EngineState, proj, bus: EventBus) -> None:
        """P1.1 = 代码研读：fork + clone + 收集 README/issue intel + 写子表 + 状态机转换。"""
        token = os.environ.get("GITHUB_TOKEN", "")
        if not token:
            bus.emit(Event("code.p1_1.skipped",
                           {"project": proj.name, "reason": "missing GITHUB_TOKEN"}))
            return
        if "/" not in proj.repo:
            bus.emit(Event("code.p1_1.skipped",
                           {"project": proj.name, "reason": f"invalid repo: {proj.repo}"}))
            return
        owner, name = proj.repo.split("/", 1)
        upstream_default = gh.get_upstream_default_branch(owner, name, token)

        # 1. Ensure fork
        ok, fork_full, reason = gh.ensure_fork(owner, name, gh.FORK_OWNER, token)
        if not ok:
            bus.emit(Event("code.p1_1.fork_failed",
                           {"project": proj.name, "reason": reason}))
            return
        bus.emit(Event("code.p1_1.fork_ready",
                       {"project": proj.name, "fork": fork_full, "status": reason}))

        # 2. Ensure clone（克隆 fork；shallow --depth=20 节省带宽）
        ok, local_path, clone_reason = gh.ensure_clone(fork_full, proj.name, branch=upstream_default)
        if not ok:
            bus.emit(Event("code.p1_1.clone_failed",
                           {"project": proj.name, "reason": clone_reason}))
            return
        head = gh.current_head(local_path) if "cloned" in clone_reason or "already" in clone_reason else "—"
        bus.emit(Event("code.p1_1.clone_ready",
                       {"project": proj.name, "path": local_path,
                        "head": head, "status": clone_reason}))

        # 3. Gather intel（README meta + good-first-issues + recent issues）
        intel = {"readme": None, "good_first_issues": [], "recent_issues": []}
        ok, readme, _ = gh.get_readme_meta(owner, name, token)
        if ok:
            intel["readme"] = readme
        ok, gfi, _ = gh.list_good_first_issues(owner, name, token, per_page=5)
        if ok:
            intel["good_first_issues"] = gfi
        ok, ri, _ = gh.list_recent_issues(owner, name, token, per_page=5)
        if ok:
            intel["recent_issues"] = ri
        bus.emit(Event("code.p1_1.intel_gathered",
                       {"project": proj.name, "readme_size": (intel["readme"] or {}).get("size", 0),
                        "gfi_count": len(intel["good_first_issues"]),
                        "recent_count": len(intel["recent_issues"])}))

        # 4. 写子表
        self._write_p1_1_subtable(proj, fork_full, upstream_default, local_path, head, intel, bus)

        # 5. 状态机转换 BACKLOG → ANALYZING（合法转换，要求 wip_ok）
        sm = StateMachine()
        ctx = GuardContext.from_engine(state)
        try:
            sm.transition(proj, ContributionState.ANALYZING, ctx)
            bus.emit(Event("project.state_changed",
                           {"project": proj.name, "to": "ANALYZING"}))
        except Exception as e:
            bus.emit(Event("code.p1_1.state_transition_failed",
                           {"project": proj.name, "error": str(e)[:200]}))

    def _code_p1_2(self, state: EngineState, proj, bus: EventBus) -> None:
        """P1.2 = 测试验证：尝试跑测试套件（stub —— 真实跑测试需外部 agent）。"""
        bus.emit(Event("code.p1_2.deferred",
                       {"project": proj.name,
                        "reason": "测试执行需外部 agent，本 tick 仅记录 TODO"}))

    def _code_p2_stub(self, state: EngineState, proj, bus: EventBus, node: str) -> None:
        """P2.* = 开发实施：需外部 agent 写代码 + 跑测试 + commit + push。"""
        bus.emit(Event("code.p2.deferred",
                       {"project": proj.name, "node": node,
                        "reason": "代码开发需外部 agent；本 tick 写 NEXT_ACTION 到子表"}))

    def _code_p3_stub(self, state: EngineState, proj, bus: EventBus, node: str) -> None:
        """P3.* = PR 提交：需外部 agent 写 PR 描述 + 调 GitHub API 创建 PR。"""
        bus.emit(Event("code.p3.deferred",
                       {"project": proj.name, "node": node,
                        "reason": "PR 创建需外部 agent；本 tick 写 NEXT_ACTION 到子表"}))

    # ─────────────────────────────────────────────────────────────────
    # 子表写入（P1.1 完成后）
    # ─────────────────────────────────────────────────────────────────
    def _write_p1_1_subtable(self, proj, fork_full: str, default_branch: str,
                             local_path: str, head: str,
                             intel: Dict, bus: EventBus) -> None:
        """把 P1.1 结果写进项目子表 codeblock 字段。"""
        repo = ProjectProgressRepo()
        try:
            now = datetime.now(timezone.utc).isoformat(timespec="seconds")
            gfi_summary = ", ".join(
                f"#{it['number']} ({it.get('comments', 0)}c)" for it in intel["good_first_issues"][:3]
            ) or "—"
            notes = (
                f"P1.1 完成于 {now}：fork={fork_full} branch={default_branch} "
                f"head={head} clone={local_path} readme_size={int((intel.get('readme') or {}).get('size', 0))}B "
                f"good_first_issues={gfi_summary}"
            )
            repo.update(proj.name, {
                "FORK": fork_full,
                "BRANCH": f"contrib/init-{now[:10]}",  # 提议的本地分支名（未创建）
                "CURRENT_NODE": "P1.2",
                "PROJECT_STATE": "ANALYZING",
                "LAST_HEARTBEAT": now,
                "LAST_HEARTBEAT_STATUS": "ok",
                "LAST_HEARTBEAT_COMMIT": head if head and head != "—" else "",
                "HEAD_COMMIT_SHA": head if head and head != "—" else "",
                "LAST_HEARTBEAT_NOTE": notes,
            })
            bus.emit(Event("code.p1_1.subtable_updated",
                           {"project": proj.name, "next_node": "P1.2"}))
        except Exception as e:
            bus.emit(Event("code.p1_1.subtable_failed",
                           {"project": proj.name, "error": str(e)[:200]}))
