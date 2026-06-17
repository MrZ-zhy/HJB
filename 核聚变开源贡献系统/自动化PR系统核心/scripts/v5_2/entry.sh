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

# ── Step 0c: HJB 仓库就位（自动定位 git 仓库根，不再假设 HJB/HJB 套娃） ──
# 第一性原理：HJB 仓库根 = 包含 .git/ 的目录。
#   - 沙盒：/workspace/ 本身就是 HJB repo（.git 在 /workspace/.git）
#   - 旧版假设：HJB_ROOT=/workspace/HJB，然后 cd HJB/HJB/…（错）
# 修法：HJB_ROOT 默认起点 = /workspace 或 caller 传入的目录，
#       上溯最多 10 层找 .git，找到即 cd 进去。
_find_hjb_repo_root() {
  local d="${1:-/workspace}"
  for _ in 1 2 3 4 5 6 7 8 9 10; do
    if [ -d "$d/.git" ]; then echo "$d"; return 0; fi
    local parent
    parent="$(dirname "$d")"
    if [ "$parent" = "$d" ]; then return 1; fi
    d="$parent"
  done
  return 1
}
HJB_ROOT="$(_find_hjb_repo_root "${HJB_ROOT:-/workspace}" 2>/dev/null || true)"
if [ -z "$HJB_ROOT" ] || [ ! -d "$HJB_ROOT/.git" ]; then
  echo "[entry] FATAL: 在起点 ${HJB_ROOT:-/workspace} 上溯 10 层仍未找到 .git 目录"
  echo "[entry]   请确认 HJB git 仓库是否已 clone，或显式 export HJB_ROOT=<repo 根>"
  exit 10
fi
export HJB_ROOT
cd "$HJB_ROOT" || { echo "[entry] FATAL: cd $HJB_ROOT failed"; exit 10; }
# V5.2 first-principles 修复：git 同步输出（fetch/checkout/pull/reset）原本污染 stdout，
# 导致下游 `python3 engine.py tick` 的 JSON TickReport 被这些 "Already on 'main'." / "From .../FETCH_HEAD"
# 之类文字前缀污染无法直接被 json.load 解析。改为 >&2 把进度信息丢到 stderr，
# stdout 干净保留给 engine.py 的 JSON 输出。
git remote set-url origin "https://x-access-token:${GITHUB_TOKEN}@github.com/MrZ-zhy/HJB.git"
git fetch origin >&2
git checkout main >&2
# `2>&1 | tail -3 >&2`：把 pull 的 stderr 合并到 pipe，tail -3 的结果再重定向到 stderr，
# 这样进度信息 ("Already up to date." 等) 全走 stderr，stdout 干净给 engine.py 输出 JSON。
git pull --rebase origin main 2>&1 | tail -3 >&2 || {
  echo "[entry] WARN: pull --rebase failed, reset --hard" >&2
  git reset --hard origin/main >&2
}

# .env.local：fallback 兜底，.gitignore 屏蔽
echo "GITHUB_TOKEN=${GITHUB_TOKEN}" > .env.local

echo "[entry] env ok, HEAD=$(git rev-parse --short HEAD), token=***${GITHUB_TOKEN: -4}" >&2

# ── Step 1-7: V5.2 统一入口 ──
CMD="${1:-tick}"
shift || true
python3 "核聚变开源贡献系统/自动化PR系统核心/scripts/v5_2/engine.py" "$CMD" "$@"
