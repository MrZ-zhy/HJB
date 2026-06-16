"""V4 health check strategy。

v2 preflight.py 升级版。结构化 StepResult 替代裸 JSON。
"""
from __future__ import annotations

import os
import subprocess
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..core.models import Action, EngineState
from ..persistence.progress_table import ProgressTableRepo

REPO_ROOT = Path(os.environ.get("HJB_ROOT", "/workspace/HJB/HJB"))
BRANCH = os.environ.get("HJB_BRANCH", "trae/solo-agent-TbCBsF")
GH_API = "https://api.github.com"


@dataclass
class HealthResult:
    ok: bool
    payload: Dict[str, Any] = field(default_factory=dict)
    error: str = ""


def _http_get(url: str, token: str) -> tuple[int, Any]:
    req = urllib.request.Request(url, headers={
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    })
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            body = r.read().decode()
            try:
                return r.status, _json_loads(body)
            except Exception:
                return r.status, body
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode(errors="replace")
    except Exception as e:
        return 0, str(e)


def _json_loads(s: str) -> Any:
    import json
    return json.loads(s)


def _git(args: List[str]) -> subprocess.CompletedProcess:
    return subprocess.run(["git"] + args, cwd=REPO_ROOT, capture_output=True, text=True)


class HealthCheckStrategy:
    name = "health_check"

    def __init__(self, prog_data: Optional[Dict[str, Dict[str, str]]] = None,
                 prog_repo: Optional[ProgressTableRepo] = None) -> None:
        self.prog_data = prog_data
        self.prog_repo = prog_repo

    def evaluate(self, state: EngineState = None) -> List[Action]:
        """Strategy 协议要求返回 List[Action]；health_check 不产生 action。
        实际健康检查由 orchestrator 在 step 2 通过 self.evaluate_health() 调。
        """
        return []

    def evaluate_health(self) -> HealthResult:
        return self._do_evaluate()

    def _do_evaluate(self) -> HealthResult:
        blockers: List[str] = []
        payload: Dict[str, Any] = {"checks": {}}
        tok = os.environ.get("GITHUB_TOKEN", "")

        # 1. Token
        if not tok:
            blockers.append("token: missing GITHUB_TOKEN")
            payload["checks"]["token"] = {"ok": False, "reason": "missing GITHUB_TOKEN"}
        else:
            status, body = _http_get(f"{GH_API}/user", tok)
            if status != 200:
                blockers.append(f"token: http {status}")
                payload["checks"]["token"] = {"ok": False, "reason": f"http {status}"}
            else:
                # scopes
                try:
                    out = subprocess.check_output(
                        ["curl", "-sS", "-D", "-", "-o", "/dev/null",
                         "-H", f"Authorization: token {tok}", f"{GH_API}/user"],
                        timeout=10,
                    ).decode()
                    scopes = ""
                    for line in out.splitlines():
                        if line.lower().startswith("x-oauth-scopes:"):
                            scopes = line.split(":", 1)[1].strip()
                            break
                    have = set(s.strip() for s in scopes.split(",") if s.strip())
                    if "repo" in have:
                        have.add("public_repo")
                    missing = [s for s in ["repo"] if s not in have]
                    payload["checks"]["token"] = {
                        "ok": len(missing) == 0,
                        "scopes": list(have),
                        "missing": missing,
                    }
                    if missing:
                        blockers.append(f"token: missing scopes {missing}")
                except Exception as e:
                    payload["checks"]["token"] = {"ok": False, "reason": f"scope probe failed: {e}"}
                    blockers.append(f"token: scope probe failed: {e}")

        # 2. upstream（从主表的活跃项目取第一个）
        if self.prog_data is None and self.prog_repo is not None:
            self.prog_data = self.prog_repo.parse()
        first_repo = ""
        if self.prog_data:
            for sec, kv in self.prog_data.items():
                gh = kv.get("GitHub仓库", "")
                if "/" in gh:
                    first_repo = gh
                    break
        if first_repo and tok:
            owner, repo = first_repo.split("/", 1)
            status, body = _http_get(f"{GH_API}/repos/{owner}/{repo}", tok)
            if status != 200:
                blockers.append(f"upstream: http {status}")
                payload["checks"]["upstream"] = {"ok": False, "reason": f"http {status}"}
            else:
                payload["checks"]["upstream"] = {
                    "ok": True,
                    "full_name": body.get("full_name") if isinstance(body, dict) else None,
                    "open_issues": body.get("open_issues_count", 0) if isinstance(body, dict) else 0,
                }
                # 3. fork
                status, body = _http_get(f"{GH_API}/repos/MrZ-zhy/{repo}", tok)
                if status == 200 and isinstance(body, dict):
                    payload["checks"]["fork"] = {"ok": True, "exists": True}
                else:
                    blockers.append(f"fork: {status}")
                    payload["checks"]["fork"] = {"ok": False, "exists": False}

        # 4. local repo
        if not (REPO_ROOT / ".git").exists():
            blockers.append("local_repo: not a git repo")
            payload["checks"]["local_repo"] = {"ok": False, "reason": "not a git repo"}
        else:
            clean = _git(["status", "--porcelain"]).stdout.strip() == ""
            head = _git(["rev-parse", "--short", "HEAD"]).stdout.strip()
            payload["checks"]["local_repo"] = {
                "ok": True, "head": head, "clean": clean
            }
            if not clean:
                payload["checks"]["local_repo"]["warning"] = "uncommitted changes"

        # 5. progress table health
        if self.prog_data:
            cb = self.prog_data.get("_codeblock", {})
            lock = cb.get("LOCK", "false").strip() == "true"
            wip = cb.get("WIP_STATUS", "normal").strip()
            budget = cb.get("ERROR_BUDGET_STATUS", "normal").strip()
            last_status = cb.get("LAST_HEARTBEAT_STATUS", "unknown").strip()
            ok = not lock and wip == "normal" and budget == "normal" and last_status == "ok"
            payload["checks"]["progress_health"] = {
                "ok": ok, "lock": lock, "wip": wip, "budget": budget, "last": last_status
            }
            if not ok:
                blockers.append(f"progress_health: lock={lock} wip={wip} budget={budget} last={last_status}")

        return HealthResult(
            ok=len(blockers) == 0,
            payload={**payload, "blockers": blockers},
        )
