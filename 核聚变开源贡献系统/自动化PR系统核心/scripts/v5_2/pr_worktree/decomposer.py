"""V5.2 Sub-task Decomposer：把 (论文, 项目) 拆成 atomic sub-task DAG。

V5.2 升级自 V5.1：
  - 每个 sub-task 携带 max_iterations / quality_threshold（来自 DEFAULT_PARAMS）
  - 同 sub-task 可被多次迭代，handler 知道当前是第几次 iteration
"""
from __future__ import annotations

import os
from typing import Dict, List

from core.models import (
    DEFAULT_PARAMS, SubTask, SubTaskType, SubTaskStatus
)


def _st(idx: int, pr_id: str, type_: SubTaskType, desc: str, depends: List[str] = None,
        output: List[str] = None, verify: str = "", ticks: int = 1) -> SubTask:
    """构造 1 个 V5.2 SubTask，自动从 DEFAULT_PARAMS 取迭代参数。"""
    sid = f"st-{idx:03d}"
    type_value = type_.value
    params = DEFAULT_PARAMS.get(type_value, {
        "max_iterations": 1, "quality_threshold": 70.0,
        "verify_prompt": ""
    })
    return SubTask(
        id=sid,
        pr_id=pr_id,
        type=type_,
        description=desc,
        depends_on=depends or [],
        output_files=output or [],
        verification=verify or params["verify_prompt"],
        estimated_ticks=ticks,
        max_iterations=params["max_iterations"],
        quality_threshold=params["quality_threshold"],
        verify_prompt=params["verify_prompt"],
    )


# ──────────────────────────────────────────────────────────────
# T1 / T2 / T5 decomposer（沿用 V5.1 模板，加 V5.2 字段）
# ──────────────────────────────────────────────────────────────

def decompose_T1(pr_id: str, paper_id: str, project: str, test_dir: str) -> List[SubTask]:
    tasks: List[SubTask] = [
        _st(1, pr_id, SubTaskType.READ_PAPER,
            f"读 arXiv:{paper_id} abstract + 关键贡献", output=[f"WORKTREES/{pr_id}/notes/01-iter{0}-paper-abstract.md"], ticks=1),
        _st(2, pr_id, SubTaskType.EXTRACT_CONTRACT,
            f"从论文 method/api 段提取可测试 contract", depends=["st-001"],
            output=[f"WORKTREES/{pr_id}/notes/02-iter{0}-paper-contract.md"], ticks=1),
        _st(3, pr_id, SubTaskType.READ_PAPER,
            f"读 arXiv:{paper_id} experimental/validation 段，提取参考值/解析解", depends=["st-001"],
            output=[f"WORKTREES/{pr_id}/notes/03-iter{0}-paper-reference.md"], ticks=1),
        _st(4, pr_id, SubTaskType.ANALYZE_CODE,
            f"读 {project} 顶层结构，识别语言 + test 框架 + CI",
            output=[f"WORKTREES/{pr_id}/notes/04-iter{0}-code-overview.md"], ticks=1),
        _st(5, pr_id, SubTaskType.ANALYZE_CODE,
            f"读 {project} 目标源文件，列出所有 public function/class + 签名", depends=["st-004"],
            output=[f"WORKTREES/{pr_id}/notes/05-iter{0}-code-surface.md"], ticks=1),
        _st(6, pr_id, SubTaskType.CROSS_CHECK,
            f"交叉对比 paper contract (st-002) vs code surface (st-005)，找 gap", depends=["st-002", "st-005"],
            output=[f"WORKTREES/{pr_id}/notes/06-iter{0}-cross-check.md"], ticks=1),
        _st(7, pr_id, SubTaskType.WRITE_TEST,
            f"写 {test_dir}/test_<module>.py 骨架", depends=["st-006"],
            output=[f"{test_dir}/test_<module>.py"], ticks=1),
        _st(8, pr_id, SubTaskType.WRITE_TEST,
            "为 cross-check 找出的每个 gap 写 1 个 test case", depends=["st-007"],
            output=[f"{test_dir}/test_<module>.py"], ticks=2),
        _st(9, pr_id, SubTaskType.WRITE_TEST,
            "添加 oracle 断言：用 paper 参考值验证", depends=["st-003", "st-008"],
            output=[f"{test_dir}/test_<module>.py"], ticks=1),
        _st(10, pr_id, SubTaskType.WRITE_DOCSTRING,
            "为目标源文件顶部加 docstring 引用论文", depends=["st-005"],
            output=[], ticks=1),
        _st(11, pr_id, SubTaskType.VERIFY_TESTS,
            "本地跑 test 套件，全部 PASS", depends=["st-009"],
            output=[f"WORKTREES/{pr_id}/notes/11-iter{0}-test-run.log"], ticks=1),
        _st(12, pr_id, SubTaskType.VERIFY_LINT,
            "跑 lint + type check，0 error", depends=["st-010"],
            output=[f"WORKTREES/{pr_id}/notes/12-iter{0}-lint-run.log"], ticks=1),
        _st(13, pr_id, SubTaskType.SELF_CRITIQUE,
            "自我批评：PR 是否真解决了 paper 提的问题？", depends=["st-011", "st-012"],
            output=[f"WORKTREES/{pr_id}/notes/13-iter{0}-self-critique.md"], ticks=1),
        _st(14, pr_id, SubTaskType.WRITE_PR_BODY,
            "写 PR description：背景 + 动机 + 改动 + 验证", depends=["st-013"],
            output=[f"WORKTREES/{pr_id}/notes/14-iter{0}-pr-body.md"], ticks=1),
        _st(15, pr_id, SubTaskType.PERSIST,
            "原子 commit + push（本地）", depends=["st-014"],
            output=[f"WORKTREES/{pr_id}/state.json"], ticks=1),
    ]
    return tasks


