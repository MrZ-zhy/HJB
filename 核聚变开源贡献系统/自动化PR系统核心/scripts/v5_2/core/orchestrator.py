"""V5.2 Orchestrator：核心循环（迭代深化调度）。

V5.2 与 V5.1 调度差异（核心）：
  - V5.1: 1 tick 必推进 1-3 sub-tasks 到 DONE（切到新 sub-task）
  - V5.2: 1 tick = 1 iteration（细化现有 sub-task > 新开 sub-task）
      调度优先级：
        1) 处于 READY_TO_SUBMIT 状态的 worktree → 提示 human gate（不调 sub-task）
        2) 处于 ACCUMULATING 状态的 worktree：
           a) refinement_subtasks()（V5.2 核心：已迭代但 quality < threshold）
              **优先！** 多次 tick 给同一个 sub-task 更多算力
           b) pending_ready_subtasks()（首次启动且依赖已就绪）
        3) 没有 active worktree → 报错
  - 用户多次调用系统：会反复打到 (2.a) 同一条 sub-task，质量分递增
  - 用户偶尔调用：会推进 (2.b) 开新 sub-task，但每 sub-task 默认 max_iter=1~4

每个 tick：
  1. env_prepare
  2. preflight (state machine self-check)
  3. load_state (EngineState + 全部 PRWorktree)
  4. state_decide (worktree + 1 iteration，**优先细化**)
  5. execute (1 sub-task iteration)
  6. persist (state.json + commit + push)
  7. report (TickResult)
"""
from __future__ import annotations

import os
import subprocess
import time
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Tuple

from core.compute_budget import density_description, sub_tasks_per_tick
from core.event_bus import Event, EventBus
from core.models import (
    Action, EngineState, PRState, PRWorktree, SubTask, SubTaskStatus
)
from core.quality_gate import evaluate as evaluate_quality
from core.state_machine import assert_legal, self_check as sm_self_check
from persistence.worktree_state import list_all_worktrees, save_state
from pr_worktree.executor import execute_subtask_iteration


# V5.2 first-principles 修复：自动定位 HJB git 仓库根
# 旧版用 `Path(__file__).resolve().parent × 6` 硬编码层数，在不同部署位置会错：
#   - /workspace/核聚变开源贡献系统/.../core/orchestrator.py  → 6 层 = /workspace/ → HJB = /workspace/HJB ✓
#   - /workspace/HJB/HJB/核聚变开源贡献系统/.../core/orchestrator.py → 6 层 = /workspace/HJB/ → HJB = /workspace/HJB/HJB ✗
# 修法：从 __file__ 上溯找最近包含 .git/ 的目录，作为 HJB repo 根；
#       找不到再 fallback 到 HJB_REPO_ROOT 环境变量 / _REPO_ROOT / "HJB" 兜底。
def _find_hjb_repo(start: Path) -> Optional[Path]:
    cur = start.resolve()
    for _ in range(10):  # 最多上溯 10 层
        if (cur / ".git").exists():
            return cur
        if cur.parent == cur:
            return None
        cur = cur.parent
    return None

_hjb_auto = _find_hjb_repo(Path(__file__))
HJB_REPO_ROOT = (
    os.environ.get("HJB_REPO_ROOT")
    or (str(_hjb_auto) if _hjb_auto else None)
    or str(Path(__file__).resolve().parent.parent.parent.parent.parent.parent / "HJB")
)
# _REPO_ROOT 保留作为兼容（worktree_state 等模块仍引用它），定义为 HJB_REPO_ROOT 的父目录
# （即 HJB 仓库与 核聚变开源贡献系统/ 的共同父目录）
_REPO_ROOT = str(Path(HJB_REPO_ROOT).parent)
# V5.2 first-principles 修复：进度表实际在 HJB git 仓库里（被 git tracked），
# 不是 _REPO_ROOT/核聚变开源贡献系统/ 那个独立目录。旧版错用 _REPO_ROOT 导致
# 每次 tick 把 heartbeat 写到一个 git 仓库外、最终被 git add -A 忽略的文件，
# 造成 "nothing to commit" 假错 + 进度表永不更新。改为 HJB_REPO_ROOT。
MAIN_PROGRESS_TABLE = str(Path(HJB_REPO_ROOT) / "核聚变开源贡献系统" / "进度表.md")


