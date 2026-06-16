"""V5.2 Quality Gate：PR 必须满足全部硬性门禁 + 质量分门槛。

V5.2 升级（核心）：
  - 8 项 V5.1 硬性门禁（保留）
  - 新增 avg_subtask_quality / min_subtask_quality 门禁：
      * avg_subtask_quality >= 75（所有 DONE sub-task 平均分）
      * min_subtask_quality >= 60（最低分不低于 60）
  - 如果有 FAILED sub-task（max_iter 用完 quality 仍 < threshold）：
      * 不计入 avg/min 分母（避免一个失败拖累整个 PR）
      * 但必须警告（warning，而非 hard fail）
"""
from __future__ import annotations

from typing import List, Tuple

from core.models import PRWorktree, QualityCriteria, SubTaskStatus


def evaluate(wt: PRWorktree) -> QualityCriteria:
    """从 PRWorktree 状态推断 QualityCriteria（V5.2：含质量分统计）。

    V5.2 修复（first-principles bug）：
      原实现无脑要求所有 PR 都有 verify_tests 子任务。
      但 T2(docstring-only)/T5(citation-only) 这两种 PR 类型根本没
      设计 verify_tests 子任务（DAG 模板里就没这一项），导致
      `tests_pass` 永远 = False，PR 永远卡在 self_review。
      修法：按 pr_type 决定 `tests_pass` 语义：
        - T1（代码 + 测试）：必须有 verify_tests 子任务且 exit=0
        - T2（docstring）/T5（citation）：tests_pass 自动 True（设计上不要求）
    """
    q = QualityCriteria()

    if wt.subtasks:
        q.all_subtasks_done = all(
            s.status == SubTaskStatus.DONE for s in wt.subtasks
        )
    else:
        q.all_subtasks_done = False

    # verify 类
    pr_type = (wt.pr_type or "").upper()
    if pr_type == "T1":
        test_st = next((s for s in wt.subtasks
                        if s.type.value == "verify_tests" and s.status == SubTaskStatus.DONE), None)
        q.tests_pass = test_st is not None and "exit=0" in (test_st.notes or "")
    else:
        # T2 / T5：设计上不要求 verify_tests 子任务；tests_pass 自动 True
        q.tests_pass = True

    lint_st = next((s for s in wt.subtasks
                    if s.type.value == "verify_lint" and s.status == SubTaskStatus.DONE), None)
    q.lint_pass = lint_st is not None and "exit=0" in (lint_st.notes or "")
    q.type_check_pass = q.lint_pass  # V5.2 沿用 V5.1 简化

    crit_st = next((s for s in wt.subtasks
                    if s.type.value == "self_critique" and s.status == SubTaskStatus.DONE), None)
    q.self_critique_pass = crit_st is not None

    cite_st = next((s for s in wt.subtasks
                    if s.type.value == "write_citation" and s.status == SubTaskStatus.DONE), None)
    doc_st = next((s for s in wt.subtasks
                   if s.type.value == "write_docstring" and s.status == SubTaskStatus.DONE), None)
    q.paper_cited = cite_st is not None or doc_st is not None

    pr_st = next((s for s in wt.subtasks
                  if s.type.value == "write_pr_body" and s.status == SubTaskStatus.DONE), None)
    q.pr_body_complete = pr_st is not None

    # V5.2：统计 quality_score
    done_qualities: List[float] = []
    for s in wt.subtasks:
        if s.status == SubTaskStatus.DONE and s.quality_score > 0:
            done_qualities.append(s.quality_score)
    if done_qualities:
        q.avg_subtask_quality = sum(done_qualities) / len(done_qualities)
        q.min_subtask_quality = min(done_qualities)
    else:
        q.avg_subtask_quality = 0.0
        q.min_subtask_quality = 0.0

    return q


def checklist_text(q: QualityCriteria) -> str:
    """返回 10 项 checklist 文本（V5.2：8 项 V5.1 + 2 项 V5.2）。"""
    rows: List[str] = []
    for name, ok in q.checklist():
        mark = "✅" if ok else "❌"
        rows.append(f"- [{mark}] **{name}**")
    return "\n".join(rows)


def quality_summary(wt: PRWorktree) -> dict:
    """返回 worktree 级别的质量摘要（含 V5.2 字段）。"""
    done_qualities = [
        s.quality_score for s in wt.subtasks
        if s.status == SubTaskStatus.DONE
    ]
    failed_count = sum(1 for s in wt.subtasks if s.status == SubTaskStatus.FAILED)
    refining_count = sum(1 for s in wt.subtasks if s.needs_refinement())
    return {
        "pr_id": wt.pr_id,
        "subtasks_total": len(wt.subtasks),
        "subtasks_done": sum(1 for s in wt.subtasks if s.status == SubTaskStatus.DONE),
        "subtasks_failed": failed_count,
        "subtasks_refining": refining_count,
        "avg_quality": sum(done_qualities) / len(done_qualities) if done_qualities else 0.0,
        "min_quality": min(done_qualities) if done_qualities else 0.0,
        "max_quality": max(done_qualities) if done_qualities else 0.0,
        "total_iterations": sum(s.iterations_done for s in wt.subtasks),
        "is_ready_to_submit": wt.quality.is_ready_to_submit(),
    }
