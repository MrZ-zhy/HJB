"""V5.1 核心模型。

V5.1 核心理念：
  - PR 是"积累型工件"，**单 tick 只推进 0.03-0.1 个 PR**
  - 1 个 PR 由 10-30 个 atomic sub-tasks 组成
  - 1 tick 完成 1-3 个 sub-tasks
  - 1 PR 通常需要 2-4 周持续推进
  - 中断后可从 sub-task N+1 恢复
  - PR 必须 100% sub-task + 100% test pass + 100% lint + self-critique pass 才能 submit
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field, asdict
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional


# ──────────────────────────────────────────────────────────────
# Sub-task 类型（V5.1 一等公民）
# ──────────────────────────────────────────────────────────────

class SubTaskType(str, Enum):
    # 论文吸收类
    READ_PAPER = "read_paper"            # 读论文某 section，输出笔记
    EXTRACT_CONTRACT = "extract_contract"  # 从论文提取 API/数据结构契约
    # 代码分析类
    ANALYZE_CODE = "analyze_code"        # 读代码并写分析报告
    CROSS_CHECK = "cross_check"          # 论文 vs 代码 一致性检查
    # 编写类
    WRITE_TEST = "write_test"            # 写 unit test
    WRITE_DOCSTRING = "write_docstring"  # 写 docstring + 引用
    WRITE_CITATION = "write_citation"    # 写 bibtex / pubs.md
    WRITE_PR_BODY = "write_pr_body"      # 写 PR description
    # 验证类
    VERIFY_TESTS = "verify_tests"        # 跑测试
    VERIFY_LINT = "verify_lint"          # 跑 lint/type
    VERIFY_BUILD = "verify_build"        # 跑 build
    # 质量类
    SELF_CRITIQUE = "self_critique"      # 自我批评：是否真解决了 paper 提的问题
    # 持久化类
    PERSIST = "persist"                  # 原子写 + commit
    # Meta
    BLOCKED = "blocked"                  # 等待外部（人类评审 / arXiv 出版 / 数据）


class SubTaskStatus(str, Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    DONE = "done"
    FAILED = "failed"
    BLOCKED = "blocked"


# ──────────────────────────────────────────────────────────────
# PR 状态（V5.1 扩展自 V5）
# ──────────────────────────────────────────────────────────────

class PRState(str, Enum):
    BACKLOG = "backlog"
    DECOMPOSING = "decomposing"          # V5.1 新增：正在分解 sub-tasks
    ACCUMULATING = "accumulating"        # V5.1 核心：积累中
    SELF_REVIEW = "self_review"          # V5.1 新增：自批评阶段
    READY_TO_SUBMIT = "ready_to_submit"  # V5.1 新增：100% ready 等人类 gate
    AWAITING_GATE = "awaiting_gate"      # 人类 gate
    PR_SUBMITTING = "pr_submitting"
    SUBMITTED = "submitted"
    REVISION = "revision"
    MERGED = "merged"
    CLOSED = "closed"
    STALLED = "stalled"


# ──────────────────────────────────────────────────────────────
# Paper（V5.1 简化：用于 read_paper handler）
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
# SubTask
# ──────────────────────────────────────────────────────────────

@dataclass
class SubTask:
    """1 个 PR 内的 atomic sub-task。"""
    id: str                              # st-001
    pr_id: str                           # pr-gym-torax-2510.11283
    type: SubTaskType
    description: str
    depends_on: List[str] = field(default_factory=list)
    status: SubTaskStatus = SubTaskStatus.PENDING
    output_files: List[str] = field(default_factory=list)
    verification: str = ""               # 如何验证这个 sub-task 完成
    estimated_ticks: int = 1             # 预估需要几个 tick
    actual_ticks: int = 0
    started_at: str = ""
    finished_at: str = ""
    notes: str = ""

    def is_ready(self, all_tasks: Dict[str, "SubTask"]) -> bool:
        """依赖都完成才 ready。"""
        return all(all_tasks[d].status == SubTaskStatus.DONE for d in self.depends_on)

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
    """PR 提交前的硬性质量门槛（V5.1 强制）。"""
    all_subtasks_done: bool = False
    tests_pass: bool = False
    lint_pass: bool = False
    type_check_pass: bool = False
    self_critique_pass: bool = False
    paper_cited: bool = False
    pr_body_complete: bool = False
    human_approved: bool = False          # 最终关卡

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
        ])

    def checklist(self) -> List[tuple]:
        return [
            ("all_subtasks_done", self.all_subtasks_done),
            ("tests_pass", self.tests_pass),
            ("lint_pass", self.lint_pass),
            ("type_check_pass", self.type_check_pass),
            ("self_critique_pass", self.self_critique_pass),
            ("paper_cited", self.paper_cited),
            ("pr_body_complete", self.pr_body_complete),
            ("human_approved", self.human_approved),
        ]

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# ──────────────────────────────────────────────────────────────
# PRWorktree（V5.1 核心：1 PR = 1 持久化工件）
# ──────────────────────────────────────────────────────────────

@dataclass
class PRWorktree:
    """1 个 PR 的完整工作区：sub-tasks + quality + 进度。"""
    pr_id: str                           # pr-gym-torax-2510.11283
    project: str                         # gym-torax
    paper_arxiv_id: str                  # 2510.11283
    paper_title: str
    pr_type: str                         # T1 / T2 / T5
    target_repo: str                     # antoine-mouchamps/gymtorax
    target_files: List[str]              # 计划改的文件
    state: PRState = PRState.BACKLOG
    created_at: str = ""
    updated_at: str = ""
    subtasks: List[SubTask] = field(default_factory=list)
    quality: QualityCriteria = field(default_factory=QualityCriteria)
    notes_dir: str = ""                  # V5_1/WORKTREES/<pr_id>/notes/
    pr_branch: str = ""                  # git branch name
    pr_number: Optional[int] = None
    pr_url: str = ""
    estimated_total_ticks: int = 0

    def subtask_by_id(self, sid: str) -> Optional[SubTask]:
        for s in self.subtasks:
            if s.id == sid:
                return s
        return None

    def pending_ready_subtasks(self) -> List[SubTask]:
        """返回所有 PENDING 且依赖都满足的 sub-tasks。"""
        all_dict = {s.id: s for s in self.subtasks}
        return [
            s for s in self.subtasks
            if s.status == SubTaskStatus.PENDING and s.is_ready(all_dict)
        ]

    def progress(self) -> tuple:
        """(done_count, total_count, percent)"""
        total = len(self.subtasks)
        if total == 0:
            return 0, 0, 0.0
        done = sum(1 for s in self.subtasks if s.status == SubTaskStatus.DONE)
        return done, total, done / total

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["state"] = self.state.value
        d["subtasks"] = [s.to_dict() for s in self.subtasks]
        d["quality"] = self.quality.to_dict()
        return d


# ──────────────────────────────────────────────────────────────
# EngineState（V5.1）
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
    """V5.1 状态总览。"""
    version: str = "5.1"
    timestamp: str = ""
    strategy_mode: str = "accumulating"  # V5.1 强制 accumulating
    worktrees: List[PRWorktree] = field(default_factory=list)
    queue: List[str] = field(default_factory=list)  # PR_IDs 待启动
    head_commit: str = ""
    last_heartbeat: str = ""
    last_heartbeat_status: str = "ok"
    last_heartbeat_note: str = ""
    iron_laws_version: str = "v5.1-1"
    total_ticks: int = 0
    total_subtasks_done: int = 0

    def worktree_by_id(self, pid: str) -> Optional[PRWorktree]:
        for w in self.worktrees:
            if w.pr_id == pid:
                return w
        return None

    def active_worktrees(self) -> List[PRWorktree]:
        """返回正在 ACCUMULATING 的 worktrees（V5.1 主战场）。"""
        return [w for w in self.worktrees if w.state == PRState.ACCUMULATING]

    def ready_worktrees(self) -> List[PRWorktree]:
        """返回 100% ready 等人类 gate 的 worktrees。"""
        return [w for w in self.worktrees if w.state == PRState.READY_TO_SUBMIT]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "version": self.version,
            "timestamp": self.timestamp,
            "strategy_mode": self.strategy_mode,
            "worktrees": [w.to_dict() for w in self.worktrees],
            "queue": self.queue,
            "head_commit": self.head_commit,
            "last_heartbeat": self.last_heartbeat,
            "last_heartbeat_status": self.last_heartbeat_status,
            "last_heartbeat_note": self.last_heartbeat_note,
            "iron_laws_version": self.iron_laws_version,
            "total_ticks": self.total_ticks,
            "total_subtasks_done": self.total_subtasks_done,
        }
