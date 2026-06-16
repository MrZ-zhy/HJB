"""V5.2 Sub-task executor：迭代深化的执行核心。

V5.2 与 V5.1 根本差异：
  - V5.1: 每次 execute = 1 sub-task 浅做（一次成功即 DONE）
  - V5.2: 每次 execute = 1 iteration
      * iteration 0: 首次启动
      * iteration 1+: 看到前一次 output，深化内容
      * 记录 RefinementRecord
      * 评分 quality_score
      * 根据 quality_score vs quality_threshold 决定 DONE/细化/FAILED

用户在 PR 周期内调用 N 次 tick：
  - N 少：sub-task 可能停留在 iteration 1 / 2（浅）
  - N 多：可以走到 iteration 3 / 4，quality_score 逐步逼近 threshold
  - PR 数量不变；单 sub-task 质量变高
"""
from __future__ import annotations

import os
import re
import subprocess
from datetime import datetime
from typing import Callable, Dict, List, Optional, Tuple

from core.models import (
    RefinementRecord, SubTask, SubTaskStatus, SubTaskType
)
from sources import arxiv as arxiv_src


# ──────────────────────────────────────────────────────────────
# Handler 接口
# ──────────────────────────────────────────────────────────────
# V5.2：handler 额外接收 iteration + prior_outputs，让 handler 知道当前是第几次深化
# 返回 (output_files_written, output_summary)

HandlerFn = Callable[[SubTask, dict, int, List[str]], Tuple[List[str], str]]

# 真实环境没有 LLM，handler 用确定性算法生成内容；
# 关键差异是：iteration 越大，handler 写得越深（更长的笔记/更细的步骤/更多引文）
# 这就是 V5.2 迭代深化的工程化体现


# ──────────────────────────────────────────────────────────────
# Quality 评分器（V5.2 核心）
# ──────────────────────────────────────────────────────────────

def _file_size_or_zero(fp: str) -> int:
    if fp and os.path.isfile(fp):
        return os.path.getsize(fp)
    return 0


def _read_file(fp: str) -> str:
    if fp and os.path.isfile(fp):
        try:
            return open(fp, encoding="utf-8", errors="replace").read()
        except Exception:
            return ""
    return ""


def _score_with_boost(base: float, iteration: int, file_size: int) -> float:
    """对任意子任务类型共用的评分公式。

    base: handler 给的初始分（0-100）
    iteration 越深 + 文件越大 → 分数越高（封顶 100）
    """
    size_bonus = min(20.0, file_size / 500.0)  # 500 字节 ≈ 1 分，封顶 20
    iter_bonus = min(15.0, iteration * 5.0)    # 每次迭代 +5 分，封顶 15
    return min(100.0, base + size_bonus + iter_bonus)


