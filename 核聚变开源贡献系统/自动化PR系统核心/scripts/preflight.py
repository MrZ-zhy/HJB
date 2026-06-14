#!/usr/bin/env python3
"""
preflight.py — 定时任务启动前的状态预检

【治本目标】
避免运行时才发现 token 缺 scope、fork 不存在、upstream 漂移等问题。

【检查项】
1. GITHUB_TOKEN 可用 + 持有必需 scopes
2. 目标项目 upstream 可达
3. fork 已存在（否则标记需要人工建 fork）
4. 本地工作分支干净
5. HEAD 已与 origin 同步
6. WIP 各项是否在限制内
7. 错误预算是否未耗尽
8. 上次心跳状态是否健康

【使用方式】
  python3 preflight.py                       # 全量检查 → JSON 报告
  python3 preflight.py --gate               # 仅返回 0/1（CI gate 用）
  python3 preflight.py --required-scope repo  # 自定义必需 scope
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List

REPO_ROOT = Path(os.environ.get("HJB_ROOT", "/workspace/HJB"))
PROG_PATH = REPO_ROOT / "核聚变开源贡献系统" / "进度表.md"
BRANCH = os.environ.get("HJB_BRANCH", "trae/solo-agent-TbCBsF")
GH_API = "https://api.github.com"


# ─────────────────────────────────────────────────────────────────────
# 工具
# ─────────────────────────────────────────────────────────────────────

def _need_gh_token() -> str:
    tok = os.environ.get("GITHUB_TOKEN", "")
    if not tok:
        print("error: GITHUB_TOKEN not in env", file=sys.stderr)
        sys.exit(2)
    return tok


def _http_get(url: str, token: str) -> tuple[int, dict | str]:
    import urllib.request, urllib.error
    req = urllib.request.Request(url, headers={
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    })
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            body = r.read().decode()
            try:
                return r.status, json.loads(body)
            except json.JSONDecodeError:
                return r.status, body
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode(errors="replace")
    except Exception as e:
        return 0, str(e)


def _git(args: list[str], cwd: Path = REPO_ROOT) -> subprocess.CompletedProcess:
    return subprocess.run(["git"] + args, cwd=cwd, capture_output=True, text=True)


# ─────────────────────────────────────────────────────────────────────
# 检查项
# ─────────────────────────────────────────────────────────────────────

def check_token(required_scopes: List[str]) -> Dict[str, Any]:
    tok = _need_gh_token()
    status, body = _http_get(f"{GH_API}/user", tok)
    if status != 200:
        return {"ok": False, "reason": f"http {status}", "body": str(body)[:200]}

    # scopes 在 header 中（urllib 拿不到 header，需要用 http.client 或 curl）
    # 这里用 subprocess 调用 curl 拿 header
    try:
        out = subprocess.check_output(
            ["curl", "-sS", "-D", "-", "-o", "/dev/null",
             "-H", f"Authorization: token {tok}",
             f"{GH_API}/user"],
            timeout=10,
        ).decode()
        scopes = ""
        for line in out.splitlines():
            if line.lower().startswith("x-oauth-scopes:"):
                scopes = line.split(":", 1)[1].strip()
                break
    except Exception as e:
        scopes = f"(probe failed: {e})"

    scope_list = [s.strip() for s in scopes.split(",") if s.strip()]
    # 'repo' scope 涵盖 'public_repo'
    if "repo" in scope_list:
        have = set(scope_list) | {"public_repo"}
    else:
        have = set(scope_list)
    missing = [s for s in required_scopes if s not in have]

    return {
        "ok": len(missing) == 0,
        "login": body.get("login") if isinstance(body, dict) else None,
        "scopes": scope_list,
        "required": required_scopes,
        "missing": missing,
    }


def check_upstream(owner: str, repo: str, token: str) -> Dict[str, Any]:
    status, body = _http_get(f"{GH_API}/repos/{owner}/{repo}", token)
    if status != 200:
        return {"ok": False, "reason": f"http {status}", "body": str(body)[:200]}
    return {
        "ok": True,
        "full_name": body.get("full_name"),
        "default_branch": body.get("default_branch"),
        "archived": body.get("archived", False),
        "open_issues": body.get("open_issues_count", 0),
        "stars": body.get("stargazers_count", 0),
    }


def check_fork(upstream_owner: str, repo: str, fork_owner: str, token: str) -> Dict[str, Any]:
    status, body = _http_get(f"{GH_API}/repos/{fork_owner}/{repo}", token)
    if status == 200 and isinstance(body, dict):
        return {
            "ok": True,
            "exists": True,
            "full_name": body.get("full_name"),
            "default_branch": body.get("default_branch"),
            "fork_of": body.get("parent", {}).get("full_name") if body.get("parent") else None,
        }
    if status == 404:
        return {"ok": False, "exists": False, "reason": "fork not found"}
    return {"ok": False, "exists": False, "reason": f"http {status}", "body": str(body)[:200]}


def check_local_repo() -> Dict[str, Any]:
    if not (REPO_ROOT / ".git").exists():
        return {"ok": False, "reason": "HJB not a git repo"}
    head = _git(["rev-parse", "--short", "HEAD"]).stdout.strip()
    branch = _git(["rev-parse", "--abbrev-ref", "HEAD"]).stdout.strip()
    clean = _git(["status", "--porcelain"]).stdout.strip() == ""
    ahead_behind = _git(["rev-list", "--left-right", "--count", f"HEAD...origin/{BRANCH}"]).stdout.strip()
    return {
        "ok": True,
        "head": head,
        "branch": branch,
        "clean": clean,
        "ahead_behind": ahead_behind,
    }


def check_progress_table() -> Dict[str, Any]:
    """读进度表关键字段，提取 WIP/预算/锁 等健康指标。"""
    sys.path.insert(0, str(Path(__file__).parent))
    from engine_helper import parse_progress
    if not PROG_PATH.exists():
        return {"ok": False, "reason": "progress table missing"}
    data = parse_progress(PROG_PATH)
    # 找关键字段
    fields = {}
    for sec, kv in data.items():
        for k, v in kv.items():
            fields[k] = v
    lock = fields.get("LOCK", "false").strip() == "true"
    wip = fields.get("WIP_STATUS", "normal").strip()
    budget = fields.get("ERROR_BUDGET_STATUS", "normal").strip()
    last_status = fields.get("LAST_HEARTBEAT_STATUS", "unknown").strip()
    return {
        "ok": not lock and wip == "normal" and budget == "normal" and last_status == "ok",
        "lock": lock,
        "wip": wip,
        "error_budget": budget,
        "last_heartbeat": last_status,
        "next_action": fields.get("NEXT_ACTION"),
        "current_node": fields.get("CURRENT_CHAIN_NODE"),
    }


# ─────────────────────────────────────────────────────────────────────
# 主入口
# ─────────────────────────────────────────────────────────────────────

def run_preflight(required_scopes: List[str] = None) -> Dict[str, Any]:
    if required_scopes is None:
        required_scopes = ["repo"]
    tok = os.environ.get("GITHUB_TOKEN", "")

    report: Dict[str, Any] = {"timestamp": __import__("datetime").datetime.utcnow().isoformat() + "Z"}

    # 1. Token
    if tok:
        report["token"] = check_token(required_scopes)
    else:
        report["token"] = {"ok": False, "reason": "GITHUB_TOKEN env not set"}

    # 2. 当前项目（如果进度表里有 GitHub仓库 字段）
    if PROG_PATH.exists():
        try:
            data = parse_progress_via_helper()
            for sec, kv in data.items():
                gh = kv.get("GitHub仓库")
                if gh and "/" in gh:
                    owner, repo = gh.split("/", 1)
                    report["upstream"] = check_upstream(owner, repo, tok) if tok else {"ok": False, "reason": "no token"}
                    if report["upstream"].get("ok"):
                        report["fork"] = check_fork(owner, repo, "MrZ-zhy", tok) if tok else {"ok": False, "reason": "no token"}
                    break
        except Exception as e:
            report["upstream"] = {"ok": False, "reason": f"parse error: {e}"}

    # 3. 本地
    report["local_repo"] = check_local_repo()

    # 4. 进度表健康
    report["progress_health"] = check_progress_table()

    # 汇总
    blockers = []
    for k in ("token", "upstream", "fork", "local_repo", "progress_health"):
        v = report.get(k, {})
        if isinstance(v, dict) and not v.get("ok", True):
            blockers.append(f"{k}: {v.get('reason', 'unknown')}")
    report["overall_ok"] = len(blockers) == 0
    report["blockers"] = blockers

    return report


def parse_progress_via_helper() -> Dict[str, Dict[str, str]]:
    sys.path.insert(0, str(Path(__file__).parent))
    from engine_helper import parse_progress
    return parse_progress(PROG_PATH)


def main(argv: list[str]) -> int:
    args = list(argv[1:])
    gate_only = "--gate" in args
    args = [a for a in args if a != "--gate"]
    required = ["repo"]
    for i, a in enumerate(args):
        if a == "--required-scope" and i + 1 < len(args):
            required = args[i + 1].split(",")

    report = run_preflight(required)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if gate_only:
        return 0 if report["overall_ok"] else 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
