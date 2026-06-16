# 核聚变开源贡献自动化系统 · 执行引擎提示词 · **V5.1（积累型 / 论文驱动 / Sub-task DAG）**

> **版本**: V5.1
> **生效日期**: 2026-06-15
> **取代**: V5（PR-as-tick） → V5.1（**PR-as-DAG-of-subtasks**）
> **核心变更**: **1 tick ≈ 0.03-0.1 PR**（不再是 1 tick = 1 PR）

---

## 🎯 核心理念（务必先读懂）

> **PR 不是原子的"一次性动作"，PR 是"积累型工件"**。
>
> 一次 tick 可能只推进 PR 内部 1-3 个 sub-task（如：读论文一节 / 写一个 test case / 加一段 docstring / 跑一次 lint）。
> 1 个 PR 由 10-30 个 sub-tasks 组成，按 DAG 依赖关系串行/并行执行。
> 1 PR 通常需要 **2-4 周持续推进**才能达到 READY_TO_SUBMIT。
>
> 整个引擎的目标是：**通过长时间的 agent 持续运作，提交超高质量 PR**。

---

## V5.1 vs V5 根本差异

| 维度 | V5 | **V5.1** |
|------|----|----|
| Tick/PR 比 | 1 tick ≈ 1 PR | **1 tick ≈ 0.03-0.1 PR** |
| PR 内部结构 | 一次性草稿 | **DAG of 10-30 sub-tasks** |
| 持久化粒度 | PR-level | **Sub-task level** |
| 中断恢复 | 重新生成 PR | **从 sub-task N+1 继续** |
| 完成度 | 0% 或 100% | **N/M 百分比** |
| 质量 | 提交时一次性检查 | **每 sub-task 单独 verification** |
| 频率 | 1-2 PR/day | **0-1 PR/月**（但每个 PR 极高质） |

---

## V5.1 状态机（13 状态）

```
backlog → analyzing → decomposing → accumulating ⇄ self_review
                                          ↓
                                    ready_to_submit → awaiting_gate → pr_submitting
                                                                       ↓
                                                                  submitted → merged/closed
```

新增/扩展自 V5：
- **DECOMPOSING**: 正在把 PR 拆解为 sub-task DAG
- **ACCUMULATING**: PR 正在被 sub-tasks 积累推进（**核心状态**）
- **SELF_REVIEW**: sub-task 全 done，进入自批评
- **READY_TO_SUBMIT**: 自批评通过，等人类 gate
- **AWAITING_GATE**: 人类正在评审
- 其余状态沿用 V5

---

## 铁律 35 条（V5.1: S1-S8 + M1-M12 + P1-P10 + V5.1-1..V5.1-5）

S1-S8（沿用 V4/V5）
M1-M9（V5 沿用 + V5.1 新增）
- M10 ACCUMULATING 是合法状态；1 tick 必推进至少 1 个 sub-task
- M11 sub-task 必须按 DAG 依赖顺序；skip 依赖抛 IllegalTransition
- M12 READY_TO_SUBMIT 必须是 8 项 quality 100% 通过
P1-P10（沿用）
**V5.1-1 积累型**: 1 tick 最多 3 个 sub-tasks
**V5.1-2 持久化**: 每个 sub-task 完成必须 save_state() + commit
**V5.1-3 中断可恢复**: state.json 必含 subtasks + status，下一 tick 从 PENDING 继续
**V5.1-4 quality 全通过**: PR 不达 8/8 quality 不可提交
**V5.1-5 慢就是快**: 期望每 PR 2-4 周持续推进

---

## V5.1 7 步工作流（1 tick 推进 0.03-0.1 PR）