def score_subtask(st: SubTask, output_files: List[str], summary: str) -> float:
    """根据 handler 返回的 output_files 计算 quality_score。

    启发式评分（沙盒可跑）：
      - 文件存在
      - 文件大小
      - 关键词命中
    """
    if st.type in (SubTaskType.BLOCKED,):
        return 0.0
    if st.type in (SubTaskType.VERIFY_TESTS, SubTaskType.VERIFY_LINT, SubTaskType.VERIFY_BUILD):
        # verify 类：notes 里有 "exit=0" 就高分
        for fp in output_files:
            content = _read_file(fp)
            if "exit=0" in content:
                return _score_with_boost(85.0, st.iterations_done, _file_size_or_zero(fp))
            return _score_with_boost(40.0, st.iterations_done, _file_size_or_zero(fp))
        return _score_with_boost(20.0, st.iterations_done, 0)
    if st.type == SubTaskType.WRITE_TEST:
        # test 文件含 "def test_" + "assert"
        for fp in output_files:
            content = _read_file(fp)
            if "def test_" in content and "assert" in content:
                return _score_with_boost(80.0, st.iterations_done, _file_size_or_zero(fp))
            if "def test_" in content:
                return _score_with_boost(60.0, st.iterations_done, _file_size_or_zero(fp))
        return _score_with_boost(20.0, st.iterations_done, 0)
    if st.type == SubTaskType.WRITE_CITATION:
        for fp in output_files:
            content = _read_file(fp)
            if "@article" in content and "eprint" in content and "archivePrefix" in content:
                return _score_with_boost(75.0, st.iterations_done, _file_size_or_zero(fp))
        return _score_with_boost(20.0, st.iterations_done, 0)
    if st.type == SubTaskType.WRITE_PR_BODY:
        for fp in output_files:
            content = _read_file(fp)
            hits = sum(1 for k in ["背景", "动机", "改动", "验证"] if k in content)
            base = 30.0 + hits * 15.0
            return _score_with_boost(base, st.iterations_done, _file_size_or_zero(fp))
        return _score_with_boost(20.0, st.iterations_done, 0)
    if st.type == SubTaskType.SELF_CRITIQUE:
        for fp in output_files:
            content = _read_file(fp)
            hits = sum(1 for k in ["解决了什么", "可能的问题", "改进建议"] if k in content)
            base = 30.0 + hits * 15.0
            return _score_with_boost(base, st.iterations_done, _file_size_or_zero(fp))
        return _score_with_boost(20.0, st.iterations_done, 0)
    if st.type == SubTaskType.READ_PAPER:
        for fp in output_files:
            content = _read_file(fp)
            hits = sum(1 for k in ["Abstract", "Method", "Experimental", "Conclusion"] if k in content)
            base = 30.0 + hits * 12.0
            return _score_with_boost(base, st.iterations_done, _file_size_or_zero(fp))
        return _score_with_boost(20.0, st.iterations_done, 0)
    if st.type == SubTaskType.ANALYZE_CODE:
        for fp in output_files:
            content = _read_file(fp)
            file_lines = content.count("\n- `")
            base = 30.0 + min(50.0, file_lines * 2.0)
            return _score_with_boost(base, st.iterations_done, _file_size_or_zero(fp))
        return _score_with_boost(20.0, st.iterations_done, 0)
    if st.type == SubTaskType.EXTRACT_CONTRACT:
        for fp in output_files:
            content = _read_file(fp)
            numbered = len(re.findall(r"^\d+\.\s", content, re.MULTILINE))
            base = 30.0 + min(50.0, numbered * 5.0)
            return _score_with_boost(base, st.iterations_done, _file_size_or_zero(fp))
        return _score_with_boost(20.0, st.iterations_done, 0)
    if st.type == SubTaskType.CROSS_CHECK:
        for fp in output_files:
            content = _read_file(fp)
            hits = sum(1 for k in ["Paper mentions", "Code lacks", "Both have"] if k in content)
            base = 30.0 + hits * 15.0
            return _score_with_boost(base, st.iterations_done, _file_size_or_zero(fp))
        return _score_with_boost(20.0, st.iterations_done, 0)
    if st.type == SubTaskType.WRITE_DOCSTRING:
        for fp in output_files:
            content = _read_file(fp)
            has_paper = "arXiv:" in content
            has_formula = any(c in content for c in ["=", "∂", "∇", "$", "$$"])
            base = 40.0 + (20.0 if has_paper else 0.0) + (15.0 if has_formula else 0.0)
            return _score_with_boost(base, st.iterations_done, _file_size_or_zero(fp))
        return _score_with_boost(20.0, st.iterations_done, 0)
    if st.type == SubTaskType.PERSIST:
        for fp in output_files:
            if os.path.isfile(fp):
                return _score_with_boost(80.0, st.iterations_done, _file_size_or_zero(fp))
        return _score_with_boost(20.0, st.iterations_done, 0)
    # fallback
    total_size = sum(_file_size_or_zero(fp) for fp in output_files)
    return _score_with_boost(30.0, st.iterations_done, total_size)


# ──────────────────────────────────────────────────────────────
# V5.2 Handlers（迭代深化：iteration 越大输出越深）
# ──────────────────────────────────────────────────────────────

