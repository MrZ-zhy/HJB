#!/usr/bin/env python3
"""
project_rotator.py - 项目轮换队列执行器

【治本目标】
进度表.md §项目轮换队列 只是装饰表。本脚本在启动新一轮时实际从队列中
选出下一项目，原子写入「当前开发项目」段。

【使用方式】
  python3 project_rotator.py                  # dry-run：显示将选哪个项目
  python3 project_rotator.py --apply          # 实际写入进度表
  python3 project_rotator.py --no-skip-current # 强制重选当前项目（用于重启卡死项目）

【选项目规则】
1. 默认：跳过 §当前开发项目 的项目 AND 跳过状态为"进行中"的项目
2. 优先级：按 §项目轮换队列 顺序字段排序
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

REPO_ROOT = Path(os.environ.get("HJB_ROOT", "/workspace/HJB"))
PROG_PATH = REPO_ROOT / "核聚变开源贡献系统" / "进度表.md"

sys.path.insert(0, str(Path(__file__).parent))
from engine_helper import parse_progress, update_fields  # noqa: E402


def _read_current_project(prog: Path = PROG_PATH) -> Dict[str, str]:
    data = parse_progress(prog)
    return dict(data.get("当前开发项目", {}))


def _read_queue(prog: Path = PROG_PATH) -> List[Dict[str, str]]:
    """从 §项目轮换队列 段读取队列。多列表格，仅取首列项目名 + 状态。"""
    text = prog.read_text(encoding="utf-8")
    lines = text.splitlines()
    in_section = False
    rows: List[Dict[str, str]] = []
    for line in lines:
        if line.startswith("## ") and "项目轮换队列" in line:
            in_section = True
            continue
        if in_section and line.startswith("## "):
            break
        if not in_section or not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if re.match(r"^[\s|:-]+$", cells[0] if cells else ""):
            continue
        if not rows and cells[0] == "顺序":
            continue
        if len(cells) >= 3 and re.match(r"^\d+$", cells[0]):
            rows.append({"顺序": cells[0], "项目": cells[1], "状态": cells[3] if len(cells) > 3 else ""})
    return rows


def select_next(skip_current: bool = True) -> Optional[Dict[str, str]]:
    """按规则选下一项目。返回 {顺序, 项目, 状态} 或 None。

    skip_current=True（默认）：跳过当前项目 AND 跳过状态为"进行中"的项目
    skip_current=False：强制选当前项目（绕过上述两个跳过条件）
    """
    queue = _read_queue()
    if not queue:
        return None
    current = _read_current_project().get("项目名称", "").strip() if skip_current else ""
    for row in queue:
        name = row.get("项目", "").strip()
        status = row.get("状态", "")
        if skip_current:
            if name == current:
                continue
            if any(token in status for token in ("🔄", "进行中", "进行中（PR")):
                continue
        return row
    return None


def rotate(prog: Path = PROG_PATH, apply: bool = False, skip_current: bool = True) -> Dict[str, Any]:
    nxt = select_next(skip_current=skip_current)
    cur = _read_current_project()

    result: Dict[str, Any] = {
        "current": cur.get("项目名称"),
        "selected": nxt.get("项目") if nxt else None,
        "selected_order": nxt.get("顺序") if nxt else None,
        "applied": False,
    }

    if nxt and apply:
        new_name = nxt["项目"]
        repo_map = {
            "OpenReactor": "natesales/openreactor",
            "TORAX": "google-deepmind/torax",
            "OpenMC": "openmc-dev/openmc",
            "PlasmaGym": "PlasmaGym/plasmagym",
            "FUSE": "Fusion-Simulator/FUSE",
            "OpenFUSIONToolkit": "OpenFusionProject/OpenFUSIONToolkit",
            "ITER-IMAS": "iterorganization/imas",
            "fusion-sim": "JeryRFong/fusion-sim",
        }
        new_gh = repo_map.get(new_name, "")

        update_fields({
            "项目名称": new_name,
            "GitHub仓库": new_gh,
            "开发轮次": "待开始",
            "本轮开始时间": "—",
            "本轮目标": "—",
            "贡献状态": "📋 BACKLOG",
            "PR 信息": "—",
            "备注": f"由 project_rotator 从队列 #{nxt['顺序']} 选出",
        }, prog, section="当前开发项目")
        result["applied"] = True
    return result


def main(argv: List[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--no-skip-current", dest="no_skip_current", action="store_true",
                    help="不跳过当前项目（默认会跳过）")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv[1:])

    skip_current = not args.no_skip_current
    result = rotate(apply=args.apply, skip_current=skip_current)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"当前项目: {result['current']}")
        if result["selected"]:
            print(f"下一项目（队列 #{result['selected_order']}）: {result['selected']}")
            if result["applied"]:
                print("已写入进度表。")
            else:
                print("用 --apply 写入。")
        else:
            print("无可选项目（队列空或所有项目都进行中）")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
