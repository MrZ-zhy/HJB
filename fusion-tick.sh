#!/usr/bin/env bash
# fusion-tick: 凭据注入器 + V4 一键 tick
# 用法：
#   fusion-tick                # 正常 tick
#   fusion-tick --dry-run      # 调试，不 commit/push
#   fusion-tick status         # 看状态
#   fusion-tick validate       # V4 自检
#   fusion-tick report         # metrics snapshot
#   fusion-tick project <name> # 子表
#
# 凭据来源（按优先级）：
#   1) 已 export 的 GITHUB_TOKEN
#   2) /workspace/HJB/HJB/.env.local（已被 .gitignore，不会入库）

set -euo pipefail

HJB_ROOT="/workspace/HJB/HJB"
ENV_FILE="${HJB_ROOT}/.env.local"

# ── 凭据注入 ──
if [ -z "${GITHUB_TOKEN:-}" ] && [ -f "$ENV_FILE" ]; then
  set -a
  # shellcheck disable=SC1090
  source "$ENV_FILE"
  set +a
fi

if [ -z "${GITHUB_TOKEN:-}" ]; then
  echo "[fusion-tick] FATAL: GITHUB_TOKEN missing (no env, no $ENV_FILE)" >&2
  exit 11
fi

# ── git 凭据 rewrite（让 https://github.com/... 走 token 认证）──
git config --global url."https://x-access-token:${GITHUB_TOKEN}@github.com/".insteadOf "https://github.com/" >/dev/null

# ── 仓内 git remote 同步一次 ──
cd "$HJB_ROOT"
git remote set-url origin "https://x-access-token:${GITHUB_TOKEN}@github.com/MrZ-zhy/HJB.git" >/dev/null

# ── 派发到 V4 engine ──
CMD="${1:-tick}"
shift || true

case "$CMD" in
  tick|status|validate|report|project)
    python3 "${HJB_ROOT}/核聚变开源贡献系统/自动化PR系统核心/scripts/v4/engine.py" "$CMD" "$@"
    ;;
  --dry-run)
    python3 "${HJB_ROOT}/核聚变开源贡献系统/自动化PR系统核心/scripts/v4/engine.py" tick --dry-run
    ;;
  -h|--help|help)
    sed -n '2,20p' "$0"
    ;;
  *)
    echo "[fusion-tick] unknown cmd: $CMD" >&2
    exit 2
    ;;
esac
