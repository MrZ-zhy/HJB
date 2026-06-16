"""V5.1 Sub-task Decomposer：把 (论文, 项目) 拆成 atomic sub-task DAG。

V5.1 核心理念：
  - PR 是 DAG of sub-tasks
  - 每个 sub-task 1 tick 内能完成
  - 依赖关系决定执行顺序
  - 1 PR 通常 10-30 个 sub-tasks
  - 1 PR 通常 2-4 周（每天 1-3 sub-tasks）
"""
from __future__ import annotations

import os
from typing import Dict, List

from core.models import SubTask, SubTaskType, SubTaskStatus


def _st(idx: int, pr_id: str, type_: SubTaskType, desc: str, depends: List[str] = None,
        output: List[str] = None, verify: str = "", ticks: int = 1) -> SubTask:
    sid = f"st-{idx:03d}"
    return SubTask(
        id=sid,
        pr_id=pr_id,
        type=type_,
        description=desc,
        depends_on=depends or [],
        output_files=output or [],
        verification=verify,
        estimated_ticks=ticks,
    )
# ──────────────────────────────────────────────────────────────
# 通用 sub-task 模板（每类 PR 类型）
# ──────────────────────────────────────────────────────────────

def decompose_T1(pr_id: str, paper_id: str, project: str, test_dir: str) -> List[SubTask]:
    """T1 复现性 unit test 的标准 15 步 DAG。"""
    tasks: List[SubTask] = [
        # 1-3: 读论文
        _st(1, pr_id, SubTaskType.READ_PAPER,
            f"读 arXiv:{paper_id} 全文，提取 abstract + 关键贡献",
            output=[f"WORKTREES/{pr_id}/notes/01-paper-abstract.md"],
            verify="文件存在且 ≥ 200 字", ticks=1),
        _st(2, pr_id, SubTaskType.READ_PAPER,
            f"读 arXiv:{paper_id} 的 method/api/specification section，提取可测试 contract",
            depends=["st-001"],
            output=[f"WORKTREES/{pr_id}/notes/02-paper-contract.md"],
            verify="列出至少 3 个可测试 API/数据结构契约", ticks=1),
        _st(3, pr_id, SubTaskType.READ_PAPER,
            f"读 arXiv:{paper_id} 的 experimental/validation section，提取参考值/解析解",
            depends=["st-001"],
            output=[f"WORKTREES/{pr_id}/notes/03-paper-reference-values.md"],
            verify="列出至少 1 个可作为 oracle 的参考值", ticks=1),
        # 4-6: 代码分析
        _st(4, pr_id, SubTaskType.ANALYZE_CODE,
            f"读 {project} 项目顶层目录，识别语言、测试框架、CI 配置",
            output=[f"WORKTREES/{pr_id}/notes/04-code-overview.md"],
            verify="识别 language + test_framework + ci_tool", ticks=1),
        _st(5, pr_id, SubTaskType.ANALYZE_CODE,
            f"读 {project} 目标源文件，列出所有 public function/class 及其签名",
            depends=["st-004"],
            output=[f"WORKTREES/{pr_id}/notes/05-code-surface.md"],
            verify="列出 ≥ 5 个 function + 完整签名", ticks=1),
        _st(6, pr_id, SubTaskType.CROSS_CHECK,
            f"交叉对比：paper contract (st-002) vs code surface (st-005)，找出 gap",
            depends=["st-002", "st-005"],
            output=[f"WORKTREES/{pr_id}/notes/06-cross-check.md"],
            verify="列出 paper 有但 code 无 / paper 无但 code 有 / 双方都有 三类对比", ticks=1),
        # 7-9: 写测试
        _st(7, pr_id, SubTaskType.WRITE_TEST,
            f"写 {test_dir}/test_<module>.py 骨架：import + 1 个 dummy test",
            depends=["st-006"],
            output=[f"{test_dir}/test_<module>.py"],
            verify="文件能被 test runner 加载（即使有 skip）", ticks=1),
        _st(8, pr_id, SubTaskType.WRITE_TEST,
            "为 cross-check 找出的每个 gap 写 1 个 test case",
            depends=["st-007"],
            output=[f"{test_dir}/test_<module>.py"],
            verify="每个 gap 对应至少 1 个 test function", ticks=2),
        _st(9, pr_id, SubTaskType.WRITE_TEST,
            "添加 oracle 断言：每个 test 用 paper 参考值 (st-003) 验证",
            depends=["st-003", "st-008"],
            output=[f"{test_dir}/test_<module>.py"],
            verify="每个 test 含 1+ assert 且 oracle 来源标注为 paper", ticks=1),
        # 10: docstring
        _st(10, pr_id, SubTaskType.WRITE_DOCSTRING,
            "为目标源文件顶部加 docstring 引用论文 arXiv ID",
            depends=["st-005"],
            output=[],  # 由 handler 决定具体文件
            verify="含 `arXiv:{paper_id}` 字符串", ticks=1),
        # 11-12: 验证
        _st(11, pr_id, SubTaskType.VERIFY_TESTS,
            "本地跑 test 套件，全部 PASS",
            depends=["st-009"],
            output=[f"WORKTREES/{pr_id}/notes/11-test-run.log"],
            verify="exit code 0 + 所有 test passed", ticks=1),
        _st(12, pr_id, SubTaskType.VERIFY_LINT,
            "跑 lint + type check，0 error",
            depends=["st-010"],
            output=[f"WORKTREES/{pr_id}/notes/12-lint-run.log"],
            verify="0 error（warning 可接受）", ticks=1),
        # 13: self critique
        _st(13, pr_id, SubTaskType.SELF_CRITIQUE,
            "自我批评：这个 PR 真解决了 paper 提的问题吗？是否有更简洁的写法？",
            depends=["st-011", "st-012"],
            output=[f"WORKTREES/{pr_id}/notes/13-self-critique.md"],
            verify="包含 3 段：'PR 解决了什么' / '可能的问题' / '改进建议'", ticks=1),
        # 14: PR body
        _st(14, pr_id, SubTaskType.WRITE_PR_BODY,
            "写 PR description：背景（论文引用）+ 动机（upstream gap）+ 改动 + 验证",
            depends=["st-013"],
            output=[f"WORKTREES/{pr_id}/notes/14-pr-body.md"],
            verify="含 arXiv ID + 改动文件列表 + 测试结果", ticks=1),
        # 15: persist
        _st(15, pr_id, SubTaskType.PERSIST,
            "原子 commit + push (本地，等 READY_TO_SUBMIT 才 push 到 fork)",
            depends=["st-014"],
            output=[f"WORKTREES/{pr_id}/state.json"],
            verify="git log 含 1 个新 commit，state.json 更新", ticks=1),
    ]
    return tasks


