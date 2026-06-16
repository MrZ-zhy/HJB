"""V5 状态机模型（核心数据类）。

V5 的核心是 EngineState + Paper + Project + Action 四元组。
V4 的状态机被一阶化进 core/state_machine.py；V5 进一步把"论文→项目→PR"显式化。
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any, Dict, List, Optional


# ──────────────────────────────────────────────────────────────
# PR Type 分类（V5 新增，公理 A7：可插拔的 PR 类型）
# ──────────────────────────────────────────────────────────────

class PRType(str, Enum):
    T1 = "T1"  # 复现性单元测试（unit test from paper reference）
    T2 = "T2"  # 数学/算法文档增强（docstring + math derivation + citation）
    T3 = "T3"  # Issue 复现 + 根因分析（bug reproduction + bisect）
    T4 = "T4"  # Cross-validation 脚本（multi-code comparison）
    T5 = "T5"  # Citation 补全（scan for missing references + add bibtex）
    T6 = "T6"  # 新算法实现（high risk, usually skipped by AI）
    T7 = "T7"  # 纯 typo 修正（V4 风格，**V5 禁止**）


class PaperStatus(str, Enum):
    DISCOVERED = "discovered"      # 已发现，待去重
    COVERED = "covered"            # 上游已实现/引用
    PARTIAL = "partial"            # 部分覆盖
    GAP = "gap"                    # 上游未覆盖
    CLAIMED = "claimed"            # 已分配给某个 PR 草稿
    PR_DRAFTED = "pr_drafted"      # 已生成 pre-PR 报告，等待人工 gate
    PR_APPROVED = "pr_approved"    # 人工已批准
    PR_SUBMITTED = "pr_submitted"  # 已发 PR
    PR_MERGED = "pr_merged"
    PR_CLOSED = "pr_closed"
    PR_REJECTED = "pr_rejected"


class ProjectState(str, Enum):
    BACKLOG = "backlog"
    ANALYZING = "analyzing"
    PAPER_MATCHING = "paper_matching"
    PR_DRAFTING = "pr_drafting"
    AWAITING_GATE = "awaiting_gate"  # V5 新增：等人工 gate
    PR_SUBMITTING = "pr_submitting"
    SUBMITTED = "submitted"
    REVISION = "revision"
    MERGED = "merged"
    CLOSED = "closed"
    STALLED = "stalled"


# ──────────────────────────────────────────────────────────────
# 核心数据
# ──────────────────────────────────────────────────────────────

@dataclass
class Paper:
    """一篇 arXiv/期刊 论文。"""
    arxiv_id: str
    title: str
    authors: List[str]
    year: int
    summary: str
    primary_category: str
    pdf_url: str
    matched_projects: List[str] = field(default_factory=list)
    status: PaperStatus = PaperStatus.DISCOVERED
    coverage_notes: str = ""
    notes: str = ""

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["status"] = self.status.value
        return d


@dataclass
class PrePRReport:
    """Pre-PR 报告（人工 gate 的物料）。"""
    report_id: str
    project: str
    paper_arxiv_id: str
    paper_title: str
    pr_type: PRType
    target_files: List[str]
    rationale: str
    gap_analysis: str
    expected_impact: str
    risks: List[str]
    sandbox_runnable: bool
    approved: bool = False
    approved_at: str = ""
    rejection_reason: str = ""

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["pr_type"] = self.pr_type.value
        return d


@dataclass
class Project:
    """一个上游项目（OpenReactor/FUSE/gym-torax/...）。"""
    name: str
    repo: str
    language: str
    sandbox_runnable: bool
    local_path: str = ""
    state: ProjectState = ProjectState.BACKLOG
    keywords: List[str] = field(default_factory=list)
    candidate_papers: List[str] = field(default_factory=list)  # arxiv_ids
    active_report: Optional[str] = None  # report_id
    pr_number: Optional[int] = None
    pr_url: Optional[str] = None
    last_heartbeat: str = ""
    notes: str = ""

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["state"] = self.state.value
        return d


@dataclass
class Action:
    """V5 action（V4 兼容）。"""
    name: str
    priority: int
    target: str
    rationale: str

    def __str__(self) -> str:
        return f"Action({self.name}, pri={self.priority}, target={self.target}, why={self.rationale})"


@dataclass
class EngineState:
    """V5 状态总览。"""
    version: str = "5"
    timestamp: str = ""
    strategy_mode: str = "conservative"  # V5 强制 conservative
    projects: List[Project] = field(default_factory=list)
    papers: List[Paper] = field(default_factory=list)
    pre_pr_reports: List[PrePRReport] = field(default_factory=list)
    queue: List[str] = field(default_factory=list)  # 待启动项目
    head_commit: str = ""
    last_heartbeat: str = ""
    last_heartbeat_status: str = "ok"
    last_heartbeat_note: str = ""
    iron_laws_version: str = "v5-1"

    def project_by_name(self, name: str) -> Optional[Project]:
        for p in self.projects:
            if p.name == name:
                return p
        return None

    def report_by_id(self, rid: str) -> Optional[PrePRReport]:
        for r in self.pre_pr_reports:
            if r.report_id == rid:
                return r
        return None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "version": self.version,
            "timestamp": self.timestamp,
            "strategy_mode": self.strategy_mode,
            "projects": [p.to_dict() for p in self.projects],
            "papers": [pp.to_dict() for pp in self.papers],
            "pre_pr_reports": [r.to_dict() for r in self.pre_pr_reports],
            "queue": self.queue,
            "head_commit": self.head_commit,
            "last_heartbeat": self.last_heartbeat,
            "last_heartbeat_status": self.last_heartbeat_status,
            "last_heartbeat_note": self.last_heartbeat_note,
            "iron_laws_version": self.iron_laws_version,
        }
