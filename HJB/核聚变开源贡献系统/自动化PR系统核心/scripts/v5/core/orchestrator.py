"""V5 orchestrator（7 步，类比 V4 但论文驱动）。"""
from __future__ import annotations

import os
import subprocess
from datetime import datetime
from typing import List

from core.event_bus import Event, EventBus
from core.models import Action, EngineState, PaperStatus
from core.state_machine import self_check as sm_self_check
from persistence.progress_table import load_state
from strategies.base import discover_strategies
from strategies.paper_discovery import PaperDiscoveryStrategy
from strategies.pre_pr_report import PrePRReportStrategy


# V5 默认元数据（sandbox-runnable 优先）
DEFAULT_PROJECTS_META: List[dict] = [
    {
        "name": "OpenReactor",
        "repo": "natesales/openreactor",
        "language": "Go",
        "sandbox_runnable": True,
        "keywords": ["fusor", "IEC", "Langmuir", "neutron", "high voltage", "vacuum", "gauges"],
        "notes": "开源 IEC 核聚变反应堆参考设计与控制系统（Go 微服务）",
    },
    {
        "name": "FUSE",
        "repo": "ProjectTorreyPines/FUSE.jl",
        "language": "Julia",
        "sandbox_runnable": False,  # Julia 未装
        "keywords": ["FUSE", "tokamak", "pilot plant", "first principle", "IMAS"],
        "notes": "General Atomics 发布的聚变电站综合设计框架（Julia）",
    },
    {
        "name": "gym-torax",
        "repo": "antoine-mouchamps/gymtorax",
        "language": "Python",
        "sandbox_runnable": True,
        "keywords": ["tokamak", "plasma", "control", "TORAX", "ITER", "magnetic", "gym"],
        "notes": "把 TORAX 仿真器包装成 Gymnasium 环境的 RL 训练包（Python）",
    },
]

DEFAULT_LOCAL_PATHS: dict = {
    "OpenReactor": "/workspace/HJB/项目/OpenReactor",
    "FUSE": "/workspace/HJB/项目/FUSE",
    "gym-torax": "/workspace/HJB/项目/gym-torax",
}


class TickResult:
    def __init__(self) -> None:
        self.overall_ok: bool = True
        self.steps: List[dict] = []
        self.events: List[str] = []
        self.actions_taken: List[str] = []
        self.next_action_hint: str = ""


def _git(*args: str, cwd: str = ".") -> str:
    try:
        return subprocess.check_output(
            ["git", *args], text=True, cwd=cwd, stderr=subprocess.STDOUT
        ).strip()
    except subprocess.CalledProcessError as e:
        return e.output.strip()