def decompose_T2(pr_id: str, paper_id: str, project: str, target_files: List[str]) -> List[SubTask]:
    """T2 文档增强的标准 12 步 DAG。"""
    file_list = "\n".join(f"- `{f}`" for f in target_files)
    return [
        _st(1, pr_id, SubTaskType.READ_PAPER,
            f"读 arXiv:{paper_id} 全文，提取 abstract + method section 关键公式",
            output=[f"WORKTREES/{pr_id}/notes/01-paper-method.md"],
            verify="含 ≥ 3 个核心公式（LaTeX 形式）", ticks=1),
        _st(2, pr_id, SubTaskType.READ_PAPER,
            "提取 paper 中对该 method 在代码层 implementation 的描述",
            depends=["st-001"],
            output=[f"WORKTREES/{pr_id}/notes/02-paper-implementation.md"],
            verify="列出 paper 提到的函数/类名（如有）", ticks=1),
        _st(3, pr_id, SubTaskType.ANALYZE_CODE,
            f"读 {project} 目标文件 {file_list}，列出当前 docstring 状态",
            output=[f"WORKTREES/{pr_id}/notes/03-current-docstring.md"],
            verify="每个目标文件记录：是否已有 docstring / 长度 / 是否含引用", ticks=1),
        _st(4, pr_id, SubTaskType.CROSS_CHECK,
            "对比 paper method (st-001/002) vs code docstring (st-003)，找 gap",
            depends=["st-002", "st-003"],
            output=[f"WORKTREES/{pr_id}/notes/04-docstring-gap.md"],
            verify="列出每个文件的 gap：缺什么公式 / 缺什么引用 / 缺什么说明", ticks=1),
        _st(5, pr_id, SubTaskType.WRITE_DOCSTRING,
            "为每个目标文件写增强 docstring（含推导 + 论文引用）",
            depends=["st-004"],
            output=target_files,
            verify="每个文件 docstring 含 1+ 公式 + 论文 arXiv ID 引用", ticks=2),
        _st(6, pr_id, SubTaskType.WRITE_CITATION,
            f"在 {project} 的 docs/references.bib 或 README 添加论文 bibtex",
            depends=["st-005"],
            output=["README.md", "docs/references.bib"],
            verify="含完整 bibtex entry（title/authors/year/arxiv_id）", ticks=1),
        _st(7, pr_id, SubTaskType.WRITE_DOCSTRING,
            "在源文件 module-level docstring 引用 bibtex key",
            depends=["st-006"],
            output=target_files,
            verify="module docstring 含 `[<bibtex_key>]` 引用", ticks=1),
        _st(8, pr_id, SubTaskType.VERIFY_LINT,
            "跑 lint / markdownlint，0 error",
            depends=["st-007"],
            output=[f"WORKTREES/{pr_id}/notes/08-lint.log"],
            verify="0 error", ticks=1),
        _st(9, pr_id, SubTaskType.VERIFY_BUILD,
            "确保 docs build 仍然通过（如 mkdocs / sphinx）",
            depends=["st-005"],
            output=[f"WORKTREES/{pr_id}/notes/09-docs-build.log"],
            verify="docs build exit 0", ticks=1),
        _st(10, pr_id, SubTaskType.SELF_CRITIQUE,
            "自我批评：docstring 的数学推导是否清晰？论文引用是否准确？",
            depends=["st-008", "st-009"],
            output=[f"WORKTREES/{pr_id}/notes/10-self-critique.md"],
            verify="含 3 段：'读者能否理解' / '引用是否准确' / '格式是否一致'", ticks=1),
        _st(11, pr_id, SubTaskType.WRITE_PR_BODY,
            "写 PR description：背景（论文引用）+ 改动文件 + docstring diff 摘要",
            depends=["st-010"],
            output=[f"WORKTREES/{pr_id}/notes/11-pr-body.md"],
            verify="含 arXiv ID + 改动文件列表 + 引用效果", ticks=1),
        _st(12, pr_id, SubTaskType.PERSIST,
            "原子 commit + push (本地)",
            depends=["st-011"],
            output=[f"WORKTREES/{pr_id}/state.json"],
            verify="git log 含 1 个新 commit", ticks=1),
    ]


