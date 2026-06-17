"""V5.1 Orchestrator：核心循环。

V5.1 与 V5 根本差异：
  - V5: 1 tick = 0 或 1 PR（一次性提交）
  - V5.1: 1 tick = 0 或 1-3 sub-tasks（PR 是 DAG 的 1-3 个节点）

Tick 流程：
  1. env_prepare  (git)
  2. preflight    (state machine)
  3. load_state   (从 git 恢复所有 PRWorktree)
  4. state_decide (从 active worktrees 中选 1 个 + 1-3 sub-tasks)
  5. execute      (调 sub-task handler，per-task try/except)
  6. persist      (原子写 state.json + commit + push)
  7. report       (TickReport)

关键不变量：
  - 1 tick 最多 3 sub-tasks
  - sub-task 必须按依赖顺序
  - 不在 AWAITING_GATE 状态调 sub-task
"""
from __future__ import annotations

import os
import subprocess
import time
from datetime import datetime
from typing import List, Optional, Tuple

from core.event_bus import Event, EventBus
from core.models import (
    Action, EngineState, PRState, PRWorktree, SubTask, SubTaskStatus
)
from core.quality_gate import evaluate as evaluate_quality
from core.state_machine import assert_legal, self_check as sm_self_check
from persistence.worktree_state import list_all_worktrees, save_state
from pr_worktree.executor import execute_subtask


# V5.1 项目元数据
DEFAULT_PROJECTS_META = {
    "OpenReactor": {
        "repo": "natesales/openreactor",
        "local": "/workspace/HJB/项目/OpenReactor",
        "test_dir": "pkg",
    },
    "FUSE": {
        "repo": "ProjectTorreyPines/FUSE.jl",
        "local": "/workspace/HJB/项目/FUSE",
        "test_dir": "test",
    },
    "gym-torax": {
        "repo": "antoine-mouchamps/gymtorax",
        "local": "/workspace/HJB/项目/gym-torax",
        "test_dir": "tests",
    },
}


def _git(*args: str) -> str:
    try:
        return subprocess.check_output(
            ["git", *args], text=True, stderr=subprocess.STDOUT
        ).strip()
    except subprocess.CalledProcessError as e:
        return e.output.strip()


class TickResult:
    def __init__(self) -> None:
        self.overall_ok: bool = True
        self.steps: List[dict] = []
        self.events: List[str] = []
        self.actions_taken: List[str] = []
        self.subtasks_completed: List[str] = []
        self.subtasks_failed: List[str] = []
        self.next_action_hint: str = ""