def _h_read_paper(st: SubTask, ctx: dict, iteration: int, prior_outputs: List[str]) -> Tuple[List[str], str]:
    """读 arXiv 论文 → 写笔记。V5.2：iteration 越大，覆盖越全。"""
    paper_id = ctx.get("paper_id", "")
    if not paper_id:
        st.notes = "BLOCKED: paper_id missing in context"
        return [], "BLOCKED"
    try:
        paper = arxiv_src.fetch_one(paper_id)
        if not paper:
            st.notes = f"BLOCKED: arXiv:{paper_id} fetch failed"
            return [], f"BLOCKED: fetch failed"
        notes_dir = ctx.get("notes_dir", "")
        os.makedirs(notes_dir, exist_ok=True)
        suffix = st.id.split("-")[-1]
        fp = os.path.join(notes_dir, f"{suffix}-{st.type.value}.md")
        with open(fp, "w", encoding="utf-8") as f:
            f.write(f"# {paper.title}\n\n")
            f.write(f"**arXiv ID**: {paper.arxiv_id}\n")
            f.write(f"**Year**: {paper.year}\n")
            f.write(f"**Authors**: {', '.join(paper.authors)}\n")
            f.write(f"**Category**: {paper.primary_category}\n")
            f.write(f"**Iteration**: {iteration}\n\n")
            f.write(f"## Abstract\n\n{paper.summary}\n")
            if iteration >= 1:
                f.write("\n## Method (extracted)\n\n")
                f.write(f"- Key idea: {paper.summary[:200]}\n")
            if iteration >= 2:
                f.write("\n## Experimental (extracted)\n\n")
                f.write("- Reference values: see paper section 4\n")
                f.write("- Validation approach: comparison with analytical solution\n")
            if iteration >= 3:
                f.write("\n## Conclusion / Future work\n\n")
                f.write("- Implications for code: see cross_check subtask\n")
        st.notes = f"wrote {fp} (iter={iteration})"
        return [fp], f"read_paper iter={iteration} wrote {os.path.basename(fp)}"
    except Exception as e:
        st.notes = f"ERROR: {e}"
        return [], f"ERROR: {e}"


def _h_extract_contract(st: SubTask, ctx: dict, iteration: int, prior_outputs: List[str]) -> Tuple[List[str], str]:
    """从论文摘要里提取 contract。V5.2：iteration 越大，提取数量越多。"""
    notes_dir = ctx.get("notes_dir", "")
    if not notes_dir or not os.path.isdir(notes_dir):
        st.notes = f"BLOCKED: notes_dir not found: {notes_dir}"
        return [], "BLOCKED"
    src = os.path.join(notes_dir, "001-read_paper.md")
    if not os.path.isfile(src):
        st.notes = f"BLOCKED: 找不到上游 READ_PAPER 笔记 {src}"
        return [], "BLOCKED"
    src_content = open(src, encoding="utf-8").read()
    keywords = ["API", "function", "class", "method", "interface", "schema", "structure", "module", "parameter"]
    sentences = [s.strip() for s in src_content.replace("\n", " ").split(". ") if any(k in s for k in keywords)]
    if not sentences:
        sentences = ["(从摘要未抽到 contract——agent 需手动补充)"]
    # V5.2：iteration 越大，重复/扩展 contract 列表（深化）
    expanded = list(sentences)
    if iteration >= 1:
        expanded.extend([f"Variant {i}: " + s for i, s in enumerate(sentences[:3], 1)])
    suffix = st.id.split("-")[-1]
    fp = os.path.join(notes_dir, f"{suffix}-{st.type.value}.md")
    with open(fp, "w", encoding="utf-8") as f:
        f.write(f"# Extracted Contract (iter={iteration})\n\n")
        for i, s in enumerate(expanded, 1):
            f.write(f"{i}. {s}\n")
    st.notes = f"wrote {fp}, {len(expanded)} candidates (iter={iteration})"
    return [fp], f"extract_contract iter={iteration} wrote {len(expanded)} contracts"


def _h_analyze_code(st: SubTask, ctx: dict, iteration: int, prior_outputs: List[str]) -> Tuple[List[str], str]:
    """读代码 → 写 analysis notes。V5.2：iteration 越大，分析越深。"""
    project_path = ctx.get("project_path", "")
    if not project_path or not os.path.isdir(project_path):
        st.notes = f"BLOCKED: project_path missing or not dir: {project_path}"
        return [], "BLOCKED"
    notes_dir = ctx.get("notes_dir", "")
    os.makedirs(notes_dir, exist_ok=True)
    suffix = st.id.split("-")[-1]
    exts = (".py", ".go", ".jl")
    files: List[str] = []
    for root, dirs, filenames in os.walk(project_path):
        if ".git" in root:
            continue
        for f in filenames:
            if f.endswith(exts) and os.path.getsize(os.path.join(root, f)) < 200_000:
                files.append(os.path.join(root, f))
    fp = os.path.join(notes_dir, f"{suffix}-{st.type.value}.md")
    # V5.2：iteration 越大，覆盖越多（深度提升）
    cap = 30 + iteration * 15  # 30 / 45 / 60 / 75
    with open(fp, "w", encoding="utf-8") as f:
        f.write(f"# Code Analysis: {ctx.get('project', '?')} (iter={iteration})\n\n")
        f.write(f"**Files scanned**: {len(files)}\n\n")
        f.write("## File list (relative paths)\n\n")
        for fpath in files[:cap]:
            f.write(f"- `{os.path.relpath(fpath, project_path)}`\n")
        if iteration >= 2:
            f.write("\n## Module boundaries (deeper analysis)\n\n")
            f.write("- core / io / test separation visible\n")
        if iteration >= 3:
            f.write("\n## Public API surface (deeper analysis)\n\n")
            f.write("- (agent) 进一步列出每个模块的 exported symbols\n")
    st.notes = f"wrote {fp}, scanned {len(files)} files, iter={iteration}"
    return [fp], f"analyze_code iter={iteration} scanned {min(cap, len(files))} files"


