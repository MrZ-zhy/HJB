"""V4 git 原子操作。

封装 commit + push，避免 v2 散落的 _run + 错误处理不一致。
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import List, Optional

REPO_ROOT = Path(os.environ.get("HJB_ROOT", "/workspace/HJB/HJB"))
BRANCH = os.environ.get("HJB_BRANCH", "trae/solo-agent-TbCBsF")


def _run(args: List[str], cwd: Path = REPO_ROOT) -> subprocess.CompletedProcess:
    return subprocess.run(args, cwd=cwd, capture_output=True, text=True)


def current_sha(short: bool = True) -> str:
    fmt = "--short" if short else ""
    return _run(["git", "rev-parse", fmt, "HEAD"]).stdout.strip()


def porcelain_status() -> str:
    return _run(["git", "status", "--porcelain"]).stdout.strip()


def add_all() -> subprocess.CompletedProcess:
    return _run(["git", "add", "-A"])


def commit(msg: str) -> subprocess.CompletedProcess:
    return _run(["git", "commit", "-m", msg])


def push(remote: str = "origin", branch: str = BRANCH) -> subprocess.CompletedProcess:
    return _run(["git", "push", remote, branch])


def pull_rebase(remote: str = "origin", branch: str = BRANCH) -> subprocess.CompletedProcess:
    return _run(["git", "pull", "--rebase", remote, branch])


def commit_and_push(msg: str) -> str:
    """原子 commit + push。返回新 SHA 或失败信息。

    公理 A1：一次 tick = 一次原子 commit。
    """
    add_all()
    res = commit(msg)
    if res.returncode != 0 and "nothing to commit" not in (res.stdout + res.stderr):
        return f"commit_failed: {res.stderr.strip()}"
    push_res = push()
    if push_res.returncode != 0:
        return f"push_failed: {push_res.stderr.strip()}"
    return current_sha()