def decompose_T5(pr_id: str, paper_id: str, project: str, target_files: List[str]) -> List[SubTask]:
    """T5 citation 补全：扫代码 → 找缺引用 → 加 bibtex。"""
    return [
        _st(1, pr_id, SubTaskType.ANALYZE_CODE,
            f"扫 {project} 源文件，识别所有数学/物理方法的函数",
            output=[f"WORKTREES/{pr_id}/notes/01-method-inventory.md"],
            verify="列出 ≥ 5 个数学方法名 + 所在文件:行号", ticks=1),
        _st(2, pr_id, SubTaskType.ANALYZE_CODE,
            "对每个方法搜索：是否已有论文引用",
            depends=["st-001"],
            output=[f"WORKTREES/{pr_id}/notes/02-citation-coverage.md"],
            verify="每个方法标注：'已引用 [paper X]' 或 '无引用'", ticks=1),
        _st(3, pr_id, SubTaskType.READ_PAPER,
            f"读 arXiv:{paper_id} 全文，提取关键方法的论文定义",
            depends=["st-002"],
            output=[f"WORKTREES/{pr_id}/notes/03-paper-method.md"],
            verify="含 method 名称 + 论文出处", ticks=1),
        _st(4, pr_id, SubTaskType.CROSS_CHECK,
            "对比 st-002 (代码无引用列表) vs st-003 (论文方法)，找目标",
            depends=["st-002", "st-003"],
            output=[f"WORKTREES/{pr_id}/notes/04-citation-targets.md"],
            verify="列出 3+ 个 待补引用的 (file:function, paper)", ticks=1),
        _st(5, pr_id, SubTaskType.WRITE_CITATION,
            f"在 {project} 添加/更新 references.bib (或 README 引用段)",
            depends=["st-004"],
            output=["README.md", "docs/references.bib"],
            verify="含完整 bibtex", ticks=1),
        _st(6, pr_id, SubTaskType.WRITE_DOCSTRING,
            "为每个目标函数添加 docstring + [bibtex key] 引用",
            depends=["st-005"],
            output=target_files,
            verify="每个目标函数含 1+ 行 docstring + 1+ 引用", ticks=2),
        _st(7, pr_id, SubTaskType.VERIFY_LINT,
            "跑 lint，0 error",
            depends=["st-006"],
            output=[f"WORKTREES/{pr_id}/notes/07-lint.log"],
            verify="0 error", ticks=1),
        _st(8, pr_id, SubTaskType.SELF_CRITIQUE,
            "自我批评：引用格式是否一致？是否漏了重要方法？",
            depends=["st-007"],
            output=[f"WORKTREES/{pr_id}/notes/08-self-critique.md"],
            verify="含 3 段：'引用质量' / '漏了什么' / '建议'", ticks=1),
        _st(9, pr_id, SubTaskType.WRITE_PR_BODY,
            "写 PR description：背景（项目无引用问题）+ 改动文件 + bibtex 摘要",
            depends=["st-008"],
            output=[f"WORKTREES/{pr_id}/notes/09-pr-body.md"],
            verify="含 改动文件列表 + 引用数量", ticks=1),
        _st(10, pr_id, SubTaskType.PERSIST,
            "原子 commit + push (本地)",
            depends=["st-009"],
            output=[f"WORKTREES/{pr_id}/state.json"],
            verify="git log 含 1 个新 commit", ticks=1),
    ]


def decompose(pr_id: str, pr_type: str, paper_id: str, project: str,
              target_files: List[str], test_dir: str = "tests") -> List[SubTask]:
    """按 PR 类型选 decomposer。"""
    if pr_type == "T1":
        return decompose_T1(pr_id, paper_id, project, test_dir)
    if pr_type == "T2":
        return decompose_T2(pr_id, paper_id, project, target_files)
    if pr_type == "T5":
        return decompose_T5(pr_id, paper_id, project, target_files)
    raise ValueError(f"未实现的 PR 类型: {pr_type}")