def decompose_T2(pr_id: str, paper_id: str, project: str, target_files: List[str]) -> List[SubTask]:
    file_list = "\n".join(f"- `{f}`" for f in target_files)
    return [
        _st(1, pr_id, SubTaskType.READ_PAPER,
            f"读 arXiv:{paper_id} 全文 + method 关键公式",
            output=[f"WORKTREES/{pr_id}/notes/01-iter{0}-paper-method.md"], ticks=1),
        _st(2, pr_id, SubTaskType.READ_PAPER,
            "提取 paper 中对该 method 在代码层 implementation 的描述", depends=["st-001"],
            output=[f"WORKTREES/{pr_id}/notes/02-iter{0}-paper-impl.md"], ticks=1),
        _st(3, pr_id, SubTaskType.ANALYZE_CODE,
            f"读 {project} 目标文件 {file_list} 当前 docstring 状态",
            output=[f"WORKTREES/{pr_id}/notes/03-iter{0}-code-docstring.md"], ticks=1),
        _st(4, pr_id, SubTaskType.CROSS_CHECK,
            "对比 paper method (st-001/002) vs code docstring (st-003)，找 gap", depends=["st-002", "st-003"],
            output=[f"WORKTREES/{pr_id}/notes/04-iter{0}-docstring-gap.md"], ticks=1),
        _st(5, pr_id, SubTaskType.WRITE_DOCSTRING,
            "为每个目标文件写增强 docstring（含推导 + 论文引用）", depends=["st-004"],
            output=target_files, ticks=2),
        _st(6, pr_id, SubTaskType.WRITE_CITATION,
            f"在 {project} 的 docs/references.bib 或 README 添加论文 bibtex", depends=["st-005"],
            output=["README.md", "docs/references.bib"], ticks=1),
        _st(7, pr_id, SubTaskType.WRITE_DOCSTRING,
            "在源文件 module-level docstring 引用 bibtex key", depends=["st-006"],
            output=target_files, ticks=1),
        _st(8, pr_id, SubTaskType.VERIFY_LINT,
            "跑 lint / markdownlint，0 error", depends=["st-007"],
            output=[f"WORKTREES/{pr_id}/notes/08-iter{0}-lint.log"], ticks=1),
        _st(9, pr_id, SubTaskType.VERIFY_BUILD,
            "确保 docs build 仍然通过", depends=["st-005"],
            output=[f"WORKTREES/{pr_id}/notes/09-iter{0}-docs-build.log"], ticks=1),
        _st(10, pr_id, SubTaskType.SELF_CRITIQUE,
            "自我批评：docstring 推导是否清晰？论文引用是否准确？", depends=["st-008", "st-009"],
            output=[f"WORKTREES/{pr_id}/notes/10-iter{0}-self-critique.md"], ticks=1),
        _st(11, pr_id, SubTaskType.WRITE_PR_BODY,
            "写 PR description", depends=["st-010"],
            output=[f"WORKTREES/{pr_id}/notes/11-iter{0}-pr-body.md"], ticks=1),
        _st(12, pr_id, SubTaskType.PERSIST,
            "原子 commit + push（本地）", depends=["st-011"],
            output=[f"WORKTREES/{pr_id}/state.json"], ticks=1),
    ]