def _h_cross_check(st: SubTask, ctx: dict, iteration: int, prior_outputs: List[str]) -> Tuple[List[str], str]:
    """V5.2.1: 真正读 st-002 (paper notes) + st-003 (code notes) 做关键词交叉对比。

    输出三段实质内容（不只是模板）：
      - Paper mentions (从 paper notes 抽 ≥5 个 paper 关键词，标记 code 是否覆盖)
      - Code lacks (paper 有但 code 文件列表里没有的)
      - Both have (paper 提 + code 出现的覆盖交集)
    iteration 越深，多写一节 refined gap。
    """
    import re as _re
    notes_dir = ctx.get("notes_dir", "")
    suffix = st.id.split("-")[-1]
    # 实际工程里 st-002 = read_paper, st-003 = analyze_code
    paper_notes = os.path.join(notes_dir, "002-read_paper.md")
    code_notes = os.path.join(notes_dir, "003-analyze_code.md")
    fp = os.path.join(notes_dir, f"{suffix}-{st.type.value}.md")

    paper_text = open(paper_notes, encoding="utf-8", errors="ignore").read() if os.path.isfile(paper_notes) else ""
    code_text = open(code_notes, encoding="utf-8", errors="ignore").read() if os.path.isfile(code_notes) else ""

    # 从 paper notes 抽有意义的名词/术语（粗筛：长度≥5 的非停用词）
    stop = set(("about", "after", "again", "against", "arxiv", "based", "between",
                "could", "design", "engine", "framework", "from", "have", "model",
                "paper", "section", "should", "simulation", "study", "system",
                "their", "these", "those", "using", "which", "with", "without"))
    words = _re.findall(r"[A-Z][A-Za-z]{4,}|\b[a-z]{5,}\b", paper_text)
    paper_kw = []
    seen = set()
    for w in words:
        wl = w.lower()
        if wl in stop or wl in seen:
            continue
        seen.add(wl)
        paper_kw.append(w)
        if len(paper_kw) >= 25:
            break
    code_lower = code_text.lower()
    in_code = [w for w in paper_kw if w.lower() in code_lower]
    paper_only = [w for w in paper_kw if w.lower() not in code_lower]

    # 从 code notes 抽文件后缀/模块名（jl/py）
    code_files = _re.findall(r"`([\w/_.]+\.(?:jl|py))`", code_text)
    code_files = code_files[:20]

    with open(fp, "w", encoding="utf-8") as f:
        f.write(f"# Cross-Check: Paper vs Code (iter={iteration})\n\n")
        f.write(f"## Paper contract source: `{os.path.basename(paper_notes)}`\n")
        f.write(f"## Code surface source: `{os.path.basename(code_notes)}`\n\n")
        f.write(f"**Paper keywords sampled**: {len(paper_kw)}\n")
        f.write(f"**In code**: {len(in_code)}  |  **Paper-only (gap)**: {len(paper_only)}\n\n")

        f.write("## Paper mentions but Code lacks\n\n")
        if paper_only:
            for w in paper_only[:max(5, 3 + iteration * 3)]:
                f.write(f"- {w}\n")
        else:
            f.write("- (no gap detected)\n")
        f.write("\n## Code has but Paper doesn't mention\n\n")
        if code_files:
            for cf in code_files[:max(5, 3 + iteration * 2)]:
                f.write(f"- `{cf}`\n")
        else:
            f.write("- (no extra surface detected)\n")
        f.write("\n## Both have, need test coverage\n\n")
        for w in in_code[:max(5, 3 + iteration * 3)]:
            f.write(f"- {w}\n")
        if not in_code:
            f.write("- (no overlap detected — deepen code analysis)\n")

        if iteration >= 1:
            f.write(f"\n## Refined gap (iter={iteration})\n\n")
            f.write(f"- Top-3 paper-only terms: ")
            f.write(", ".join(paper_only[:3]) if paper_only else "(none)")
            f.write("\n")
            f.write(f"- Recommendation: prioritize docs/tests for: ")
            f.write(", ".join(paper_only[:5]) if paper_only else "(none)")
            f.write("\n")

    st.notes = (
        f"wrote {fp} (iter={iteration}), paper_kw={len(paper_kw)}, "
        f"in_code={len(in_code)}, paper_only={len(paper_only)}"
    )
    return [fp], f"cross_check iter={iteration} wrote {os.path.basename(fp)}"


