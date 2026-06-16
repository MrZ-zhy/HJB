"""V5 pr_type_classifier：根据 (论文, 项目) 配对，决定 PR 类型。"""
from __future__ import annotations

from typing import List

from core.models import Action, EngineState, PRType, Paper, Project


# 规则（V5 注册表；类比 V4 decision_matrix.RULES）
def _has_python_entrypoint(proj: Project) -> bool:
    return proj.language in ("Python", "Go", "Julia")  # 沙盒可跑的


def classify(paper: Paper, proj: Project) -> PRType:
    """根据 (论文, 项目) 特征，决定 PR 类型。"""
    # 规则 R1: 项目有 Python/Go/Julia 入口 + gap → T1 复现性测试
    if paper.status.value == "gap" and _has_python_entrypoint(proj):
        return PRType.T1
    # 规则 R2: 论文是项目自己的 → T2 文档增强 + T5 citation
    if proj.name.lower() in paper.title.lower() or paper.arxiv_id in proj.notes:
        return PRType.T2
    # 规则 R3: 任何状态 + 项目有 docs → T5 citation 补全
    if proj.keywords:
        return PRType.T5
    # 默认
    return PRType.T2


class PRTypeClassifierStrategy:
    name = "pr_type_classifier"

    def evaluate(self, state: EngineState) -> List[Action]:
        actions: List[Action] = []
        for proj in state.projects:
            for aid in proj.candidate_papers:
                paper = next((p for p in state.papers if p.arxiv_id == aid), None)
                if not paper:
                    continue
                pt = classify(paper, proj)
                actions.append(Action(
                    name="pr_type_classifier",
                    priority=55,
                    target=f"{proj.name}/{aid}",
                    rationale=f"分类 {pt.value}（paper={paper.title[:50]}）",
                ))
        return actions
