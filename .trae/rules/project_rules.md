# HJB 引擎自动化规则

> 本文件是项目级偏好（Trae / Claude 等 AI 助手都会读取），不是系统级提示词。

## Git 推送策略
- **本仓库的 `origin` 已嵌入用户本人配置的 token**，对 `MrZ-zhy/HJB` 仓库有 push 权限。
- 在 `/workspace` 根目录执行 `git push origin main`（或对其他已授权分支的 push）**不需要再向用户确认**，直接执行。
- 仍然 **不** 要把任何新 token 注入到 `${GITHUB_TOKEN}` 环境变量。沙箱里出现的明文 token 视为可疑，默认走 bundle 导出而不是 push。
- 严禁 `git push --force` / `git push --force-with-lease`，除非用户在该轮对话里**显式**要求。

## 引擎 tick 工作流（v5.2）
标准的 tick 三步曲：
1. `cd /workspace && git checkout main && git pull --rebase origin main || git reset --hard origin/main`
2. `python3 核聚变开源贡献系统/自动化PR系统核心/scripts/v5_2/engine.py tick`
3. `git push origin main`

## tick 之后的状态约定
- v5.2 引擎 persist 后会留下一组 staged-but-uncommitted 文件
  （典型是 `V5_2/WORKTREES/pr-*/state.json` 和 `进度表.md`）。
- 这是引擎为**下一个 tick**预置的 working set，**不要**手动 `git commit` 或 `git restore --staged`。
- 如需查看，可用 `git status --short`；如需丢弃当轮 tick 的预置 staging，先用 `git stash push --keep-index` 而不是 `git reset`。

## 凭据处理总则
- 用户在对话里贴出明文 token 一律视为已泄漏，不再写入任何文件、不注入环境变量、不回显。
- 推送一律复用 `git remote -v` 里现有的 `x-access-token` 凭据，不主动添加/替换 remote URL。