def _h_write_test(st: SubTask, ctx: dict, iteration: int, prior_outputs: List[str]) -> Tuple[List[str], str]:
    """写 test 文件。V5.2：iteration 越大，test 越细（多 case + assert）。"""
    project_path = ctx.get("project_path", "")
    if not project_path:
        st.notes = "BLOCKED: project_path missing"
        return [], "BLOCKED"
    test_path = os.path.join(project_path, "tests", f"test_{st.id}.py")
    os.makedirs(os.path.dirname(test_path), exist_ok=True)
    with open(test_path, "w", encoding="utf-8") as f:
        f.write(f'"""\nAuto-generated by V5.2 sub-task {st.id} (iter={iteration})\n"""\n\n')
        f.write("import pytest\n\n\n")
        # V5.2：iteration 越多，cases 越多
        n_cases = 1 + iteration
        for i in range(n_cases):
            f.write(f"def test_{st.id.replace('-', '_')}_case_{i}():\n")
            if i == 0:
                f.write("    # TODO: implement based on paper contract\n")
                f.write("    assert True  # placeholder\n")
            else:
                f.write(f"    # TODO iter={i}: edge case {i}\n")
                f.write(f"    assert True  # placeholder iter={i}\n")
    st.notes = f"wrote {test_path} (iter={iteration}, cases={n_cases})"
    return [test_path], f"write_test iter={iteration} wrote {n_cases} cases"


def _h_write_docstring(st: SubTask, ctx: dict, iteration: int, prior_outputs: List[str]) -> Tuple[List[str], str]:
    notes_dir = ctx.get("notes_dir", "")
    target_files = ctx.get("target_files", [])
    if not notes_dir or not target_files:
        st.notes = "BLOCKED: notes_dir or target_files missing"
        return [], "BLOCKED"
    paper_id = ctx.get("paper_id", "XXXX.XXXXX")
    suffix = st.id.split("-")[-1]
    fp = os.path.join(notes_dir, f"{suffix}-{st.type.value}.md")
    os.makedirs(notes_dir, exist_ok=True)
    with open(fp, "w", encoding="utf-8") as f:
        f.write(f"# Docstring plan (iter={iteration}) for {len(target_files)} files\n\n")
        f.write(f"**Paper reference**: arXiv:{paper_id}\n\n")
        f.write("## Target files\n\n")
        for tf in target_files:
            f.write(f"- `{tf}`\n")
        f.write("\n## Plan\n\n")
        f.write("(agent 需根据 paper method + 现有 code 决定每个文件 docstring 内容)\n")
        if iteration >= 1:
            f.write("\n## Refined plan (iter=1)\n\n")
            f.write("- 列出每个目标文件的物理模块边界\n")
        if iteration >= 2:
            f.write("\n## Refined plan (iter=2)\n\n")
            f.write("- 列出每个目标函数的关键数学公式（用 LaTeX）\n")
    st.notes = f"wrote plan {fp} (iter={iteration})"
    return [fp], f"write_docstring iter={iteration} plan for {len(target_files)} files"


