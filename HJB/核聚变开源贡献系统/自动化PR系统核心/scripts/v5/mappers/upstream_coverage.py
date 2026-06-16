"""V5 上游覆盖检查：扫 upstream 代码 + docs，确认论文是否已被覆盖。"""
from __future__ import annotations

import os
from typing import Dict, List, Set

from core.models import Paper, PaperStatus


# 扫的文件后缀（沙盒可处理的语言）
SCAN_EXTS = {".md", ".go", ".py", ".jl", ".rst", ".txt"}

# 大文件跳过（>500KB 可能是数据/资源）
MAX_FILE_SIZE = 500_000


def _iter_text_files(root: str) -> List[str]:
    """递归列出 root 下所有文本文件（排除 .git）。"""
    out: List[str] = []
    for dirpath, dirnames, filenames in os.walk(root):
        # 跳过 .git
        dirnames[:] = [d for d in dirnames if d != ".git"]
        for f in filenames:
            ext = os.path.splitext(f)[1].lower()
            if ext not in SCAN_EXTS:
                continue
            fp = os.path.join(dirpath, f)
            try:
                if os.path.getsize(fp) > MAX_FILE_SIZE:
                    continue
            except OSError:
                continue
            out.append(fp)
    return out


def _read_corpus(files: List[str]) -> str:
    parts: List[str] = []
    for fp in files:
        try:
            with open(fp, encoding="utf-8", errors="ignore") as fh:
                parts.append(fh.read().lower())
        except Exception:
            pass
    return " ".join(parts)


def check_paper_coverage(paper: Paper, project_path: str, project_keywords: List[str]) -> PaperStatus:
    """检查 1 篇论文在 1 个项目的 upstream 中是否被覆盖。

    判定逻辑（V5 简化版）：
    - 论文 ID 出现 → covered
    - 论文标题/作者关键词任一命中 ≥ 3 次 → partial
    - 项目关键词 + 论文摘要共享 token ≥ 2 → partial
    - 否则 → gap
    """
    if not os.path.isdir(project_path):
        return PaperStatus.DISCOVERED  # 路径无效，不判定

    files = _iter_text_files(project_path)
    corpus = _read_corpus(files)

    # 1. arxiv ID 命中
    if paper.arxiv_id.lower() in corpus:
        return PaperStatus.COVERED

    # 2. 论文标题 token 命中
    title_tokens = [t for t in paper.title.lower().split() if len(t) > 4]
    title_hits = sum(1 for t in title_tokens if t in corpus)
    if title_hits >= 3:
        return PaperStatus.COVERED
    if title_hits >= 1:
        return PaperStatus.PARTIAL

    # 3. 项目关键词 + 摘要 token 交集
    summary_tokens = {t for t in paper.summary.lower().split() if len(t) > 5}
    kw_hits = sum(1 for k in project_keywords if k.lower() in summary_tokens and k.lower() in corpus)
    if kw_hits >= 2:
        return PaperStatus.PARTIAL

    return PaperStatus.GAP


def batch_coverage(papers: List[Paper], project_path: str, project_keywords: List[str]) -> Dict[str, PaperStatus]:
    """批量覆盖检查。"""
    return {p.arxiv_id: check_paper_coverage(p, project_path, project_keywords) for p in papers}
