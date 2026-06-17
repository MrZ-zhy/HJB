#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────
# V5.2 持久化入口 shell
#
# 第一性原理：凭据来源 = prompt 顶部（一次性）+ ~/.config/fusion/credentials（持久化）
#   1. 任何 tick 开始前必须 `source ~/.config/fusion/credentials`
#   2. 然后再 `export GITHUB_TOKEN` 才能让 git config / origin URL 拿到真 token
#   3. 否则会出现 "Invalid username or token" 的 push 失败
#
# 用法：
#   bash 核聚变开源贡献系统/自动化PR系统核心/scripts/v5_2/entry.sh
#   bash 核聚变开源贡献系统/自动化PR系统核心/scripts/v5_2/entry.sh tick --density deep
#   bash 核聚变开源贡献系统/自动化PR系统核心/scripts/v5_2/entry.sh worktrees
# ─────────────────────────────────────────────────────────────────────────

set -e

# ── Step 0a: 凭据加载（修复 push auth 失败的第一性根因）──
CRED_FILE="${HOME}/.config/fusion/credentials"
if [ ! -f "$CRED_FILE" ]; then
  echo "[entry] FATAL: $CRED_FILE 不存在；请把 token 写入该文件（chmod 600）"
  echo "[entry]   内容示例："
  echo '[entry]   export GITHUB_TOKEN="ghp_..."'
  exit 11
fi
# shellcheck disable=SC1090
source "$CRED_FILE"
if [ -z "${GITHUB_TOKEN:-}" ]; then
  echo "[entry] FATAL: $CRED_FILE 存在但 GITHUB_TOKEN 为空"
  exit 12
fi
export GITHUB_TOKEN

# ── Step 0b: git 全局配置（每次 tick 都重设，覆盖之前空 token 的污染）──
git config --global user.email "engine@fusion-contrib.local"
git config --global user.name  "Fusion-Contrib Engine v5.2"
git config --global url."https://x-access-token:${GITHUB_TOKEN}@github.com/".insteadOf "https://github.com/"

# ── Step 0c: HJB 仓库就位 ──
export HJB_ROOT="${HJB_ROOT:-/workspace/HJB}"
mkdir -p "$HJB_ROOT"
cd "$HJB_ROOT" || { echo "[entry] FATAL: HJB_ROOT unavailable"; exit 10; }
if [ ! -d "HJB/.git" ]; then
  rm -rf HJB
  git clone "https://x-access-token:${GITHUB_TOKEN}@github.com/MrZ-zhy/HJB.git" HJB 2>&1 | tail -3
fi
cd HJB
git remote set-url origin "https://x-access-token:${GITHUB_TOKEN}@github.com/MrZ-zhy/HJB.git"
git fetch origin
git checkout main
git pull --rebase origin main 2>&1 | tail -3 || {
  echo "[entry] WARN: pull --rebase failed, reset --hard"
  git reset --hard origin/main
}

# .env.local：fallback 兜底，.gitignore 屏蔽
echo "GITHUB_TOKEN=${GITHUB_TOKEN}" > .env.local

echo "[entry] env ok, HEAD=$(git rev-parse --short HEAD), token=***${GITHUB_TOKEN: -4}"

# ── Step 1-7: V5.2 统一入口 ──
CMD="${1:-tick}"
shift || true
python3 "核聚变开源贡献系统/自动化PR系统核心/scripts/v5_2/engine.py" "$CMD" "$@"