def decompose_T5(pr_id: str, paper_id: str, project: str, target_files: List[str]) -> List[SubTask]:
    return [
        _st(1, pr_id, SubTaskType.ANALYZE_CODE,
            f"扫 {project} 源文件，识别所有数学/物理方法的函数",
            output=[f"WORKTREES/{pr_id}/notes/01-iter{0}-method-inventory.md"], ticks=1),
        _st(2, pr_id, SubTaskType.ANALYZE_CODE,
            "对每个方法搜索：是否已有论文引用", depends=["st-001"],
            output=[f"WORKTREES/{pr_id}/notes/02-iter{0}-citation-coverage.md"], ticks=1),
        _st(3, pr_id, SubTaskType.READ_PAPER,
            f"读 arXiv:{paper_id} 关键方法 + 论文出处", depends=["st-002"],
            output=[f"WORKTREES/{pr_id}/notes/03-iter{0}-paper-method.md"], ticks=1),
        _st(4, pr_id, SubTaskType.CROSS_CHECK,
            "对比 st-002 (代码无引用列表) vs st-003 (论文方法)，找目标", depends=["st-002", "st-003"],
            output=[f"WORKTREES/{pr_id}/notes/04-iter{0}-citation-targets.md"], ticks=1),
        _st(5, pr_id, SubTaskType.WRITE_CITATION,
            f"在 {project} 添加/更新 references.bib", depends=["st-004"],
            output=["README.md", "docs/references.bib"], ticks=1),
        _st(6, pr_id, SubTaskType.WRITE_DOCSTRING,
            "为每个目标函数添加 docstring + [bibtex key] 引用", depends=["st-005"],
            output=target_files, ticks=2),
        _st(7, pr_id, SubTaskType.VERIFY_LINT,
            "跑 lint，0 error", depends=["st-006"],
            output=[f"WORKTREES/{pr_id}/notes/07-iter{0}-lint.log"], ticks=1),
        _st(8, pr_id, SubTaskType.SELF_CRITIQUE,
            "自我批评：引用格式是否一致？是否漏了重要方法？", depends=["st-007"],
            output=[f"WORKTREES/{pr_id}/notes/08-iter{0}-self-critique.md"], ticks=1),
        _st(9, pr_id, SubTaskType.WRITE_PR_BODY,
            "写 PR description", depends=["st-008"],
            output=[f"WORKTREES/{pr_id}/notes/09-iter{0}-pr-body.md"], ticks=1),
        _st(10, pr_id, SubTaskType.PERSIST,
            "原子 commit + push（本地）", depends=["st-009"],
            output=[f"WORKTREES/{pr_id}/state.json"], ticks=1),
    ]


def decompose(pr_id: str, pr_type: str, paper_id: str, project: str,
              target_files: List[str], test_dir: str = "tests") -> List[SubTask]:
    if pr_type == "T1":
        return decompose_T1(pr_id, paper_id, project, test_dir)
    if pr_type == "T2":
        return decompose_T2(pr_id, paper_id, project, target_files)
    if pr_type == "T5":
        return decompose_T5(pr_id, paper_id, project, target_files)
    raise ValueError(f"未实现的 PR 类型: {pr_type}")