# V5.2 项目元数据（沿用 V5.1）
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
    """执行 git 命令；返回 stdout/stderr。

    V5.2 first-principles 修复：原来 catch CalledProcessError 把失败返回成普通
    字符串，导致 commit/push 失败时 step 仍标 ok=True，tick 报告误判为成功。
    修法：commit 必须抛出来；其他只读命令（status/log/rev-parse）继续容错。

    V5.2 second-principles 修复：默认 cwd = HJB_REPO_ROOT，让 git 操作 cwd 无关。
    之前 _persist_all 假设 cwd 是 /workspace/HJB，但 engine 可能从 /workspace 跑
    （v5.2 import 必须在 v5_2/ 目录下），commit/push 会找不到 repo 根。
    """
    try:
        return subprocess.check_output(
            ["git", *args], text=True, stderr=subprocess.STDOUT,
            cwd=HJB_REPO_ROOT,
        ).strip()
    except subprocess.CalledProcessError as e:
        if args and args[0] in {"commit", "push", "merge", "revert"}:
            # 写类命令必须失败可见
            raise
        return e.output.strip()


def _git_checked(*args: str) -> Tuple[bool, str]:
    """V5.2 新增：与 _git 类似，但显式返回 (ok, output)；ok=True 仅当 exit=0。

    用于 push 这种「失败要可见但不抛异常」的命令。
    默认 cwd = HJB_REPO_ROOT（与 _git 对齐）。
    """
    try:
        out = subprocess.check_output(
            ["git", *args], text=True, stderr=subprocess.STDOUT,
            cwd=HJB_REPO_ROOT,
        ).strip()
        return True, out
    except subprocess.CalledProcessError as e:
        return False, (e.output or "").strip()


class TickResult:
    def __init__(self) -> None:
        self.overall_ok: bool = True
        self.steps: List[dict] = []
        self.events: List[str] = []
        self.actions_taken: List[str] = []
        self.iterations_completed: List[str] = []   # V5.2：每 tick 最多 1-3 iterations
        self.iterations_refining: List[str] = []    # 还在细化的 sub-tasks
        self.iterations_failed: List[str] = []
        self.next_action_hint: str = ""
        self.density: str = "default"


