"""V5 pre_pr_report strategy：生成 pre-PR 报告（人工 gate 物料）。

V5 与 V4 关键差异：pre-PR 报告 = 强制 gate。
  - 状态 AWAITING_GATE：报告已生成，未通过人工批准之前**不**调 patch generator
  - 报告路径：`核聚变开源贡献系统/V5/REPORTS/<id>-<project>-<topic>.md`
"""
from __future__ import annotations

import os
from datetime import datetime
from typing import List

from core.models import Action, EngineState, Paper, PrePRReport, Project, PRType


REPORTS_DIR = "核聚变开源贡献系统/V5/REPORTS"


def _next_report_id(existing: List[PrePRReport]) -> str:
    n = len(existing) + 1
    return f"{n:03d}"


def _render_report(report: PrePRReport, proj: Project, paper: Paper) -> str:
    return f"""# Pre-PR 报告 {report.report_id}

**生成时间**: {datetime.utcnow().isoformat()}Z
**项目**: {proj.name}
**仓库**: {proj.repo}
**目标 PR 类型**: {report.pr_type.value}
**沙盒可跑性**: {'✅ 是' if report.sandbox_runnable else '❌ 否'}

---

## 1. 项目是什么 / 解决什么问题

{proj.notes or '(从大方向/各项目评估/<name>.md 读取)'}

---

## 2. 候选论文

| arXiv ID | 标题 | 年份 | primary_category |
|----------|------|------|------------------|
| `{paper.arxiv_id}` | {paper.title} | {paper.year} | {paper.primary_category} |

**论文摘要**:
> {paper.summary}

---

## 3. 上游覆盖检查

**覆盖状态**: `{paper.status.value}`
**说明**: {paper.coverage_notes or '(见 V5/REPORTS 上游覆盖检查报告)'}

---

## 4. PR 计划

**目标文件**:
{chr(10).join('- `' + f + '`' for f in report.target_files)}

**Rationale**:
{report.rationale}

**Gap 分析**:
{report.gap_analysis}

---

## 5. 期望影响

{report.expected_impact}

---

## 6. 风险

{chr(10).join('- ' + r for r in report.risks)}

---

## 7. 等待批准

- [ ] 用户确认论文选择
- [ ] 用户确认 PR 类型
- [ ] 用户确认目标文件
- [ ] 用户确认沙盒可跑性

**人工 gate 通过前不会生成 patch / 不会 push / 不会 create PR。**
"""


def write_report(report: PrePRReport, proj: Project, paper: Paper, root: str = ".") -> str:
    """把 pre-PR 报告写到 V5/REPORTS/，返回路径。"""
    rel_dir = os.path.join(root, REPORTS_DIR)
    os.makedirs(rel_dir, exist_ok=True)
    filename = f"{report.report_id}-{proj.name}-{report.pr_type.value}.md"
    fp = os.path.join(rel_dir, filename)
    with open(fp, "w", encoding="utf-8") as f:
        f.write(_render_report(report, proj, paper))
    return fp


class PrePRReportStrategy:
    name = "pre_pr_report"

    def evaluate(self, state: EngineState) -> List[Action]:
        """评估：对每个 (项目, gap 论文) 生成 pre-PR 报告。"""
        actions: List[Action] = []
        for proj in state.projects:
            if proj.state.value in {"merged", "closed", "awaiting_gate"}:
                continue
            for aid in proj.candidate_papers:
                paper = next((p for p in state.papers if p.arxiv_id == aid), None)
                if not paper or paper.status.value not in {"gap", "partial"}:
                    continue
                actions.append(Action(
                    name="pre_pr_report",
                    priority=60,
                    target=f"{proj.name}/{aid}",
                    rationale=f"为 (project={proj.name}, paper={aid}) 生成 pre-PR 报告",
                ))
        return actions

    def build_report(self, state: EngineState, proj_name: str, arxiv_id: str) -> PrePRReport:
        """构造 1 份 PrePRReport（不写文件）。"""
        proj = state.project_by_name(proj_name)
        paper = next((p for p in state.papers if p.arxiv_id == arxiv_id), None)
        if not proj or not paper:
            raise ValueError(f"未找到 project={proj_name} 或 paper={arxiv_id}")
        rid = _next_report_id(state.pre_pr_reports)
        return PrePRReport(
            report_id=rid,
            project=proj.name,
            paper_arxiv_id=paper.arxiv_id,
            paper_title=paper.title,
            pr_type=PRType.T1,  # 默认；classifier 决定后覆盖
            target_files=[],  # 由 patch_generator 阶段填
            rationale=f"论文 {paper.arxiv_id} 标题：{paper.title[:60]}",
            gap_analysis=paper.coverage_notes or "未覆盖",
            expected_impact="上游覆盖率提升；维护者感受到 AI 引用了论文",
            risks=["论文与代码不对应", "PR 描述不够清楚"],
            sandbox_runnable=proj.sandbox_runnable,
        )