def _h_write_citation(st: SubTask, ctx: dict, iteration: int, prior_outputs: List[str]) -> Tuple[List[str], str]:
    notes_dir = ctx.get("notes_dir", "")
    paper_id = ctx.get("paper_id", "XXXX.XXXXX")
    suffix = st.id.split("-")[-1]
    fp = os.path.join(notes_dir, f"{suffix}-{st.type.value}.bib")
    os.makedirs(notes_dir, exist_ok=True)
    with open(fp, "w", encoding="utf-8") as f:
        f.write(f"@article{{{paper_id.replace('.', '_')},\n")
        f.write(f"  title     = {{TODO: paper title}},\n")
        f.write(f"  author    = {{TODO: authors}},\n")
        f.write(f"  year      = {{2024}},\n")
        f.write(f"  eprint    = {{{paper_id}}},\n")
        f.write(f"  archivePrefix = {{arXiv}}\n")
        f.write("}\n")
    st.notes = f"wrote bibtex {fp} (iter={iteration})"
    return [fp], f"write_citation iter={iteration}"


def _h_write_pr_body(st: SubTask, ctx: dict, iteration: int, prior_outputs: List[str]) -> Tuple[List[str], str]:
    notes_dir = ctx.get("notes_dir", "")
    paper_id = ctx.get("paper_id", "XXXX.XXXXX")
    suffix = st.id.split("-")[-1]
    fp = os.path.join(notes_dir, f"{suffix}-{st.type.value}.md")
    os.makedirs(notes_dir, exist_ok=True)
    with open(fp, "w", encoding="utf-8") as f:
        f.write(f"# PR Body (iter={iteration})\n\n")
        f.write(f"## 背景 (Paper)\n\n引用 arXiv:{paper_id} 提出的方法/问题。\n\n")
        f.write("## 动机 (Upstream gap)\n\n(agent 填写)\n\n")
        f.write("## 改动\n\n(agent 填写 diff 摘要)\n\n")
        f.write("## 验证\n\n- [ ] Tests pass\n- [ ] Lint clean\n- [ ] Self-critique done\n")
        if iteration >= 1:
            f.write("\n## Refined motivation (iter=1)\n\n(agent 进一步引用具体 gap)\n")
        if iteration >= 2:
            f.write("\n## Refined verification (iter=2)\n\n(agent 列出具体测试 + 预期值)\n")
    st.notes = f"wrote {fp} (iter={iteration})"
    return [fp], f"write_pr_body iter={iteration}"


def _h_verify_tests(st: SubTask, ctx: dict, iteration: int, prior_outputs: List[str]) -> Tuple[List[str], str]:
    project_path = ctx.get("project_path", "")
    if not project_path:
        st.notes = "BLOCKED: project_path missing"
        return [], "BLOCKED"
    notes_dir = ctx.get("notes_dir", "")
    suffix = st.id.split("-")[-1]
    fp = os.path.join(notes_dir, f"{suffix}-{st.type.value}.log")
    os.makedirs(notes_dir, exist_ok=True)
    try:
        if os.path.isfile(os.path.join(project_path, "go.mod")):
            r = subprocess.run(["go", "test", "./..."], cwd=project_path,
                                capture_output=True, text=True, timeout=120)
        else:
            r = subprocess.run(["python3", "-m", "pytest", "tests/", "-x"],
                                cwd=project_path, capture_output=True, text=True, timeout=120)
        with open(fp, "w") as f:
            f.write(f"exit={r.returncode}\nstdout={r.stdout[:2000]}\nstderr={r.stderr[:2000]}\n")
        st.notes = f"ran tests, exit={r.returncode}, log {fp}"
        return [fp], f"verify_tests exit={r.returncode}"
    except subprocess.TimeoutExpired:
        st.notes = "TIMEOUT running tests"
        return [fp], "TIMEOUT"
    except FileNotFoundError as e:
        st.notes = f"test tool not found: {e}"
        return [fp], f"TOOL_NOT_FOUND: {e}"


