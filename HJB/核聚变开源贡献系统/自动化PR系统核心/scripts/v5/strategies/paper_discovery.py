"""V5 paper_discovery strategy：找论文。"""
from __future__ import annotations

from typing import List

from core.models import Action, EngineState, Paper
from sources import arxiv as arxiv_src


# 每项目的 arXiv 检索式（V5 简化：每项目 1-2 条主检索式）
QUERIES: dict = {
    "OpenReactor": [
        'cat:physics.plasm-ph AND (abs:"inertial electrostatic confinement" OR abs:"IEC fusor" OR abs:"Langmuir probe" OR abs:"neutron yield" OR abs:"sheath")',
    ],
    "FUSE": [
        'cat:physics.plasm-ph AND (abs:"integrated modeling" OR abs:"systems code" OR abs:"pilot plant" OR abs:"first principle" OR abs:"drift kinetic")',
    ],
    "gym-torax": [
        'cat:physics.plasm-ph AND (abs:"reinforcement learning" OR abs:gymnasium) AND (abs:tokamak OR abs:plasma OR abs:"magnetic confinement")',
    ],
}


class PaperDiscoveryStrategy:
    name = "paper_discovery"

    def evaluate(self, state: EngineState) -> List[Action]:
        actions: List[Action] = []
        for proj in state.projects:
            queries = QUERIES.get(proj.name, [])
            if not queries:
                continue
            for q in queries:
                actions.append(Action(
                    name="paper_discovery",
                    priority=70,
                    target=proj.name,
                    rationale=f"检索 arXiv：{q[:80]}...",
                ))
        return actions

    def run(self, project_name: str) -> List[Paper]:
        """实际执行：拉论文。"""
        queries = QUERIES.get(project_name, [])
        out: List[Paper] = []
        for q in queries:
            try:
                papers = arxiv_src.search_arxiv(q, max_results=8, since_year=2024)
                out.extend(papers)
            except Exception as e:
                # 错误隔离（axiom A3）
                pass
        # 去重
        seen: set = set()
        dedup: List[Paper] = []
        for p in out:
            if p.arxiv_id in seen:
                continue
            seen.add(p.arxiv_id)
            dedup.append(p)
        return dedup[:8]