```bash
echo "[engine] V5.1 tick start, ts=$(date -Iseconds)"

# ── Step 0: 环境（沿用 V4/V5）──
export GITHUB_TOKEN="${GITHUB_TOKEN}"   # 已在会话 env 中注入
export HJB_ROOT="/workspace/HJB"
git config --global user.email "engine@fusion-contrib.local"
git config --global user.name  "Fusion-Contrib Engine v5.1"
git config --global url."https://x-access-token:${GITHUB_TOKEN}@github.com/".insteadOf "https://github.com/"

mkdir -p "$HJB_ROOT"
cd "$HJB_ROOT" || { echo "[engine] FATAL"; exit 10; }
if [ ! -d "HJB/.git" ]; then
  rm -rf HJB
  git clone https://github.com/MrZ-zhy/HJB.git HJB 2>&1 | tail -3
fi
cd HJB
git remote set-url origin "https://x-access-token:${GITHUB_TOKEN}@github.com/MrZ-zhy/HJB.git"
git fetch origin
git checkout trae/solo-agent-TbCBsF
git pull --rebase origin trae/solo-agent-TbCBsF 2>&1 | tail -3 || {
  git reset --hard origin/trae/solo-agent-TbCBsF
}
echo "GITHUB_TOKEN=${GITHUB_TOKEN}" > .env.local
echo "[engine] env ok, HEAD=$(git rev-parse --short HEAD)"

# ── Step 1-7: V5.1 统一入口（积累型）──
python3 核聚变开源贡献系统/自动化PR系统核心/scripts/v5_1/engine.py tick 2>&1 | tee /tmp/v5_1_tick.json
RC=$?
echo "[engine] V5.1 tick exit code: $RC"
```

V5.1 内部 7 步（**单 tick = 推进 1-3 sub-tasks**）：
  Step 1 env_prepare   ── git HEAD + dirty check
  Step 2 preflight     ── V5.1 状态机自检（13 状态可达性）
  Step 3 load_state    ── 从 git 恢复所有 PRWorktree（含 sub-tasks + quality）
  Step 4 state_decide  ── 选 1 个 ACCUMULATING worktree + 1-3 个 ready sub-tasks
  Step 5 execute       ── 调 sub-task handler，per-sub-task try/except 错误隔离
  Step 6 persist       ── save_state() 每 worktree + 原子 commit + push
  Step 7 report        ── TickReport（subtasks_completed/failed/next_action_hint）

---

## V5.1 辅助命令

```bash
# 核心：1 tick 推进 0.03-0.1 PR
python3 v5_1/engine.py tick

# dry-run
python3 v5_1/engine.py tick --dry-run

# 初始化新 PRWorktree（含 sub-task DAG）
python3 v5_1/engine.py init "pr-gym-torax-2510.11283" \
  --project "gym-torax" --paper-id "2510.11283" --paper-title "..." \
  --pr-type "T1" --target-files "gymtorax/rewards.py"

# 列出所有 PRWorktree + 进度
python3 v5_1/engine.py worktrees

# 全局进度
python3 v5_1/engine.py progress

# 状态机自检
python3 v5_1/engine.py validate

# 人类 gate：标记 human_approved=true（V5.1 终点）
python3 v5_1/engine.py promote pr-gym-torax-2510.11283
```

---

## Sub-task DAG 示例（gym-torax T1 PR）

```
st-001 READ_PAPER          (读 arXiv:2510.11283 abstract)
   ↓
st-002 READ_PAPER          (extract contract)
st-003 READ_PAPER          (extract reference values)
   ↓        ↓
st-004 ANALYZE_CODE        (扫项目结构)
   ↓
st-005 ANALYZE_CODE        (读 rewards.py 列出所有 getter)
   ↓        ↓
st-006 CROSS_CHECK         (paper vs code gap)
   ↓
st-007 WRITE_TEST          (创建 test 骨架)
   ↓
st-008 WRITE_TEST          (为每个 gap 写 test)
   ↓
st-009 WRITE_TEST          (oracle 断言)
   ↓
st-010 WRITE_DOCSTRING     (加论文引用)
   ↓        ↓
st-011 VERIFY_TESTS        (跑 pytest)
st-012 VERIFY_LINT         (跑 lint)
   ↓        ↓
st-013 SELF_CRITIQUE       (自我批评)
   ↓
st-014 WRITE_PR_BODY       (写 PR description)
   ↓
st-015 PERSIST             (commit + push)
```