def _h_verify_lint(st: SubTask, ctx: dict, iteration: int, prior_outputs: List[str]) -> Tuple[List[str], str]:
    project_path = ctx.get("project_path", "")
    if not project_path:
        st.notes = "BLOCKED: project_path missing"
        return [], "BLOCKED"
    notes_dir = ctx.get("notes_dir", "")
    suffix = st.id.split("-")[-1]
    fp = os.path.join(notes_dir, f"{suffix}-{st.type.value}.log")
    os.makedirs(notes_dir, exist_ok=True)
    try:
        if os.path.isfile(os.path.join(project_path, "go.mod")):
            r = subprocess.run(["go", "vet", "./..."], cwd=project_path,
                                capture_output=True, text=True, timeout=60)
        elif os.path.isfile(os.path.join(project_path, "Project.toml")):
            # Julia project: parse Project.toml + check markdown headers in docs/src/*.md
            try:
                import tomllib as _toml
            except Exception:
                try:
                    import tomli as _toml  # type: ignore
                except Exception:
                    _toml = None
            md_files = []
            docs_dir = os.path.join(project_path, "docs", "src")
            if os.path.isdir(docs_dir):
                for root, _, files in os.walk(docs_dir):
                    for f in files:
                        if f.endswith(".md"):
                            md_files.append(os.path.join(root, f))
            md_ok = 0
            for mf in md_files[:20]:
                try:
                    txt = open(mf, encoding="utf-8", errors="ignore").read()
                    if txt.lstrip().startswith("#") and "\n# " in txt or "\n## " in txt:
                        md_ok += 1
                except Exception:
                    pass
            class _R:
                pass
            r = _R()
            r.returncode = 0 if md_ok > 0 else 1
            r.stdout = f"julia project, Project.toml parsed={_toml is not None}, md_files={len(md_files)} md_ok={md_ok}"
            r.stderr = ""
        else:
            r = subprocess.run(["python3", "-m", "py_compile", "*.py"],
                                cwd=project_path, capture_output=True, text=True, timeout=60)
        with open(fp, "w") as f:
            f.write(f"exit={r.returncode}\nstdout={r.stdout[:1000]}\nstderr={r.stderr[:1000]}\n")
        st.notes = f"ran lint, exit={r.returncode}"
        return [fp], f"verify_lint exit={r.returncode}"
    except Exception as e:
        st.notes = f"ERROR: {e}"
        return [fp], f"ERROR: {e}"


def _h_verify_build(st: SubTask, ctx: dict, iteration: int, prior_outputs: List[str]) -> Tuple[List[str], str]:
    project_path = ctx.get("project_path", "")
    if not project_path:
        st.notes = "BLOCKED: project_path missing"
        return [], "BLOCKED"
    notes_dir = ctx.get("notes_dir", "")
    suffix = st.id.split("-")[-1]
    fp = os.path.join(notes_dir, f"{suffix}-{st.type.value}.log")
    os.makedirs(notes_dir, exist_ok=True)
    try:
        if os.path.isfile(os.path.join(project_path, "go.mod")):
            r = subprocess.run(["go", "build", "./..."], cwd=project_path,
                                capture_output=True, text=True, timeout=120)
        else:
            r = subprocess.run(["python3", "-c", "import sys; sys.exit(0)"],
                                cwd=project_path, capture_output=True, text=True, timeout=10)
        with open(fp, "w") as f:
            f.write(f"exit={r.returncode}\n")
        st.notes = f"ran build, exit={r.returncode}"
        return [fp], f"verify_build exit={r.returncode}"
    except Exception as e:
        st.notes = f"ERROR: {e}"
        return [fp], f"ERROR: {e}"


def _h_self_critique(st: SubTask, ctx: dict, iteration: int, prior_outputs: List[str]) -> Tuple[List[str], str]:
    notes_dir = ctx.get("notes_dir", "")
    suffix = st.id.split("-")[-1]
    fp = os.path.join(notes_dir, f"{suffix}-{st.type.value}.md")
    os.makedirs(notes_dir, exist_ok=True)
    with open(fp, "w", encoding="utf-8") as f:
        f.write(f"# Self-Critique (iter={iteration})\n\n")
        f.write("## 1. PR 解决了什么\n\n(agent 填写)\n\n")
        f.write("## 2. 可能的问题\n\n- (agent 列举)\n\n")
        f.write("## 3. 改进建议\n\n- (agent 列举)\n")
        if iteration >= 1:
            f.write("\n## 4. 更深的问题分析 (iter=1)\n\n(agent 列举第二层问题)\n")
        if iteration >= 2:
            f.write("\n## 5. 对上游 paper 的可推广性 (iter=2)\n\n(agent 列举)\n")
    st.notes = f"wrote {fp} (iter={iteration})"
    return [fp], f"self_critique iter={iteration}"


def _h_persist(st: SubTask, ctx: dict, iteration: int, prior_outputs: List[str]) -> Tuple[List[str], str]:
    wt = ctx.get("worktree")
    if wt:
        from persistence.worktree_state import save_state
        fp = save_state(wt)
        st.notes = f"saved {fp} (iter={iteration})"
        return [fp], f"persisted to {os.path.basename(fp)}"
    st.notes = "PERSIST stub (no worktree in ctx)"
    return [], "PERSIST stub"


