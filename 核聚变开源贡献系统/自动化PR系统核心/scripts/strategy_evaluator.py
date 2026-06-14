#!/usr/bin/env python3
"""
strategy_evaluator.py - 策略模式自动评估器

【治本目标】
进度表 PR 收益与策略反馈 表 + 贡献状态机 §策略模式自动调整 文档化的触发规则
必须由代码实际评估，否则 STRATEGY_MODE 永远不会被自动改写。

【使用方式】
  python3 strategy_evaluator.py                  # 评估 + dry-run 显示
  python3 strategy_evaluator.py --apply          # 评估 + 写入进度表
  python3 strategy_evaluator.py --json           # 输出 JSON 报告

【触发规则】（与贡献状态机.md §策略模式自动调整 严格同步）
1. 连续 3 PR 在 7 天内 review 数 = 0  -> aggressive -> conservative
2. 合并率 >= 60% 累计 >= 5 PR         -> conservative -> aggressive
3. 同一仓库连续 2 PR 被 closed(rejected) -> aggressive -> conservative
4. 错误预算耗尽                       -> aggressive -> conservative
5. 单 PR review 周期 > 30 天          -> aggressive -> conservative
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

REPO_ROOT = Path(os.environ.get("HJB_ROOT", "/workspace/HJB"))
PROG_PATH = REPO_ROOT / "核聚变开源贡献系统" / "进度表.md"

sys.path.insert(0, str(Path(__file__).parent))
from engine_helper import parse_progress, update_fields  # noqa: E402


def _read_pr_outcomes(prog: Path = PROG_PATH) -> List[Dict[str, str]]:
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


def _read_strategy_mode(prog: Path = PROG_PATH) -> str:
    data = parse_progress(prog)
    return data.get("_codeblock", {}).get("STRATEGY_MODE", "aggressive").strip()


def _read_error_budget(prog: Path = PROG_PATH) -> str:
    data = parse_progress(prog)
    return data.get("_codeblock", {}).get("ERROR_BUDGET_STATUS", "normal").strip()


def evaluate_triggers(pr_rows: List[Dict[str, str]], current_mode: str,
                      error_budget: str) -> List[Dict[str, Any]]:
    fired: List[Dict[str, Any]] = []
    now = datetime.now(timezone.utc)

    def parse_date(s: str) -> Optional[datetime]:
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

    recent = []
    for r in pr_rows:
        d = parse_date(r.get("创建日", ""))
        if d and now - d <= timedelta(days=7):
            recent.append(r)
    if len(recent) >= 3 and all(r.get("review 周期", "—") in ("—", "-", "") for r in recent[-3:]):
        if current_mode == "aggressive":
            fired.append({
                "rule": "R1_stale_3_in_7d",
                "from": "aggressive", "to": "conservative",
                "reason": f"近 7 天内 {len(recent)} 个 PR 均无 review 活动",
            })

    terminal = [r for r in pr_rows if r.get("终态", "") not in ("", "—", "open（pending）", "open(pending)")]
    merged = [r for r in terminal if "merged" in r.get("终态", "").lower() or r.get("终态", "") == "merged"]
    if len(terminal) >= 5 and len(merged) / max(len(terminal), 1) >= 0.6:
        if current_mode == "conservative":
            fired.append({
                "rule": "R2_merge_rate_60pct",
                "from": "conservative", "to": "aggressive",
                "reason": f"累计 {len(terminal)} PR，合并率 {len(merged)/len(terminal):.0%}",
            })

    by_repo: Dict[str, List[Dict[str, str]]] = {}
    for r in pr_rows:
        repo = r.get("项目", "").strip()
        if repo:
            by_repo.setdefault(repo, []).append(r)
    for repo, rs in by_repo.items():
        if len(rs) < 2:
            continue
        last2 = rs[-2:]
        if all(("closed" in r.get("终态", "").lower() or "rejected" in r.get("终态", "").lower())
               for r in last2):
            if current_mode == "aggressive":
                fired.append({
                    "rule": "R3_repo_2x_rejected",
                    "from": "aggressive", "to": "conservative",
                    "reason": f"仓库 {repo} 连续 2 PR 被 closed/rejected",
                })

    if error_budget == "depleted" and current_mode == "aggressive":
        fired.append({
            "rule": "R4_budget_depleted",
            "from": "aggressive", "to": "conservative",
            "reason": "错误预算耗尽",
        })

    for r in pr_rows[-1:]:
        cycle = r.get("review 周期", "").strip()
        m = re.match(r"(\d+)", cycle)
        if m and int(m.group(1)) > 30:
            if current_mode == "aggressive":
                fired.append({
                    "rule": "R5_long_review_cycle",
                    "from": "aggressive", "to": "conservative",
                    "reason": f"PR {r.get('PR', '?')} review 周期 {cycle} > 30 天",
                })

    return fired


def run_evaluation(prog: Path = PROG_PATH, apply: bool = False) -> Dict[str, Any]:
    rows = _read_pr_outcomes(prog)
    cur_mode = _read_strategy_mode(prog)
    budget = _read_error_budget(prog)
    fired = evaluate_triggers(rows, cur_mode, budget)

    result: Dict[str, Any] = {
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "current_mode": cur_mode,
        "error_budget": budget,
        "pr_rows_count": len(rows),
        "fired_triggers": fired,
        "applied": False,
    }
    if fired and apply:
        new_mode = fired[-1]["to"]
        update_fields({"STRATEGY_MODE": new_mode}, prog)
        result["applied"] = True
        result["new_mode"] = new_mode
    elif fired:
        result["would_change_to"] = fired[-1]["to"]
    return result


def main(argv: List[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv[1:])
    result = run_evaluation(apply=args.apply)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"当前 STRATEGY_MODE: {result['current_mode']}")
        print(f"错误预算: {result['error_budget']}")
        print(f"PR 收益行数: {result['pr_rows_count']}")
        if result["fired_triggers"]:
            print(f"\n命中触发器 ({len(result['fired_triggers'])}):")
            for t in result["fired_triggers"]:
                print(f"  [{t['rule']}] {t['from']} -> {t['to']}  //  {t['reason']}")
            if result.get("applied"):
                print(f"\n已写入: STRATEGY_MODE = {result['new_mode']}")
            else:
                tgt = result.get("would_change_to")
                print(f"\n将改为: {tgt} (用 --apply 写入)")
        else:
            print("\n无触发器命中，保持当前模式")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
