"""V4 共享 GitHub API + 本地 git 辅助。

被 code_strategy / monitor_strategy / stalled_strategy 共用。
失败一律 raise，由调用方决定如何处理（log / emit / 降级）。
"""
from __future__ import annotations

import json
import os
import subprocess
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


# ─────────────────────────────────────────────────────────────────────
# 常量
# ─────────────────────────────────────────────────────────────────────
GH_API = "https://api.github.com"
FORK_OWNER = os.environ.get("HJB_FORK_OWNER", "MrZ-zhy")  # 本机 fork owner
# 本机项目工作副本根（沙盒临时，C2 唯一持久化 = GitHub）
REPOS_ROOT = Path(os.environ.get("HJB_REPOS_ROOT", "/workspace/HJB/HJB/项目"))


# ─────────────────────────────────────────────────────────────────────
# HTTP 辅助
# ─────────────────────────────────────────────────────────────────────
def _http(method: str, url: str, token: str, body: Optional[dict] = None,
          timeout: int = 30) -> Tuple[int, Any]:
    """统一的 GitHub API 调用。返回 (status_code, body)。"""
    data = None
    headers = {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "fusion-contrib-engine",
    }
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read().decode()
            try:
                return r.status, json.loads(raw)
            except Exception:
                return r.status, raw
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode(errors="replace")
    except Exception as e:
        return 0, str(e)


# ─────────────────────────────────────────────────────────────────────
# GitHub API 高层封装
# ─────────────────────────────────────────────────────────────────────
def get_repo(owner: str, name: str, token: str) -> Tuple[int, Any]:
    return _http("GET", f"{GH_API}/repos/{owner}/{name}", token)


def ensure_fork(upstream_owner: str, repo_name: str, fork_owner: str,
                token: str) -> Tuple[bool, str, str]:
    """幂等 fork：已存在则返回 (True, fork_full_name, "")。
    不存在则创建，返回 (True, fork_full_name, "") 或 (False, "", reason)。
    """
    # 1. 检查是否已 fork
    status, body = get_repo(fork_owner, repo_name, token)
    if status == 200 and isinstance(body, dict) and body.get("fork") is True:
        return True, f"{fork_owner}/{repo_name}", "exists"
    # 2. 创建 fork
    status, body = _http("POST", f"{GH_API}/repos/{upstream_owner}/{repo_name}/forks", token, body={})
    if status in (202, 201):
        full = body.get("full_name", f"{fork_owner}/{repo_name}") if isinstance(body, dict) else f"{fork_owner}/{repo_name}"
        return True, full, "created"
    if status == 422 and "already exists" in str(body).lower():
        return True, f"{fork_owner}/{repo_name}", "already_exists_422"
    return False, "", f"http {status}: {str(body)[:200]}"


def list_good_first_issues(owner: str, name: str, token: str,
                           per_page: int = 10) -> Tuple[bool, List[Dict[str, Any]], str]:
    """列出 open 的 good first issue。返回 (ok, issues, reason)。"""
    qs = "state=open&labels=good%20first%20issue&per_page=" + str(per_page)
    status, body = _http("GET", f"{GH_API}/repos/{owner}/{name}/issues?{qs}", token)
    if status != 200:
        return False, [], f"http {status}: {str(body)[:200]}"
    if not isinstance(body, list):
        return False, [], f"unexpected body type: {type(body).__name__}"
    # 过滤掉 PR（issue API 也会返回 PR）
    issues = [
        {
            "number": it.get("number"),
            "title": it.get("title"),
            "url": it.get("html_url"),
            "updated_at": it.get("updated_at"),
            "comments": it.get("comments", 0),
        }
        for it in body
        if isinstance(it, dict) and "pull_request" not in it
    ]
    return True, issues, ""


def list_recent_issues(owner: str, name: str, token: str,
                       per_page: int = 10) -> Tuple[bool, List[Dict[str, Any]], str]:
    """列出最近的 open issues（无 label 过滤，用于 P1.1 概览）。"""
    qs = "state=open&per_page=" + str(per_page)
    status, body = _http("GET", f"{GH_API}/repos/{owner}/{name}/issues?{qs}", token)
    if status != 200:
        return False, [], f"http {status}: {str(body)[:200]}"
    if not isinstance(body, list):
        return False, [], f"unexpected body type: {type(body).__name__}"
    issues = [
        {
            "number": it.get("number"),
            "title": it.get("title"),
            "url": it.get("html_url"),
            "labels": [l.get("name") for l in (it.get("labels") or []) if isinstance(l, dict)],
            "comments": it.get("comments", 0),
        }
        for it in body
        if isinstance(it, dict) and "pull_request" not in it
    ]
    return True, issues, ""


def get_readme_meta(owner: str, name: str, token: str) -> Tuple[bool, Dict[str, Any], str]:
    """获取 README 元信息（大小、下载 URL）。"""
    status, body = _http("GET", f"{GH_API}/repos/{owner}/{name}/readme", token)
    if status != 200:
        return False, {}, f"http {status}: {str(body)[:200]}"
    if not isinstance(body, dict):
        return False, {}, "unexpected body"
    return True, {
        "name": body.get("name"),
        "size": body.get("size", 0),
        "path": body.get("path"),
        "download_url": body.get("download_url"),
        "encoding": body.get("encoding"),
    }, ""


# ─────────────────────────────────────────────────────────────────────
# 本地 git 辅助
# ─────────────────────────────────────────────────────────────────────
def _run_git(args: List[str], cwd: Optional[Path] = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git"] + args,
        cwd=str(cwd) if cwd else None,
        capture_output=True,
        text=True,
        timeout=120,
    )


def ensure_clone(fork_full_name: str, project_name: str,
                 branch: str = "") -> Tuple[bool, str, str]:
    """幂等克隆 fork 到 REPOS_ROOT/<project_name>/。

    返回 (ok, local_path, reason)。
    branch 非空时克隆后立即 checkout（默认 = upstream default branch）。
    """
    target = REPOS_ROOT / project_name
    if (target / ".git").is_dir():
        return True, str(target), "already_cloned"
    target.parent.mkdir(parents=True, exist_ok=True)
    fork_url = f"https://github.com/{fork_full_name}.git"
    res = _run_git(["clone", "--depth=20", fork_url, str(target)])
    if res.returncode != 0:
        return False, "", f"clone_failed: {res.stderr.strip()[:200]}"
    if branch:
        res2 = _run_git(["checkout", branch], cwd=target)
        if res2.returncode != 0:
            return True, str(target), f"cloned_but_branch_failed: {res2.stderr.strip()[:200]}"
    return True, str(target), "cloned"


def current_head(local_path: str) -> str:
    res = _run_git(["rev-parse", "--short", "HEAD"], cwd=Path(local_path))
    return res.stdout.strip() or "—"


def get_upstream_default_branch(owner: str, name: str, token: str) -> str:
    """获取 upstream 默认分支名。失败返回 'main'。"""
    status, body = get_repo(owner, name, token)
    if status == 200 and isinstance(body, dict):
        return body.get("default_branch", "main") or "main"
    return "main"