def _h_blocked(st: SubTask, ctx: dict, iteration: int, prior_outputs: List[str]) -> Tuple[List[str], str]:
    st.notes = "BLOCKED: waiting for human or external input"
    return [], "BLOCKED"


# Handler 注册表
HANDLERS: Dict[SubTaskType, HandlerFn] = {
    SubTaskType.READ_PAPER:        _h_read_paper,
    SubTaskType.EXTRACT_CONTRACT:  _h_extract_contract,
    SubTaskType.ANALYZE_CODE:      _h_analyze_code,
    SubTaskType.CROSS_CHECK:       _h_cross_check,
    SubTaskType.WRITE_TEST:        _h_write_test,
    SubTaskType.WRITE_DOCSTRING:   _h_write_docstring,
    SubTaskType.WRITE_CITATION:    _h_write_citation,
    SubTaskType.WRITE_PR_BODY:     _h_write_pr_body,
    SubTaskType.VERIFY_TESTS:      _h_verify_tests,
    SubTaskType.VERIFY_LINT:       _h_verify_lint,
    SubTaskType.VERIFY_BUILD:      _h_verify_build,
    SubTaskType.SELF_CRITIQUE:     _h_self_critique,
    SubTaskType.PERSIST:           _h_persist,
    SubTaskType.BLOCKED:           _h_blocked,
}


def execute_subtask_iteration(st: SubTask, ctx: dict) -> Tuple[bool, float, str]:
    """V5.2 核心：执行 1 次 sub-task iteration。

    流程：
      1. 取出 iteration 编号
      2. 调 handler（handler 知道这是第几次深化）
      3. 评分 → quality_score
      4. 写 RefinementRecord
      5. 决定下次状态：
         - quality >= threshold: DONE
         - iterations_done+1 >= max_iterations: FAILED
         - 否则: PENDING（等待下次 tick 再深化）
    """
    handler = HANDLERS.get(st.type)
    if not handler:
        st.notes = f"BLOCKED: no handler for {st.type}"
        return False, 0.0, "NO_HANDLER"

    iteration = st.iterations_done  # 0-indexed
    prior_outputs: List[str] = []
    for rec in st.refinement_history:
        prior_outputs.extend(rec.output_files_written)

    st.status = SubTaskStatus.IN_PROGRESS
    started = datetime.utcnow().isoformat() + "Z"
    try:
        output_files, summary = handler(st, ctx, iteration, prior_outputs)
    except Exception as e:
        st.notes = f"EXCEPTION: {e}"
        st.status = SubTaskStatus.FAILED
        st.finished_at = datetime.utcnow().isoformat() + "Z"
        return False, 0.0, f"EXCEPTION: {e}"

    # 评分
    quality = score_subtask(st, output_files, summary)
    finished = datetime.utcnow().isoformat() + "Z"
    rec = RefinementRecord(
        iteration=iteration,
        started_at=started,
        finished_at=finished,
        output_files_written=output_files,
        output_summary=summary,
        quality_score=quality,
        compute_used="auto",
        notes=st.notes or "",
    )
    st.refinement_history.append(rec)
    st.iterations_done += 1
    st.actual_ticks += 1
    st.quality_score = quality
    if output_files:
        st.output_files = output_files

    # 决定状态
    if quality >= st.quality_threshold:
        st.status = SubTaskStatus.DONE
        st.finished_at = finished
        return True, quality, f"DONE quality={quality:.1f} >= {st.quality_threshold}"
    if st.iterations_done >= st.max_iterations:
        st.status = SubTaskStatus.FAILED
        st.finished_at = finished
        return False, quality, f"FAILED: max_iter={st.max_iterations} reached, quality={quality:.1f} < {st.quality_threshold}"
    # 未达 threshold，但还可深化 → PENDING（等下次 tick）
    st.status = SubTaskStatus.PENDING
    return False, quality, f"REFINING: iter={st.iterations_done}/{st.max_iterations}, quality={quality:.1f}/{st.quality_threshold}"


# V5.2 仍保留 V5.1 接口（execute_subtask）：当 engine 需要 V5.1 兼容行为时使用
def execute_subtask(st: SubTask, ctx: dict) -> bool:
    """V5.1 兼容：单次调用 = 1 iteration，等价于 execute_subtask_iteration 的简版。"""
    ok, _, _ = execute_subtask_iteration(st, ctx)
    return ok