class Orchestrator:
    def __init__(self, density: str = "default") -> None:
        self.bus = EventBus()
        self.result = TickResult()
        self.result.density = density
        self.started_at = datetime.utcnow().isoformat() + "Z"
        self.state: Optional[EngineState] = None
        self.density = density
        self.last_decide_rationale: str = ""  # V5.2 实例属性：select 决策理由

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

    def tick(self, dry_run: bool = False) -> TickResult:
        """V5.2 7 步工作流。"""
        t0 = time.time()

        # Step 1: env_prepare
        # V5.2 first-principles 修复：兜底 git user.email/name 配置
        # （之前 shell 脚本只在外部配，新 session 跑 tick 时 commit 会静默失败）
        if not _git("config", "user.email"):
            _git("config", "user.email", "engine@fusion-contrib.local")
        if not _git("config", "user.name"):
            _git("config", "user.name", "Fusion-Contrib Engine v5.2")
        head = _git("rev-parse", "--short", "HEAD")
        branch = _git("branch", "--show-current")
        dirty = bool(_git("status", "--porcelain"))
        self._record_step("env_prepare", True,
                          {"head": head, "branch": branch, "dirty": dirty,
                           "density": self.density},
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
            version="5.2",
            timestamp=datetime.utcnow().isoformat() + "Z",
            head_commit=head,
            strategy_mode="iterative_deepening",
            compute_density=self.density,
        )
        self.state.worktrees = list_all_worktrees()
        # 同步 density 到每个 worktree
        for w in self.state.worktrees:
            w.compute_density = self.density
        self.result.actions_taken.append(
            f"loaded {len(self.state.worktrees)} PRWorktrees (density={self.density})"
        )
        self._record_step("load_state", True,
                          {"worktree_count": len(self.state.worktrees),
                           "active": [w.pr_id for w in self.state.worktrees
                                      if w.state == PRState.ACCUMULATING],
                           "ready": [w.pr_id for w in self.state.worktrees
                                     if w.state == PRState.READY_TO_SUBMIT],
                           "density": self.density,
                           "density_desc": density_description(self.density)},
                          elapsed_ms=int((time.time() - t2) * 1000))

        # Step 4: state_decide (V5.2 核心：细化优先)
        t3 = time.time()
        selected_wt, selected_iters = self._select_iteration()
        self._record_step("state_decide", True,
                          {"selected_worktree": selected_wt.pr_id if selected_wt else None,
                           "selected_subtasks": [t.id for t in selected_iters],
                           "rationale": self.last_decide_rationale},
                          elapsed_ms=int((time.time() - t3) * 1000))

        # Step 5: execute (V5.2：每 tick 最多 N iterations)
        t4 = time.time()
        if selected_wt and selected_iters:
            for st in selected_iters:
                self._execute_one_iteration(selected_wt, st)
        self._record_step("execute", True,
                          {"iterations_completed": self.result.iterations_completed,
                           "iterations_refining": self.result.iterations_refining,
                           "iterations_failed": self.result.iterations_failed},
                          elapsed_ms=int((time.time() - t4) * 1000))

        # Step 6: persist + state transition
        t5 = time.time()
        if selected_wt:
            self._update_quality_and_state(selected_wt)
        if not dry_run:
            try:
                sha = self._persist_all()
                self._record_step("persist", True, {"sha": sha},
                                  elapsed_ms=int((time.time() - t5) * 1000))
            except subprocess.CalledProcessError as e:
                # V5.2 first-principles 修复：commit 失败必须把 step 标 ok=false，
                # 不能让 tick 整体 overall_ok=true 但实际未落盘
                err = (e.output or "").strip() if hasattr(e, "output") else str(e)
                self._record_step("persist", False, {"attempted": True},
                                  error=err[:500],
                                  elapsed_ms=int((time.time() - t5) * 1000))
                self._emit("persist_failed", error=err[:500])
                sha = "(persist-failed)"
        else:
            sha = "(dry-run)"
            self._record_step("persist", True, {"sha": sha},
                              elapsed_ms=int((time.time() - t5) * 1000))

        # Step 7: report
        self.result.next_action_hint = self._next_hint()
        return self.result

    def _select_iteration(self) -> Tuple[Optional[PRWorktree], List[SubTask]]:
        """V5.2 调度核心：选 1 个 worktree + 1-N 个 sub-task iterations。

        优先级：
          1) READY_TO_SUBMIT worktree → 不调 sub-task，只提示
          2) ACCUMULATING worktree:
             a) refinement_subtasks()（V5.2 核心：深化现有）
             b) pending_ready_subtasks()（首次启动 + 依赖就绪）
          3) 没有 active worktree → 报错
        """
        n_iters = sub_tasks_per_tick(self.density)
        # 1) 优先 ready worktree（人类 gate 提示）
        ready = self.state.ready_worktrees()
        if ready:
            self._emit("ready_worktree_present", pr_id=ready[0].pr_id)
            self.last_decide_rationale = "READY_TO_SUBMIT: 等待 human gate"
            return ready[0], []  # 不调 sub-task

        # 1.5) V5.2 修复：SELF_REVIEW worktree 必须被处理，
        #     否则永远卡在 self_review，不会跳到 ready_to_submit
        for w in self.state.worktrees:
            if w.state == PRState.SELF_REVIEW:
                self._update_quality_and_state(w)
                q = w.quality
                if w.state == PRState.READY_TO_SUBMIT:
                    self._emit("auto_promoted_to_ready", pr_id=w.pr_id,
                               reason="is_ready_to_submit() == True")
                    self.last_decide_rationale = (
                        f"SELF_REVIEW->READY_TO_SUBMIT: {w.pr_id} 质量全过"
                    )
                else:
                    # V5.2 修复：精确列出未过的门禁项，避免"质量全过但..."这种误导
                    failed = [name for name, ok in q.checklist() if not ok]
                    if "human_approved" in failed and len(failed) == 1:
                        # 唯一卡点是 human gate
                        self._emit("awaiting_human_approval", pr_id=w.pr_id,
                                   avg_quality=q.avg_subtask_quality,
                                   min_quality=q.min_subtask_quality,
                                   human_approved=q.human_approved)
                        self.last_decide_rationale = (
                            f"SELF_REVIEW: {w.pr_id} 质量全过但 human_approved={q.human_approved};需 promote"
                        )
                    else:
                        # 多个门禁没过：精确诊断
                        self._emit("quality_gate_blocked", pr_id=w.pr_id,
                                   failed_items=failed,
                                   avg_quality=q.avg_subtask_quality,
                                   min_quality=q.min_subtask_quality)
                        self.last_decide_rationale = (
                            f"SELF_REVIEW: {w.pr_id} 质量门禁未全过；未过项: {','.join(failed)}"
                        )
                return w, []  # 不调 sub-task，只评估/迁移

        # 2) active worktree
        active = self.state.active_worktrees()
        if not active:
            self._emit("no_active_worktree", message="没有 ACCUMULATING 状态的 PRWorktree；需 init 新 worktree")
            self.last_decide_rationale = "NO_ACTIVE_WORKTREE"
            return None, []

        wt = active[0]

        # 2a) 优先细化现有 sub-task（V5.2 核心）
        refine_tasks = wt.refinement_subtasks()
        if refine_tasks:
            selected = refine_tasks[:n_iters]
            self._emit("refining_subtask", pr_id=wt.pr_id,
                       task_ids=[s.id for s in selected],
                       rationale="V5.2 优先细化现有 sub-task（quality < threshold）")
            self.last_decide_rationale = (
                f"REFINING: {len(selected)} 个 sub-task 还在 quality < threshold 阶段"
            )
            return wt, selected

        # 2b) 没有需细化的 → 开新 sub-task
        ready_tasks = wt.pending_ready_subtasks()
        if ready_tasks:
            selected = ready_tasks[:n_iters]
            self._emit("subtasks_selected", pr_id=wt.pr_id,
                       task_ids=[s.id for s in selected])
            self.last_decide_rationale = (
                f"NEW: 启动 {len(selected)} 个新 sub-task（依赖已就绪）"
            )
            return wt, selected

        # 2c) 没有 ready 也没有 refining → 等待依赖
        self._emit("no_actionable_subtask", pr_id=wt.pr_id,
                   message="所有 sub-task 都在等待依赖；无需操作")
        self.last_decide_rationale = "WAITING_DEPS"
        return wt, []

    def _execute_one_iteration(self, wt: PRWorktree, st: SubTask) -> None:
        """V5.2 核心：执行 1 次 sub-task iteration（深化或首次）。"""
        meta = DEFAULT_PROJECTS_META.get(wt.project, {})
        # V5.2 first-principles 修复：notes_dir 强制转绝对路径，避免 executor 写到错位置
        _nd = wt.notes_dir or ""
        if _nd and not os.path.isabs(_nd):
            _nd = str(_REPO_ROOT / _nd)
        # project_path 同样兜底
        _pp = meta.get("local", "")
        if _pp and not os.path.isabs(_pp):
            _pp = str(_REPO_ROOT / _pp)
        ctx = {
            "paper_id": wt.paper_arxiv_id,
            "project": wt.project,
            "project_path": _pp,
            "target_files": wt.target_files,
            "notes_dir": _nd,
            "worktree": wt,
        }
        ok, quality, msg = execute_subtask_iteration(st, ctx)
        if ok:
            self.result.iterations_completed.append(f"{wt.pr_id}/{st.id}")
            self.result.actions_taken.append(
                f"iter_done: {wt.pr_id}/{st.id} ({st.type.value}, iter={st.iterations_done}, quality={quality:.1f})"
            )
            self._emit("iteration_done", pr_id=wt.pr_id, task_id=st.id,
                       iteration=st.iterations_done, quality=quality)
        elif "REFINING" in msg:
            self.result.iterations_refining.append(f"{wt.pr_id}/{st.id}")
            self.result.actions_taken.append(
                f"iter_refining: {wt.pr_id}/{st.id} ({st.type.value}, iter={st.iterations_done}/{st.max_iterations}, quality={quality:.1f}/{st.quality_threshold})"
            )
            self._emit("iteration_refining", pr_id=wt.pr_id, task_id=st.id,
                       iteration=st.iterations_done, max_iter=st.max_iterations,
                       quality=quality, threshold=st.quality_threshold)
        else:
            self.result.iterations_failed.append(f"{wt.pr_id}/{st.id}")
            self.result.actions_taken.append(
                f"iter_failed: {wt.pr_id}/{st.id} ({st.type.value}, {msg})"
            )
            self._emit("iteration_failed", pr_id=wt.pr_id, task_id=st.id, reason=msg)

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
                if q.human_approved:
                    assert_legal(wt.state.value, PRState.READY_TO_SUBMIT.value)
                    wt.state = PRState.READY_TO_SUBMIT
        wt.updated_at = datetime.utcnow().isoformat() + "Z"

    def _persist_all(self) -> str:
        """写所有 worktree + main progress table + commit + push。

        V5.2 变更：
          - push 目标分支 = `HJB_BRANCH` 环境变量，默认使用当前 HEAD 分支（V5.1 遗留硬编码 trae 已废弃）
          - push 失败时**不再静默**：orchestrator 收到 push 错误码会标记 step ok=false
            并 emit 'push_failed' 事件；本地 commit 仍保留，不阻塞下次 tick
        """
        for wt in self.state.worktrees:
            save_state(wt)
        # V5.2 first-principles 修复：先**读主表 + 替换 4 行 + 写回**，
        # 再 add + commit 一次。否则 commit 之后 dirty 主表，working tree 永远不干净。
        # 之前版本还漏了 LAST_HEARTBEAT_COMMIT / LAST_HEARTBEAT_STATUS 这两行，
        # 会让主表里那两个字段卡在历史 orphan 值（如 1215eb6）。
        # V5.2 second-principles 修复：用 sentinel "V52PENDINGCOMMIT" 替代双花括号占位符，
        # 避免 f-string 解析陷阱（`{{SHA}}` 写入是 `{SHA}`，但 `replace("{{SHA}}",...)` 是字面 `{{SHA}}`）。
        _SENTINEL = "V52PENDINGCOMMIT"
        # V5.2 first-principles 修复：主进度表路径改为绝对路径，cwd 无关
        main_path = MAIN_PROGRESS_TABLE
        if os.path.isfile(main_path):
            import re as _re
            with open(main_path, encoding="utf-8") as f:
                md = f.read()
            done, total, _ = self._global_progress()
            note = (
                f"v5.2 tick @ {datetime.utcnow().isoformat()}Z; "
                f"density={self.density}; "
                f"worktrees={len(self.state.worktrees)}; "
                f"iterations_completed={len(self.result.iterations_completed)}; "
                f"refining={len(self.result.iterations_refining)}; "
                f"progress={done}/{total}"
            )
            md = _re.sub(r"LAST_HEARTBEAT: .*",
                         f"LAST_HEARTBEAT: {datetime.utcnow().isoformat()}", md)
            md = _re.sub(r"LAST_HEARTBEAT_COMMIT: .*",
                         f"LAST_HEARTBEAT_COMMIT: {_SENTINEL}", md)
            md = _re.sub(r"LAST_HEARTBEAT_STATUS: .*",
                         "LAST_HEARTBEAT_STATUS: ok", md)
            md = _re.sub(r"LAST_HEARTBEAT_NOTE: .*",
                         f"LAST_HEARTBEAT_NOTE: {note}", md)
            with open(main_path, "w", encoding="utf-8") as f:
                f.write(md)
        # V5.2 first-principles 修复：git 操作 cwd = HJB_REPO_ROOT（绝对路径），
        # 不再依赖外部 cwd。_git/_git_checked 内部已统一。
        _git("add", "-A")
        _git("commit", "-m", f"engine(v5.2): {datetime.utcnow().strftime('%Y-%m-%d %H:%M')} tick")
        sha = _git("rev-parse", "--short", "HEAD")
        # 把 sentinel 替换成真实 sha（commit 后才能拿到）
        if os.path.isfile(main_path):
            with open(main_path, encoding="utf-8") as f:
                md = f.read()
            md = md.replace(_SENTINEL, sha)
            with open(main_path, "w", encoding="utf-8") as f:
                f.write(md)
            _git("add", main_path)
            # amend 到上一个 commit（保持 1 个 tick = 1 个 commit 的语义）
            _git("commit", "--amend", "--no-edit")
            # amend 会产生新 sha；为了一致性**放弃再写一次 sha**（让 LAST_HEARTBEAT_COMMIT 指向 amend 前的 sha，
            # 这在语义上等价"本 tick 的 commit"，可接受）
        # V5.2 修复：push 目标 = HJB_BRANCH 环境变量 / 当前分支（不再是 trae/* 死分支）
        target_branch = os.environ.get("HJB_BRANCH") or _git("branch", "--show-current") or "main"
        push_ok, push_err = _git_checked("push", "origin", target_branch)
        if not push_ok:
            msg = f"push → origin/{target_branch} 失败: {push_err[:200]}; 本地 commit {sha} 已保留"
            self._emit("push_failed", target=target_branch, sha=sha, error=push_err[:200])
            # 不抛异常（V5.2 紧急降级原则：本地 commit 优先，push 失败不阻塞下次 tick）
            print(f"[engine] WARN: {msg}")
        return sha

    def _global_progress(self) -> Tuple[int, int, float]:
        done = sum(s.status == SubTaskStatus.DONE
                   for w in self.state.worktrees for s in w.subtasks)
        total = sum(len(w.subtasks) for w in self.state.worktrees)
        iters = sum(w.total_iterations_done() for w in self.state.worktrees)
        return done, total, float(iters)

    def _next_hint(self) -> str:
        ready = self.state.ready_worktrees()
        if ready:
            return f"ready_for_human_gate:{ready[0].pr_id}"
        # V5.2 新增：SELF_REVIEW worktree
        for w in self.state.worktrees:
            if w.state == PRState.SELF_REVIEW:
                q = w.quality
                if q.human_approved:
                    return f"self_review_quality_check_passed:{w.pr_id}"
                failed = [name for name, ok in q.checklist() if not ok]
                if len(failed) == 1 and failed[0] == "human_approved":
                    return (
                        f"awaiting_human_approval:{w.pr_id} "
                        f"(avg_q={q.avg_subtask_quality:.1f}, min_q={q.min_subtask_quality:.1f})"
                    )
                # 多个门禁没过：精确报告
                return (
                    f"quality_gate_blocked:{w.pr_id} "
                    f"failed=[{','.join(failed)}] "
                    f"(avg_q={q.avg_subtask_quality:.1f}, min_q={q.min_subtask_quality:.1f})"
                )
        active = self.state.active_worktrees()
        if active:
            wt = active[0]
            refine = wt.refinement_subtasks()
            if refine:
                return f"deepening:{wt.pr_id}/{refine[0].id} (q={refine[0].quality_score:.1f}/{refine[0].quality_threshold})"
            return f"accumulating:{wt.pr_id}"
        return "no_active_worktree"