总耗时：~15 sub-tasks × 1-2 ticks each = 15-30 ticks = **2-4 周 @ 每天 1 tick**

---

## V5.1 质量门槛（PR 提交前必须 8/8 通过）

| # | 标准 | 谁来验证 |
|---|------|---------|
| 1 | `all_subtasks_done` | orchestrator |
| 2 | `tests_pass` | verify_tests handler |
| 3 | `lint_pass` | verify_lint handler |
| 4 | `type_check_pass` | verify_lint handler（V5.1 简化）|
| 5 | `self_critique_pass` | self_critique handler |
| 6 | `paper_cited` | write_citation / write_docstring |
| 7 | `pr_body_complete` | write_pr_body handler |
| 8 | `human_approved` | **人类 gate**（`promote <pr_id>`） |

`promote` 命令把 `human_approved=true`，状态 → READY_TO_SUBMIT。

---

## V5.1 文件结构

```
核聚变开源贡献系统/自动化PR系统核心/scripts/v5_1/
├── engine.py                       # CLI: tick / init / worktrees / progress / validate / promote
├── core/
│   ├── models.py                   # EngineState / PRWorktree / SubTask / QualityCriteria / Paper
│   ├── state_machine.py            # 13 状态 + 邻接表 + 自检
│   ├── event_bus.py
│   ├── orchestrator.py             # 7 步工作流（积累型核心）
│   └── quality_gate.py            # 8 项质量门槛
├── sources/arxiv.py                # arXiv 客户端
├── pr_worktree/
│   ├── decomposer.py              # 论文 → DAG of sub-tasks
│   └── executor.py                # 14 个 sub-task handler
├── persistence/
│   └── worktree_state.py          # V5_1/WORKTREES/<pr_id>/state.json
└── WORKTREES/                      # 实际积累目录（git-tracked）
    ├── pr-gym-torax-2510.11283/
    │   ├── state.json
    │   └── notes/
    ├── pr-openreactor-sheath2025/
    └── pr-fuse-2409.05894/
```

---

## V5.1 vs V5 vs V4 全维度对比

| 维度 | V4 | V5 | **V5.1** |
|------|----|----|----|
| 触发源 | issue 池 | arXiv | arXiv（沿用） |
| 决策粒度 | PR | PR | **Sub-task** |
| 持久化 | main + sub tables | progress table | **PRWorktree.state.json** |
| PR-as- | 1-shot | 1-shot | **DAG of 10-30 subtasks** |
| 频率 | 1-2 PR/day | 0-1 PR/week | **0-1 PR/月**（质量爆表） |
| 中断恢复 | 重新跑 | 重新跑 | **sub-task N+1 继续** |
| 质量门禁 | 无 | 8 项 pre-PR | **8 项 quality（per sub-task verify）** |
| 完成度跟踪 | 二元 | 进度 0-100% | **N/M 百分比 + 每个 sub-task 状态** |
| 状态机 | 14 状态 | 11 状态 | **13 状态** |
| 频率 | 1-2/day | 0-1/week | **0-1/month（但每 PR 极质）** |

---

## 12 月展望（V5.1 期望值）

- 同步活跃 PRWorktree: **2-4 个**
- 单 PR sub-tasks: 10-30
- 单 tick 推进: 1-3 sub-tasks
- 单 PR 周期: 2-4 周
- 月 PR 数量: 1-2 个（但每个都是高质量）
- 期望合并率: **40-60%**（vs V4 <5% / V5 20-40%）
- 维护者 review 周期: <14 天
- contributor 资格达成: 6-12 月（vs V4 永远 / V5 6-12 月）