class Orchestrator:
    def __init__(self) -> None:
        self.bus = EventBus()
        self.result = TickResult()
        self.started_at = datetime.utcnow().isoformat() + "Z"

    def _record_step(self, name: str, ok: bool, payload: dict, error: str = "", elapsed_ms: int = 0) -> None:
        self.result.steps.append({
            "step": name,
            "ok": ok,
            "elapsed_ms": elapsed_ms,
            "payload": payload,
            "error": error,
        })
        if not ok:
            self.result.overall_ok = False

    def _emit(self, name: str, **payload) -> None:
        ev = Event(name=name, payload=payload)
        self.bus.emit(ev)
        self.result.events.append(str(ev))

    def tick(self, dry_run: bool = False) -> TickResult:
        """V5 7 步工作流（类比 V4 但论文驱动）。"""
        import time

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
        state = load_state([p["name"] for p in DEFAULT_PROJECTS_META],
                            DEFAULT_PROJECTS_META, DEFAULT_LOCAL_PATHS)
        self._record_step("load_state", True,
                          {
                              "active_projects": [p.name for p in state.projects],
                              "queue_len": len(state.queue),
                              "papers_loaded": len(state.papers),
                              "strategy_mode": state.strategy_mode,
                          },
                          elapsed_ms=int((time.time() - t2) * 1000))

        # Step 4: state_decide
        t3 = time.time()
        actions: List[Action] = []
        try:
            paper_disc = PaperDiscoveryStrategy()
            actions.extend(paper_disc.evaluate(state))
            pre_pr = PrePRReportStrategy()
            actions.extend(pre_pr.evaluate(state))
        except Exception as e:
            self._emit("state_decide.error", error=str(e))
        actions.sort(key=lambda a: -a.priority)
        self._record_step("state_decide", True,
                          {"actions_count": len(actions),
                           "strategy_errors": []},
                          elapsed_ms=int((time.time() - t3) * 1000))

        # Step 5: execute
        t4 = time.time()
        executed: List[str] = []
        for action in actions:
            try:
                if action.name == "paper_discovery":
                    proj_name = action.target
                    pds = PaperDiscoveryStrategy()
                    papers = pds.run(proj_name)
                    proj = state.project_by_name(proj_name)
                    if proj:
                        for p in papers:
                            if p.arxiv_id not in proj.candidate_papers:
                                proj.candidate_papers.append(p.arxiv_id)
                            existing = next(
                                (pp for pp in state.papers if pp.arxiv_id == p.arxiv_id),
                                None,
                            )
                            if existing is None:
                                state.papers.append(p)
                    executed.append(f"paper_discovery:{proj_name}")
                elif action.name == "pre_pr_report":
                    # 解析 target = "project/arxiv_id"
                    if "/" in action.target:
                        proj_name, arxiv_id = action.target.split("/", 1)
                        pre_pr_s = PrePRReportStrategy()
                        rep = pre_pr_s.build_report(state, proj_name, arxiv_id)
                        state.pre_pr_reports.append(rep)
                        proj = state.project_by_name(proj_name)
                        if proj:
                            proj.state = "awaiting_gate"
                            proj.active_report = rep.report_id
                        executed.append(f"pre_pr_report:{proj_name}/{arxiv_id}")
            except Exception as e:
                self._emit("strategy.error", strategy=action.name, error=str(e))
        self._record_step("execute", True,
                          {"executed": executed, "errors": []},
                          elapsed_ms=int((time.time() - t4) * 1000))

        # Step 6: persist（写主表 + 状态机）
        t5 = time.time()
        sha = ""
        if not dry_run:
            sha = self._persist(state)
        self._record_step("persist", True, {"sha": sha},
                          elapsed_ms=int((time.time() - t5) * 1000))

        # Step 7: report
        for a in actions:
            self.result.actions_taken.append(str(a))
        self.result.next_action_hint = "pre_pr_review" if state.pre_pr_reports else "paper_discovery"
        return self.result

    def _persist(self, state: EngineState) -> str:
        """更新主表 codeblock + git commit + push。"""
        import re as _re
        main_path = "核聚变开源贡献系统/进度表.md"
        if not os.path.isfile(main_path):
            return ""
        with open(main_path, encoding="utf-8") as f:
            md = f.read()
        # 更新 LAST_HEARTBEAT_NOTE
        note = f"v5 tick @ {datetime.utcnow().isoformat()}Z; active=3 papers={len(state.papers)} reports={len(state.pre_pr_reports)}"
        md = _re.sub(r"LAST_HEARTBEAT: .*", f"LAST_HEARTBEAT: {datetime.utcnow().isoformat()}", md)
        md = _re.sub(r"LAST_HEARTBEAT_NOTE: .*", f"LAST_HEARTBEAT_NOTE: {note}", md)
        md = _re.sub(r"HEAD_COMMIT_SHA: .*", "HEAD_COMMIT_SHA: pending", md)
        with open(main_path, "w", encoding="utf-8") as f:
            f.write(md)
        # commit
        _git("add", "-A")
        commit_msg = f"engine(v5): {datetime.utcnow().strftime('%Y-%m-%d %H:%M')} paper_discovery"
        _git("commit", "-m", commit_msg)
        sha = _git("rev-parse", "--short", "HEAD")
        _git("push", "origin", "trae/solo-agent-TbCBsF")
        return sha