class Orchestrator:
    def __init__(self) -> None:
        self.bus = EventBus()
        self.result = TickResult()
        self.started_at = datetime.utcnow().isoformat() + "Z"
        self.state: Optional[EngineState] = None

    def _record_step(self, name: str, ok: bool, payload: dict, error: str = "", elapsed_ms: int = 0) -> None:
        self.result.steps.append({
            "step": name, "ok": ok, "elapsed_ms": elapsed_ms,
            "payload": payload, "error": error,
        })
        if not ok:
            self.result.overall_ok = False

    def _emit(self, name: str, **payload) -> None:
        ev = Event(name=name, payload=payload)
        self.bus.emit(ev)
        self.result.events.append(str(ev))

    def tick(self, dry_run: bool = False, max_subtasks_per_tick: int = 3) -> TickResult:
        """V5.1 7 步工作流。"""
        t0 = time.time()

        # Step 1: env_prepare
        head = _git("rev-parse", "--short", "HEAD")
        branch = _git("branch", "--show-current")
        dirty = bool(_git("status", "--porcelain"))
        self._record_step("env_prepare", True,
                          {"head": head, "branch": branch, "dirty": dirty},
                          elapsed_ms=int((time.time() - t0) * 1000))

        # Step 2: preflight
        t1 = time.time()
        sm_ok, sm_msg = sm_self_check()
        self._record_step("preflight", sm_ok,
                          {"state_machine": {"ok": sm_ok, "msg": sm_msg}},
                          elapsed_ms=int((time.time() - t1) * 1000))
        if not sm_ok:
            self._emit("preflight.failed", reason=sm_msg)

        # Step 3: load_state
        t2 = time.time()
        self.state = EngineState(
            version="5.1",
            timestamp=datetime.utcnow().isoformat() + "Z",
            head_commit=head,
        )
        self.state.worktrees = list_all_worktrees()
        self.result.actions_taken.append(
            f"loaded {len(self.state.worktrees)} PRWorktrees"
        )
        self._record_step("load_state", True,
                          {"worktree_count": len(self.state.worktrees),
                           "active": [w.pr_id for w in self.state.worktrees
                                      if w.state == PRState.ACCUMULATING],
                           "ready": [w.pr_id for w in self.state.worktrees
                                     if w.state == PRState.READY_TO_SUBMIT]},
                          elapsed_ms=int((time.time() - t2) * 1000))

        # Step 4: state_decide (选 1 个 active worktree + 1-3 sub-tasks)
        t3 = time.time()
        selected_wt, selected_tasks = self._select_work_and_tasks(max_subtasks_per_tick)
        self._record_step("state_decide", True,
                          {"selected_worktree": selected_wt.pr_id if selected_wt else None,
                           "selected_subtasks": [t.id for t in selected_tasks]},
                          elapsed_ms=int((time.time() - t3) * 1000))

        # Step 5: execute
        t4 = time.time()
        if selected_wt and selected_tasks:
            for st in selected_tasks:
                self._execute_one_subtask(selected_wt, st)
        self._record_step("execute", True,
                          {"completed": self.result.subtasks_completed,
                           "failed": self.result.subtasks_failed},
                          elapsed_ms=int((time.time() - t4) * 1000))

        # Step 6: persist + state transition
        t5 = time.time()
        if selected_wt:
            self._update_quality_and_state(selected_wt)
        if not dry_run:
            sha = self._persist_all()
        else:
            sha = "(dry-run)"
        self._record_step("persist", True, {"sha": sha},
                          elapsed_ms=int((time.time() - t5) * 1000))

        # Step 7: report
        self.result.next_action_hint = self._next_hint()
        return self.result

    def _select_work_and_tasks(self, max_tasks: int) -> Tuple[Optional[PRWorktree], List[SubTask]]:
        """选 1 个 active worktree + 1-3 个 sub-tasks。"""
        # 优先选 AWAITING_GATE（已 ready 但未提交）——V5.1 不在此处提交，只等人类
        ready = self.state.ready_worktrees()
        if ready:
            self._emit("ready_worktree_present", pr_id=ready[0].pr_id)
            return ready[0], []  # 不调 sub-task，只提醒
        # 选第 1 个 accumulating
        active = self.state.active_worktrees()
        if not active:
            self._emit("no_active_worktree", message="没有 ACCUMULATING 状态的 PRWorktree；需 init 新 worktree")
            return None, []
        wt = active[0]
        ready_tasks = wt.pending_ready_subtasks()
        if not ready_tasks:
            self._emit("no_ready_subtask", pr_id=wt.pr_id,
                       message="该 worktree 所有 sub-task 都在等待依赖")
            return wt, []
        # 选最多 max_tasks 个
        selected = ready_tasks[:max_tasks]
        self._emit("subtasks_selected", pr_id=wt.pr_id,
                   task_ids=[s.id for s in selected])
        return wt, selected

    def _execute_one_subtask(self, wt: PRWorktree, st: SubTask) -> None:
        """执行 1 个 sub-task。"""
        meta = DEFAULT_PROJECTS_META.get(wt.project, {})
        ctx = {
            "paper_id": wt.paper_arxiv_id,
            "project": wt.project,
            "project_path": meta.get("local", ""),
            "target_files": wt.target_files,
            "notes_dir": wt.notes_dir,
            "worktree": wt,
        }
        ok = execute_subtask(st, ctx)
        if ok:
            self.result.subtasks_completed.append(st.id)
            self.result.actions_taken.append(
                f"subtask_done: {wt.pr_id}/{st.id} ({st.type.value})"
            )
        else:
            self.result.subtasks_failed.append(st.id)
            self.result.actions_taken.append(
                f"subtask_failed: {wt.pr_id}/{st.id} ({st.type.value}, notes={st.notes})"
            )

    def _update_quality_and_state(self, wt: PRWorktree) -> None:
        """更新 quality criteria + 状态机迁移。"""
        q = evaluate_quality(wt)
        wt.quality = q
        done, total, _ = wt.progress()
        # 状态机迁移
        if done == total and total > 0 and q.self_critique_pass:
            if wt.state == PRState.ACCUMULATING:
                assert_legal(wt.state.value, PRState.SELF_REVIEW.value)
                wt.state = PRState.SELF_REVIEW
            if wt.state == PRState.SELF_REVIEW and q.is_ready_to_submit():
                # 注意：human_approved 是 false → 仍不能 READY_TO_SUBMIT
                # 简化：quality.is_ready_to_submit() 已包含 human_approved
                if q.human_approved:
                    assert_legal(wt.state.value, PRState.READY_TO_SUBMIT.value)
                    wt.state = PRState.READY_TO_SUBMIT
                # 没 human_approved 仍 SELF_REVIEW
        wt.updated_at = datetime.utcnow().isoformat() + "Z"

    def _persist_all(self) -> str:
        """写所有 worktree + main progress table + commit + push。"""
        for wt in self.state.worktrees:
            save_state(wt)
        # 写主表
        main_path = "核聚变开源贡献系统/进度表.md"
        if os.path.isfile(main_path):
            import re as _re
            with open(main_path, encoding="utf-8") as f:
                md = f.read()
            done, total = self._global_progress()
            note = f"v5.1 tick @ {datetime.utcnow().isoformat()}Z; worktrees={len(self.state.worktrees)}; progress={done}/{total}"
            md = _re.sub(r"LAST_HEARTBEAT: .*",
                         f"LAST_HEARTBEAT: {datetime.utcnow().isoformat()}", md)
            md = _re.sub(r"LAST_HEARTBEAT_NOTE: .*",
                         f"LAST_HEARTBEAT_NOTE: {note}", md)
            with open(main_path, "w", encoding="utf-8") as f:
                f.write(md)
        _git("add", "-A")
        _git("commit", "-m", f"engine(v5.1): {datetime.utcnow().strftime('%Y-%m-%d %H:%M')} tick")
        sha = _git("rev-parse", "--short", "HEAD")
        _git("push", "origin", "trae/solo-agent-TbCBsF")
        return sha

    def _global_progress(self) -> Tuple[int, int]:
        done = sum(s.status == SubTaskStatus.DONE
                   for w in self.state.worktrees for s in w.subtasks)
        total = sum(len(w.subtasks) for w in self.state.worktrees)
        return done, total

    def _next_hint(self) -> str:
        active = self.state.active_worktrees()
        ready = self.state.ready_worktrees()
        if ready:
            return f"ready_for_human_gate:{ready[0].pr_id}"
        if active:
            return f"accumulating:{active[0].pr_id}"
        return "no_active_worktree"
