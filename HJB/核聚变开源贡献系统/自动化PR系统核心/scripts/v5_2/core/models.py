"""V5.2 核心模型。

V5.2 核心理念（升级自 V5.1）：
  - V5.1: 1 sub-task = 1 tick（浅）
  - **V5.2: 1 sub-task = N ticks（迭代深化）**

  调用系统 10 次不会让 PR 数量变 10，但**会让同一个 sub-task 的 quality_score 更高**。
  1 sub-task 的 handler 可被调用 max_iterations 次，每次调用都基于前一次的输出继续深化。
  每次 tick 默认"细化现有 sub-task"（质量未达标）> "新开 sub-task"（依赖已就绪）。

PR 仍是积累型工件：1 PR = 10-30 sub-tasks；2-4 周周期。
每次 tick = 1 次 sub-task + 1 次 iteration（**不是** 1 PR，也不是 1 sub-task 完成）。
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field, asdict
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional


# ──────────────────────────────────────────────────────────────
# Compute Density 模式（V5.2 新增）
# ──────────────────────────────────────────────────────────────

class ComputeDensity(str, Enum):
    QUICK = "quick"        # 1 sub-task/tick, max_iter=1 (V5.1 兼容模式)
    DEFAULT = "default"    # 1 sub-task/tick, max_iter=type default (推荐)
    DEEP = "deep"          # 1 sub-task/tick, max_iter=type default × 2
    BURST = "burst"        # 3 sub-tasks/tick, max_iter=1 each (高吞吐)


# ──────────────────────────────────────────────────────────────
# Sub-task 类型（V5.2 沿用 V5.1）
# ──────────────────────────────────────────────────────────────

class SubTaskType(str, Enum):
    READ_PAPER = "read_paper"
    EXTRACT_CONTRACT = "extract_contract"
    ANALYZE_CODE = "analyze_code"
    CROSS_CHECK = "cross_check"
    WRITE_TEST = "write_test"
    WRITE_DOCSTRING = "write_docstring"
    WRITE_CITATION = "write_citation"
    WRITE_PR_BODY = "write_pr_body"
    VERIFY_TESTS = "verify_tests"
    VERIFY_LINT = "verify_lint"
    VERIFY_BUILD = "verify_build"
    SELF_CRITIQUE = "self_critique"
    PERSIST = "persist"
    BLOCKED = "blocked"


class SubTaskStatus(str, Enum):
    PENDING = "pending"            # 未开始 OR 已开始但 quality 未达 threshold（需要更多 iteration）
    IN_PROGRESS = "in_progress"    # 正在执行某次 iteration
    DONE = "done"                  # quality >= threshold（最终完成）
    FAILED = "failed"              # max_iterations 用完但 quality 仍 < threshold
    BLOCKED = "blocked"            # 外部阻塞


# ──────────────────────────────────────────────────────────────
# PR 状态（V5.2 沿用 V5.1 的 13 状态）
# ──────────────────────────────────────────────────────────────

class PRState(str, Enum):
    BACKLOG = "backlog"
    DECOMPOSING = "decomposing"
    ACCUMULATING = "accumulating"
    SELF_REVIEW = "self_review"
    READY_TO_SUBMIT = "ready_to_submit"
    AWAITING_GATE = "awaiting_gate"
    PR_SUBMITTING = "pr_submitting"
    SUBMITTED = "submitted"
    REVISION = "revision"
    MERGED = "merged"
    CLOSED = "closed"
    STALLED = "stalled"


# ──────────────────────────────────────────────────────────────
# RefinementRecord（V5.2 新增：每次 iteration 的历史）
# ──────────────────────────────────────────────────────────────

@dataclass
class RefinementRecord:
    """1 次 sub-task iteration 的完整记录。"""
    iteration: int                 # 0-indexed
    started_at: str = ""
    finished_at: str = ""
    output_files_written: List[str] = field(default_factory=list)
    output_summary: str = ""       # 简述这次迭代干了什么
    quality_score: float = 0.0     # 0-100
    compute_used: str = ""         # "low" / "medium" / "high"（手填）
    notes: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# ──────────────────────────────────────────────────────────────
# SubTask（V5.2 升级：iterations + quality）
# ──────────────────────────────────────────────────────────────

# 每 sub-task 类型的默认参数（V5.2：迭代次数 + 质量门槛）
DEFAULT_PARAMS: Dict[str, dict] = {
    # type: {max_iterations, quality_threshold, verify_prompt}
    "read_paper":      {"max_iterations": 3, "quality_threshold": 70.0,
                         "verify_prompt": "是否覆盖 abstract + method + experimental 三节？"},
    "extract_contract": {"max_iterations": 2, "quality_threshold": 75.0,
                          "verify_prompt": "是否列出 ≥ 3 个 API/数据结构契约？"},
    "analyze_code":    {"max_iterations": 4, "quality_threshold": 75.0,
                         "verify_prompt": "是否列出 ≥ 5 个 function 签名 + 物理模块边界？"},
    "cross_check":     {"max_iterations": 3, "quality_threshold": 80.0,
                         "verify_prompt": "是否给出 paper 提/未提 vs code 有/无的双向对比？"},
    "write_test":      {"max_iterations": 2, "quality_threshold": 75.0,
                         "verify_prompt": "是否有 ≥ 1 test function + oracle 断言？"},
    "write_docstring": {"max_iterations": 2, "quality_threshold": 70.0,
                         "verify_prompt": "是否含 1+ 公式 + 1+ 论文引用？"},
    "write_citation":  {"max_iterations": 1, "quality_threshold": 60.0,
                         "verify_prompt": "是否含完整 bibtex？"},
    "write_pr_body":   {"max_iterations": 3, "quality_threshold": 75.0,
                         "verify_prompt": "是否含背景/动机/改动/验证四段？"},
    "verify_tests":    {"max_iterations": 1, "quality_threshold": 60.0,
                         "verify_prompt": "exit=0 且所有 test passed？"},
    "verify_lint":     {"max_iterations": 1, "quality_threshold": 60.0,
                         "verify_prompt": "lint 0 error？"},
    "verify_build":    {"max_iterations": 1, "quality_threshold": 60.0,
                         "verify_prompt": "build exit 0？"},
    "self_critique":   {"max_iterations": 3, "quality_threshold": 75.0,
                         "verify_prompt": "是否含 3 段（解决了什么/可能问题/改进建议）？"},
    "persist":         {"max_iterations": 1, "quality_threshold": 50.0,
                         "verify_prompt": "state.json 已写 + git committed？"},
    "blocked":         {"max_iterations": 1, "quality_threshold": 100.0,
                         "verify_prompt": "需要人类/外部介入"},
}


@dataclass
class SubTask:
    id: str
    pr_id: str
    type: SubTaskType
    description: str
    depends_on: List[str] = field(default_factory=list)
    status: SubTaskStatus = SubTaskStatus.PENDING
    output_files: List[str] = field(default_factory=list)
    verification: str = ""
    estimated_ticks: int = 1
    actual_ticks: int = 0
    started_at: str = ""
    finished_at: str = ""
    notes: str = ""
    # V5.2 新增
    max_iterations: int = 1
    iterations_done: int = 0
    quality_score: float = 0.0
    quality_threshold: float = 70.0
    refinement_history: List[RefinementRecord] = field(default_factory=list)
    verify_prompt: str = ""

    def is_ready(self, all_tasks: Dict[str, "SubTask"]) -> bool:
        return all(all_tasks[d].status == SubTaskStatus.DONE for d in self.depends_on)

    def needs_refinement(self) -> bool:
        """是否需要细化（V5.2 核心判断）。"""
        return (
            self.status == SubTaskStatus.PENDING
            and self.iterations_done > 0
            and self.quality_score < self.quality_threshold
            and self.iterations_done < self.max_iterations
        )

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["type"] = self.type.value
        d["status"] = self.status.value
        return d


# ──────────────────────────────────────────────────────────────
# QualityCriteria
# ──────────────────────────────────────────────────────────────

@dataclass
class QualityCriteria:
    all_subtasks_done: bool = False
    tests_pass: bool = False
    lint_pass: bool = False
    type_check_pass: bool = False
    self_critique_pass: bool = False
    paper_cited: bool = False
    pr_body_complete: bool = False
    human_approved: bool = False
    # V5.2 新增
    avg_subtask_quality: float = 0.0  # 所有 DONE sub-task 的平均 quality_score
    min_subtask_quality: float = 0.0  # 最低的 sub-task quality_score

    def is_ready_to_submit(self) -> bool:
        return all([
            self.all_subtasks_done,
            self.tests_pass,
            self.lint_pass,
            self.type_check_pass,
            self.self_critique_pass,
            self.paper_cited,
            self.pr_body_complete,
            self.human_approved,
            # V5.2 额外门禁：平均质量 ≥ 75，最低 ≥ 60
            self.avg_subtask_quality >= 75.0,
            self.min_subtask_quality >= 60.0,
        ])

    def checklist(self) -> List[tuple]:
        base = [
            ("all_subtasks_done", self.all_subtasks_done),
            ("tests_pass", self.tests_pass),
            ("lint_pass", self.lint_pass),
            ("type_check_pass", self.type_check_pass),
            ("self_critique_pass", self.self_critique_pass),
            ("paper_cited", self.paper_cited),
            ("pr_body_complete", self.pr_body_complete),
            ("human_approved", self.human_approved),
            ("avg_subtask_quality>=75", self.avg_subtask_quality >= 75.0),
            ("min_subtask_quality>=60", self.min_subtask_quality >= 60.0),
        ]
        return base

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# ──────────────────────────────────────────────────────────────
# PRWorktree
# ──────────────────────────────────────────────────────────────

@dataclass
class PRWorktree:
    pr_id: str
    project: str
    paper_arxiv_id: str
    paper_title: str
    pr_type: str
    target_repo: str
    target_files: List[str]
    state: PRState = PRState.BACKLOG
    created_at: str = ""
    updated_at: str = ""
    subtasks: List[SubTask] = field(default_factory=list)
    quality: QualityCriteria = field(default_factory=QualityCriteria)
    notes_dir: str = ""
    pr_branch: str = ""
    pr_number: Optional[int] = None
    pr_url: str = ""
    estimated_total_ticks: int = 0
    # V5.2 新增
    compute_density: str = "default"  # 当前 density 模式

    def subtask_by_id(self, sid: str) -> Optional[SubTask]:
        for s in self.subtasks:
            if s.id == sid:
                return s
        return None

    def pending_ready_subtasks(self) -> List[SubTask]:
        """返回 PENDING（首次） + 依赖都满足的 sub-tasks。"""
        all_dict = {s.id: s for s in self.subtasks}
        return [
            s for s in self.subtasks
            if s.status == SubTaskStatus.PENDING
            and s.iterations_done == 0
            and s.is_ready(all_dict)
        ]

    def refinement_subtasks(self) -> List[SubTask]:
        """V5.2 核心：需要细化的 sub-tasks（已迭代但 quality 未达标）。"""
        return [s for s in self.subtasks if s.needs_refinement()]

    def progress(self) -> tuple:
        total = len(self.subtasks)
        if total == 0:
            return 0, 0, 0.0
        done = sum(1 for s in self.subtasks if s.status == SubTaskStatus.DONE)
        return done, total, done / total

    def total_iterations_done(self) -> int:
        return sum(s.iterations_done for s in self.subtasks)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["state"] = self.state.value
        d["subtasks"] = [s.to_dict() for s in self.subtasks]
        d["quality"] = self.quality.to_dict()
        return d


# ──────────────────────────────────────────────────────────────
# Paper（用于 read_paper handler）
# ──────────────────────────────────────────────────────────────

@dataclass
class Paper:
    arxiv_id: str
    title: str
    authors: List[str]
    year: int
    summary: str
    primary_category: str
    pdf_url: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# ──────────────────────────────────────────────────────────────
# EngineState
# ──────────────────────────────────────────────────────────────

@dataclass
class Action:
    name: str
    priority: int
    target: str
    rationale: str

    def __str__(self) -> str:
        return f"Action({self.name}, pri={self.priority}, target={self.target}, why={self.rationale})"


@dataclass
class EngineState:
    version: str = "5.2"
    timestamp: str = ""
    strategy_mode: str = "iterative_deepening"  # V5.2 强制
    compute_density: str = "default"            # V5.2 新增
    worktrees: List[PRWorktree] = field(default_factory=list)
    queue: List[str] = field(default_factory=list)
    head_commit: str = ""
    last_heartbeat: str = ""
    last_heartbeat_status: str = "ok"
    last_heartbeat_note: str = ""
    iron_laws_version: str = "v5.2-1"
    total_ticks: int = 0
    total_iterations_done: int = 0  # V5.2 改名（原 total_subtasks_done）
    total_subtasks_done: int = 0

    def worktree_by_id(self, pid: str) -> Optional[PRWorktree]:
        for w in self.worktrees:
            if w.pr_id == pid:
                return w
        return None

    def active_worktrees(self) -> List[PRWorktree]:
        return [w for w in self.worktrees if w.state == PRState.ACCUMULATING]

    def ready_worktrees(self) -> List[PRWorktree]:
        return [w for w in self.worktrees if w.state == PRState.READY_TO_SUBMIT]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "version": self.version,
            "timestamp": self.timestamp,
            "strategy_mode": self.strategy_mode,
            "compute_density": self.compute_density,
            "worktrees": [w.to_dict() for w in self.worktrees],
            "queue": self.queue,
            "head_commit": self.head_commit,
            "last_heartbeat": self.last_heartbeat,
            "last_heartbeat_status": self.last_heartbeat_status,
            "last_heartbeat_note": self.last_heartbeat_note,
            "iron_laws_version": self.iron_laws_version,
            "total_ticks": self.total_ticks,
            "total_iterations_done": self.total_iterations_done,
            "total_subtasks_done": self.total_subtasks_done,
        }
