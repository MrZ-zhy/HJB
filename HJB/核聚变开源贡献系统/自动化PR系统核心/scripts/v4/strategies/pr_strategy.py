"""V4 PR 策略（v2 strategy_evaluator.py 升级版）。

5 条触发器由代码硬编码（与 贡献状态机.md §策略模式自动调整 严格同步）。
未来可改造为外部规则文件。
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..core.event_bus import EventBus, Events
from ..core.models import Action, EngineState, StrategyMode


_REPO_ROOT = Path(__file__).resolve().parents[4]
PROG_PATH = _REPO_ROOT / "进度表.md"


def _read_pr_rows(prog: Path = PROG_PATH) -> List[Dict[str, str]]:
    text = prog.read_text(encoding="utf-8")
    lines = text.splitlines()
    in_section = False
    rows: List[Dict[str, str]] = []
    headers: List[str] = []
    for line in lines:
        if line.startswith("## ") and "PR 收益" in line:
            in_section = True
            continue
        if in_section and line.startswith("## "):
            break
        if not in_section or not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if re.match(r"^[\s|:-]+$", cells[0] if cells else ""):
            continue
        if not headers:
            headers = cells
            continue
        if len(cells) != len(headers):
            continue
        rows.append(dict(zip(headers, cells)))
    return rows


def _parse_date(s: str) -> Optional[datetime]:
    s = (s or "").strip()
    if not s or s in ("—", "-"):
        return None
    try:
        d = datetime.fromisoformat(s.replace("Z", "+00:00"))
        if d.tzinfo is None:
            d = d.replace(tzinfo=timezone.utc)
        return d
    except ValueError:
        return None


def _evaluate_triggers(rows: List[Dict[str, str]], mode: StrategyMode,
                       budget_ok: bool) -> List[Dict[str, Any]]:
    fired: List[Dict[str, Any]] = []
    now = datetime.now(timezone.utc)

    recent = []
    for r in rows:
        d = _parse_date(r.get("创建日", ""))
        if d and now - d <= timedelta(days=7):
            recent.append(r)
    if len(recent) >= 3 and all(r.get("review 周期", "—") in ("—", "-", "") for r in recent[-3:]):
        if mode == StrategyMode.AGGRESSIVE:
            fired.append({
                "rule": "R1_stale_3_in_7d",
                "from": "aggressive", "to": "conservative",
                "reason": f"近 7 天内 {len(recent)} 个 PR 均无 review 活动",
            })

    terminal = [r for r in rows if r.get("终态", "") not in ("", "—", "open（pending）", "open(pending)")]
    merged = [r for r in terminal if "merged" in r.get("终态", "").lower() or r.get("终态", "") == "merged"]
    if len(terminal) >= 5 and len(merged) / max(len(terminal), 1) >= 0.6:
        if mode == StrategyMode.CONSERVATIVE:
            fired.append({
                "rule": "R2_merge_rate_60pct",
                "from": "conservative", "to": "aggressive",
                "reason": f"累计 {len(terminal)} PR，合并率 {len(merged)/len(terminal):.0%}",
            })

    by_repo: Dict[str, List[Dict[str, str]]] = {}
    for r in rows:
        repo = r.get("项目", "").strip()
        if repo:
            by_repo.setdefault(repo, []).append(r)
    for repo, rs in by_repo.items():
        if len(rs) < 2:
            continue
        last2 = rs[-2:]
        if all(("closed" in r.get("终态", "").lower() or "rejected" in r.get("终态", "").lower())
               for r in last2):
            if mode == StrategyMode.AGGRESSIVE:
                fired.append({
                    "rule": "R3_repo_2x_rejected",
                    "from": "aggressive", "to": "conservative",
                    "reason": f"仓库 {repo} 连续 2 PR 被 closed/rejected",
                })

    if not budget_ok and mode == StrategyMode.AGGRESSIVE:
        fired.append({
            "rule": "R4_budget_depleted",
            "from": "aggressive", "to": "conservative",
            "reason": "错误预算耗尽",
        })

    for r in rows[-1:]:
        cycle = r.get("review 周期", "").strip()
        m = re.match(r"(\d+)", cycle)
        if m and int(m.group(1)) > 30:
            if mode == StrategyMode.AGGRESSIVE:
                fired.append({
                    "rule": "R5_long_review_cycle",
                    "from": "aggressive", "to": "conservative",
                    "reason": f"PR {r.get('PR', '?')} review 周期 {cycle} > 30 天",
                })

    return fired


class PRStrategy:
    name = "pr_strategy"

    def evaluate(self, state: EngineState) -> List[Action]:
        """评估 PR 触发器 → 可能改写 STRATEGY_MODE。"""
        rows = _read_pr_rows()
        budget_ok = state.metrics.budget_ok
        fired = _evaluate_triggers(rows, state.strategy_mode, budget_ok)
        if not fired:
            return []
        last = fired[-1]
        return [Action(
            name="pr_strategy",
            priority=70,
            rationale=f"PR trigger {last['rule']}: {last['reason']}",
            payload={
                "fired": fired,
                "new_mode": last["to"],
            },
        )]

    def execute(self, state: EngineState, action: Action, bus: EventBus) -> None:
        """应用模式切换 + emit event。"""
        new_mode = action.payload.get("new_mode", state.strategy_mode.value)
        old_mode = state.strategy_mode.value
        state.strategy_mode = StrategyMode(new_mode)
        bus.emit(Event(Events.STRATEGY_MODE_CHANGED, {"from": old_mode, "to": new_mode}))
