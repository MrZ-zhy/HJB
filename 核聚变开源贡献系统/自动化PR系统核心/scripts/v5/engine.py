"""V5 统一 CLI 入口。"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

# 让脚本模式 `python3 v5/engine.py` 也能 import 兄弟模块
_PKG_PARENT = Path(__file__).resolve().parent.parent
if str(_PKG_PARENT) not in sys.path:
    sys.path.insert(0, str(_PKG_PARENT))


def cmd_tick(args: argparse.Namespace) -> int:
    from core.orchestrator import Orchestrator
    orch = Orchestrator()
    result = orch.tick(dry_run=args.dry_run)
    out = {
        "version": "5",
        "started_at": orch.started_at,
        "finished_at": __import__("datetime").datetime.utcnow().isoformat() + "Z",
        "overall_ok": result.overall_ok,
        "steps": result.steps,
        "actions_taken": result.actions_taken,
        "events": result.events,
        "next_action_hint": result.next_action_hint,
    }
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0 if result.overall_ok else 1


def cmd_reports(_: argparse.Namespace) -> int:
    """列出所有 pre-PR 报告。"""
    reports_dir = Path("核聚变开源贡献系统/V5/REPORTS")
    if not reports_dir.is_dir():
        print(json.dumps({"reports": []}, ensure_ascii=False, indent=2))
        return 0
    out = []
    for p in sorted(reports_dir.glob("*.md")):
        out.append({
            "id": p.stem,
            "path": str(p),
            "size": p.stat().st_size,
        })
    print(json.dumps({"reports": out}, ensure_ascii=False, indent=2))
    return 0


def cmd_validate(_: argparse.Namespace) -> int:
    from core.state_machine import self_check
    ok, msg = self_check()
    print(json.dumps({"ok": ok, "msg": msg}, ensure_ascii=False, indent=2))
    return 0 if ok else 1


def cmd_papers(_: argparse.Namespace) -> int:
    """列出 paper log。"""
    fp = Path("核聚变开源贡献系统/V5/PAPER_LOG/papers_2024plus.json")
    if not fp.is_file():
        print(json.dumps({"papers": []}, ensure_ascii=False, indent=2))
        return 0
    raw = json.load(open(fp, encoding="utf-8"))
    flat = []
    for proj, papers in raw.items():
        for p in papers:
            if "error" in p:
                continue
            flat.append({"project": proj, **p})
    print(json.dumps({"papers": flat, "count": len(flat)}, ensure_ascii=False, indent=2))
    return 0


def main(argv: list = None) -> int:
    ap = argparse.ArgumentParser(description="V5 paper-aware 核聚变贡献引擎")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_tick = sub.add_parser("tick", help="执行一个 V5 tick")
    p_tick.add_argument("--dry-run", action="store_true")
    p_tick.set_defaults(func=cmd_tick)

    sub.add_parser("reports", help="列出所有 pre-PR 报告").set_defaults(func=cmd_reports)
    sub.add_parser("validate", help="V5 状态机自检").set_defaults(func=cmd_validate)
    sub.add_parser("papers", help="列出 paper log").set_defaults(func=cmd_papers)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
