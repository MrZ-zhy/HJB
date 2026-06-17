"""V5.1 Quality Gate：PR 必须满足全部 8 项才能提交。"""
from __future__ import annotations

import os
from typing import List

from core.models import PRWorktree, QualityCriteria, SubTaskStatus


def evaluate(wt: PRWorktree) -> QualityCriteria:
    """从 PRWorktree 状态推断 QualityCriteria。

    返回值：当前是否达标的 QualityCriteria
    """
    q = QualityCriteria()
    # 1. all_subtasks_done
    if wt.subtasks:
        q.all_subtasks_done = all(
            s.status == SubTaskStatus.DONE for s in wt.subtasks
        )
    # 2. tests_pass
    test_st = next((s for s in wt.subtasks
                    if s.type.value == "verify_tests" and s.status == SubTaskStatus.DONE), None)
    q.tests_pass = test_st is not None and "exit=0" in (test_st.notes or "")
    # 3. lint_pass
    lint_st = next((s for s in wt.subtasks
                    if s.type.value == "verify_lint" and s.status == SubTaskStatus.DONE), None)
    q.lint_pass = lint_st is not None and "exit=0" in (lint_st.notes or "")
    # 4. type_check_pass（沿用 lint_pass——V5.1 简化）
    q.type_check_pass = q.lint_pass
    # 5. self_critique_pass
    crit_st = next((s for s in wt.subtasks
                    if s.type.value == "self_critique" and s.status == SubTaskStatus.DONE), None)
    q.self_critique_pass = crit_st is not None
    # 6. paper_cited
    cite_st = next((s for s in wt.subtasks
                    if s.type.value == "write_citation" and s.status == SubTaskStatus.DONE), None)
    doc_st = next((s for s in wt.subtasks
                   if s.type.value == "write_docstring" and s.status == SubTaskStatus.DONE), None)
    q.paper_cited = cite_st is not None or doc_st is not None
    # 7. pr_body_complete
    pr_st = next((s for s in wt.subtasks
                  if s.type.value == "write_pr_body" and s.status == SubTaskStatus.DONE), None)
    q.pr_body_complete = pr_st is not None
    # 8. human_approved（外部标记）
    return q


def checklist_text(q: QualityCriteria) -> str:
    """返回 8 项 checklist 文本。"""
    rows = []
    for name, ok in q.checklist():
        mark = "✅" if ok else "❌"
        rows.append(f"- [{mark}] **{name}**")
    return "\n".join(rows)
