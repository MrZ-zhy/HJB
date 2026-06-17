"""V5 arXiv 论文源。

V5 与 V4 的根本差异：触发源从 upstream issue 池 → arXiv + 期刊。
"""
from __future__ import annotations

import os
import time
from typing import List, Optional

# 避免缺包崩引擎
try:
    import arxiv  # type: ignore
except ImportError:  # pragma: no cover
    arxiv = None  # type: ignore

from core.models import Paper


def search_arxiv(query: str, max_results: int = 10, since_year: int = 2024) -> List[Paper]:
    """从 arXiv 拉取论文列表（按提交日期降序）。"""
    if arxiv is None:
        raise RuntimeError("arxiv 包未安装；pip install arxiv")

    client = arxiv.Client(page_size=min(20, max_results), delay_seconds=2.0, num_retries=3)
    s = arxiv.Search(
        query=query,
        max_results=max_results,
        sort_by=arxiv.SortCriterion.SubmittedDate,
        sort_order=arxiv.SortOrder.Descending,
    )
    out: List[Paper] = []
    seen: set = set()
    for r in client.results(s):
        if r.published.year < since_year:
            continue
        if r.entry_id in seen:
            continue
        seen.add(r.entry_id)
        # 从 entry_id 提取 arXiv ID（如 http://arxiv.org/abs/2409.05894v1 → 2409.05894）
        arxiv_id = r.entry_id.rsplit("/", 1)[-1]
        arxiv_id = arxiv_id.split("v")[0] if "v" in arxiv_id else arxiv_id
        out.append(Paper(
            arxiv_id=arxiv_id,
            title=r.title.strip().replace("\n", " "),
            authors=[a.name for a in r.authors[:5]],
            year=r.published.year,
            summary=r.summary.strip().replace("\n", " ")[:600],
            primary_category=r.primary_category,
            pdf_url=r.pdf_url,
        ))
    return out


def fetch_one(arxiv_id: str) -> Optional[Paper]:
    """按 arXiv ID 抓取单篇（去掉版本号后缀）。"""
    if arxiv is None:
        return None
    aid = arxiv_id.split("v")[0]
    client = arxiv.Client(page_size=1, delay_seconds=2.0, num_retries=3)
    s = arxiv.Search(id_list=[aid], max_results=1)
    for r in client.results(s):
        return Paper(
            arxiv_id=aid,
            title=r.title.strip().replace("\n", " "),
            authors=[a.name for a in r.authors[:5]],
            year=r.published.year,
            summary=r.summary.strip().replace("\n", " ")[:600],
            primary_category=r.primary_category,
            pdf_url=r.pdf_url,
        )
    return None
