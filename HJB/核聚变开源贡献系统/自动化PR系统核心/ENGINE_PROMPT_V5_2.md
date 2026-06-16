# 核聚变开源贡献自动化系统 · 执行引擎提示词 · **V5.2（积累型 / 论文驱动 / 迭代深化）**

> **版本**: V5.2
> **生效日期**: 2026-06-15
> **取代**: V5.1（PR-as-DAG, 1 tick 切换 sub-task） → V5.2（**PR-as-DAG + 迭代深化**, 1 tick 优先细化现有 sub-task）
> **核心变更**: **多次调用系统 = 同 sub-task 更多算力, PR 数量不变**

---

## 🎯 核心理念（务必先读懂）

> **PR 数量固定 2 周 1 个; 调用系统的次数 = 给"同一 sub-task"投入更多算力**
>
> 用户原话: "再频繁也不会影响PR数量, 比如说两个周一个PR, 在此期间我调用再多次这个系统也不会让这个数量改变, 只会让更细的任务获得更大的智能和算力, 比如调用一次可能是一次性读取多篇论文, 调用十次就成了一次读一篇论文, 那算力和细节把握、高智力产出物肯定也更多"
>
> **V5.2 核心机制: 1 tick = 1 iteration**
>   - 调度优先级: **细化现有 sub-task（quality < threshold）** > **新开 sub-task**
>   - 每个 sub-task 携带 `max_iterations` + `quality_threshold`（来自 DEFAULT_PARAMS）
>   - 每次 tick 给同一个 sub-task 一次 iteration（handler 知道是第几次深化）
>   - 每次 iteration 写一条 `RefinementRecord` 到 `refinement_history`
>   - 当 `quality_score >= quality_threshold`: 状态 → DONE
>   - 当 `iterations_done >= max_iterations` 但 quality 仍 < threshold: 状态 → FAILED
>   - 否则: 状态回 PENDING（等下次 tick 再深化）
>
> **用户在 PR 周期内调用 N 次系统:**
>   - N 少: sub-task 可能停留在 iteration 1 / 2（浅, 质量 50-60）
>   - N 多: 可以走到 iteration 3 / 4, quality_score 逐步逼近 70-100
>   - **PR 数量不变; 单 sub-task 质量变高**

---

## V5.2 vs V5.1 根本差异

| 维度 | V5.1 | **V5.2** |
|------|----|----|
| 1 tick = | 推进 1-3 sub-task **到 DONE** | **1 次 iteration（细化或首次）** |
| 调度优先级 | 切到新 sub-task | **细化现有 sub-task 优先** |
| Sub-task 生命周期 | PENDING → IN_PROGRESS → DONE | **PENDING → IN_PROGRESS → PENDING (细化) → ... → DONE/FAILED** |
| Iteration 上限 | 1（不可深化） | **每 type 不同 (1~4, 见 DEFAULT_PARAMS)** |
| Quality 评分 | 仅 binary（DONE/非 DONE） | **0-100 评分 (quality_score)** |
| Quality 门槛 | 8 项硬性 | **8 项硬性 + 2 项分数门槛 (avg≥75, min≥60)** |
| 持久化 | subtask.status | **subtask.quality_score + refinement_history[]** |
| 调用次数影响 | 切换 sub-task 进度 | **同 sub-task 质量分递增** |
| Compute density | 固定 | **4 模式 (quick/default/deep/burst)** |

---

## V5.2 Compute Density 模式

每次 tick 给多少算力 / 多少 sub-task 推进:

| Density | sub_tasks/tick | effective max_iter | 用途 |
|---------|---------------|---------------------|------|
| **quick** | 1 | 1 (强制) | V5.1 兼容模式（每 tick 完成 1 个浅 sub-task） |
| **default** | 1 | type default (1~4) | **推荐**：每 tick 深化 1 个 sub-task |
| **deep** | 1 | type default × 2 | 高智模式：每 tick 给同 sub-task 2x 算力 |
| **burst** | 3 | 1 each | 高吞吐：每 tick 推进 3 个浅 sub-task |

```bash
python3 v5_2/engine.py density              # 查看当前
python3 v5_2/engine.py density --set deep   # 切换
python3 v5_2/engine.py tick --density deep  # 单次覆盖
```

---

## V5.2 DEFAULT_PARAMS（每 SubTaskType 的 max_iterations + quality_threshold）

```python
DEFAULT_PARAMS = {
    "read_paper":      {"max_iterations": 3, "quality_threshold": 70.0},
    "extract_contract": {"max_iterations": 2, "quality_threshold": 75.0},
    "analyze_code":    {"max_iterations": 4, "quality_threshold": 75.0},
    "cross_check":     {"max_iterations": 3, "quality_threshold": 80.0},
    "write_test":      {"max_iterations": 2, "quality_threshold": 75.0},
    "write_docstring": {"max_iterations": 2, "quality_threshold": 70.0},
    "write_citation":  {"max_iterations": 1, "quality_threshold": 60.0},
    "write_pr_body":   {"max_iterations": 3, "quality_threshold": 75.0},
    "verify_tests":    {"max_iterations": 1, "quality_threshold": 60.0},
    "verify_lint":     {"max_iterations": 1, "quality_threshold": 60.0},
    "verify_build":    {"max_iterations": 1, "quality_threshold": 60.0},
    "self_critique":   {"max_iterations": 3, "quality_threshold": 75.0},
    "persist":         {"max_iterations": 1, "quality_threshold": 50.0},
    "blocked":         {"max_iterations": 1, "quality_threshold": 100.0},
}
```

---

## V5.2 状态机（沿用 V5.1 13 状态）

```
backlog → analyzing → decomposing → accumulating ⇄ self_review
                                          ↓
                                    ready_to_submit → awaiting_gate → pr_submitting
                                                                       ↓
                                                                  submitted → merged/closed
```

新增 `strategy_mode = "iterative_deepening"`，PRWorktree 新增 `compute_density` 字段。

---

## 铁律 35 条（V5.2: V5.1 全部沿用 + V5.2-1..V5.2-5）

**V5.1-1 积累型**: 1 tick 最多 3 个 sub-tasks (在 burst mode 下)
**V5.1-2 持久化**: 每个 sub-task iteration 必须 save_state() + commit
**V5.1-3 中断可恢复**: state.json 必含 refinement_history, 下一 tick 从 PENDING 继续深化
**V5.1-4 quality 全通过**: PR 不达 8 项硬性 + 2 项分数门槛不可提交
**V5.1-5 慢就是快**: 期望每 PR 2-4 周持续推进

**V5.2-1 迭代深化优先**: tick 调度优先级 = refinement_subtasks() > pending_ready_subtasks()
**V5.2-2 单 sub-task 多次 iteration**: max_iterations 上限内, 同 sub-task 可被多次深化
**V5.2-3 quality 评分 0-100**: 每次 iteration 必评分; quality < threshold 必细化
**V5.2-4 PR 数量固定**: 用户多次调用不增加 PR 数量, 只增加单 sub-task 质量
**V5.2-5 持久化 refinement_history**: 每次 iteration 必写 RefinementRecord（不可丢失）

---

## V5.2 7 步工作流（1 tick = 1 iteration）

```bash
echo "[engine] V5.2 tick start, ts=$(date -Iseconds)"

# ── Step 0: 环境（沿用 V4/V5/V5.1）──
export GITHUB_TOKEN="${GITHUB_TOKEN}"   # 已在会话 env 中注入
export HJB_ROOT="/workspace/HJB"
git config --global user.email "engine@fusion-contrib.local"
git config --global user.name  "Fusion-Contrib Engine v5.2"
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

# ── Step 1-7: V5.2 统一入口（迭代深化）──
python3 核聚变开源贡献系统/自动化PR系统核心/scripts/v5_2/engine.py tick 2>&1 | tee /tmp/v5_2_tick.json
RC=$?
echo "[engine] V5.2 tick exit code: $RC"
```

V5.2 内部 7 步（**单 tick = 1 次 iteration，优先细化现有**）：
  Step 1 env_prepare   ── git HEAD + dirty check + density mode
  Step 2 preflight     ── V5.2 状态机自检（13 状态可达性）
  Step 3 load_state    ── 从 git 恢复所有 PRWorktree（含 refinement_history）
  Step 4 state_decide  ── 选 1 个 ACCUMULATING worktree + 1 个 iteration（**优先 refinement**）
  Step 5 execute       ── 调 sub-task handler（handler 接收 iteration 编号，输出深化）
  Step 6 persist       ── save_state() 每 worktree + 原子 commit + push
  Step 7 report        ── TickReport（iterations_completed/refining/failed + quality 分）

### 调度核心逻辑（V5.2）

```python
def _select_iteration(self):
    n_iters = sub_tasks_per_tick(self.density)  # 1 或 3

    # 1) 优先 ready worktree（人类 gate 提示）
    if self.state.ready_worktrees():
        return ready[0], []  # 不调 sub-task

    # 2) active worktree
    wt = self.state.active_worktrees()[0]

    # 2a) 优先细化现有 sub-task（V5.2 核心）
    refine_tasks = wt.refinement_subtasks()  # quality < threshold 且 iterations < max
    if refine_tasks:
        return wt, refine_tasks[:n_iters]  # **多次 tick 会反复打到这些 task**

    # 2b) 没有需细化的 → 开新 sub-task
    ready_tasks = wt.pending_ready_subtasks()
    if ready_tasks:
        return wt, ready_tasks[:n_iters]

    # 2c) 等待依赖
    return wt, []
```

---

## V5.2 辅助命令

```bash
# 核心：1 tick = 1 iteration（迭代深化）
python3 v5_2/engine.py tick                    # 默认 density
python3 v5_2/engine.py tick --density deep    # 单次高智模式
python3 v5_2/engine.py tick --dry-run         # 不持久化

# 查看/修改 density
python3 v5_2/engine.py density
python3 v5_2/engine.py density --set deep

# 手动细化 1 个 sub-task（不走调度）
python3 v5_2/engine.py refine pr-fuse-2409.05894 st-002

# 列出 worktree 全部 sub-tasks + quality
python3 v5_2/engine.py subtask-list pr-fuse-2409.05894

# 显示 1 个 worktree 的 quality 详情
python3 v5_2/engine.py show pr-fuse-2409.05894

# 初始化新 PRWorktree（含 sub-task DAG + 迭代参数）
python3 v5_2/engine.py init "pr-gym-torax-2510.11283" \
  --project "gym-torax" --paper-id "2510.11283" --paper-title "..." \
  --pr-type "T1" --target-files "gymtorax/rewards.py"

# 列出所有 PRWorktree + 进度
python3 v5_2/engine.py worktrees

# 全局进度
python3 v5_2/engine.py progress

# 状态机自检
python3 v5_2/engine.py validate

# 人类 gate：标记 human_approved=true
python3 v5_2/engine.py promote pr-gym-torax-2510.11283
```

---

## Sub-task DAG 示例（V5.2: read_paper 节点可被深化 3 次）

```
st-001 READ_PAPER          (max_iter=3, threshold=70)
   ↓
st-002 EXTRACT_CONTRACT    (max_iter=2, threshold=75)
   ↓
... (12-15 个 nodes) ...
   ↓
st-015 PERSIST             (max_iter=1, threshold=50)
```

每次 tick 给 st-001 一次 iteration：
- tick 1: iter=0, q=43.8, status=PENDING（还需细化）
- tick 2: iter=1, q=61.3, status=PENDING（还需细化）
- tick 3: iter=2, q=78.5, status=DONE（达到 70 threshold）
- tick 4: 调度自动切到 st-002（因为 st-001 已 DONE）

---

## V5.2 质量门槛（PR 提交前必须 10/10 通过）

### 8 项 V5.1 硬性门禁（沿用）

| # | 标准 | 谁来验证 |
|---|------|---------|
| 1 | `all_subtasks_done` | orchestrator |
| 2 | `tests_pass` | verify_tests handler |
| 3 | `lint_pass` | verify_lint handler |
| 4 | `type_check_pass` | verify_lint handler（沿用 V5.1 简化）|
| 5 | `self_critique_pass` | self_critique handler |
| 6 | `paper_cited` | write_citation / write_docstring |
| 7 | `pr_body_complete` | write_pr_body handler |
| 8 | `human_approved` | **人类 gate**（`promote <pr_id>`） |

### 2 项 V5.2 新增分数门槛

| # | 标准 | 含义 |
|---|------|------|
| 9 | `avg_subtask_quality >= 75` | 所有 DONE sub-task 的 quality_score 平均分 |
| 10 | `min_subtask_quality >= 60` | 最低分（避免一个低分拖累 PR） |

---

## V5.2 文件结构

```
核聚变开源贡献系统/自动化PR系统核心/scripts/v5_2/
├── engine.py                       # CLI: tick/init/worktrees/progress/validate/promote
│                                   # + V5.2: refine/density/subtask-list/show
├── core/
│   ├── models.py                   # V5.2: +ComputeDensity +RefinementRecord
│   │                               # +DEFAULT_PARAMS +SubTask.iteration/quality 字段
│   ├── compute_budget.py           # V5.2 新建: effective_max_iterations() + sub_tasks_per_tick()
│   ├── state_machine.py            # 13 状态 + 邻接表 + 自检
│   ├── event_bus.py
│   ├── orchestrator.py             # V5.2: 调度细化优先 (refinement > new)
│   └── quality_gate.py            # V5.2: 8 项硬性 + 2 项分数门槛
├── sources/arxiv.py                # arXiv 客户端（沿用 V5.1）
├── pr_worktree/
│   ├── decomposer.py              # V5.2: 每 sub-task 自动注入 max_iter + threshold
│   └── executor.py                # V5.2: 14 个 handler 接收 iteration 参数
│                                   # + score_subtask() 启发式评分
├── persistence/
│   └── worktree_state.py          # V5.2/WORKTREES/<pr_id>/state.json
│                                   # + 持久化 refinement_history + quality_score
├── test_v5_2.py                    # 端到端测试（迭代深化 + 持久化往返）
└── WORKTREES/                      # 实际积累目录（git-tracked）
    ├── pr-gym-torax-2510.11283/
    │   ├── state.json             # 含 refinement_history
    │   └── notes/                 # 每次 iteration 写一份
    ├── pr-openreactor-sheath2025/
    └── pr-fuse-2409.05894/
```

---

## V5.2 vs V5.1 vs V5 vs V4 全维度对比

| 维度 | V4 | V5 | V5.1 | **V5.2** |
|------|----|----|----|----|
| 触发源 | issue 池 | arXiv | arXiv | arXiv（沿用）|
| 决策粒度 | PR | PR | Sub-task | **Sub-task iteration** |
| 持久化 | main + sub tables | progress table | PRWorktree.state.json | **+ refinement_history** |
| PR-as- | 1-shot | 1-shot | DAG of 10-30 subtasks | **DAG + 多次迭代** |
| 频率 | 1-2 PR/day | 0-1 PR/week | 0-1 PR/月 | **0-1 PR/月**（PR 数固定）|
| 调用次数影响 | 切 PR | 切 PR | 切 sub-task | **同 sub-task 质量递增** |
| 中断恢复 | 重新跑 | 重新跑 | sub-task N+1 继续 | **iteration N+1 继续** |
| 质量门禁 | 无 | 8 项 pre-PR | 8 项 quality | **8 项硬性 + 2 项分数** |
| 完成度跟踪 | 二元 | 进度 0-100% | N/M 百分比 | **+ 每 sub-task quality 0-100** |
| 状态机 | 14 状态 | 11 状态 | 13 状态 | 13 状态（沿用）|
| Density | 无 | 无 | 无 | **quick/default/deep/burst** |

---

## 12 月展望（V5.2 期望值）

- 同步活跃 PRWorktree: **2-4 个**
- 单 PR sub-tasks: 10-30
- 单 sub-task iterations: 1-4（按 type 不同）
- 单 tick 推进: **1 次 iteration**（不是 1 个 sub-task）
- 单 PR 周期: 2-4 周（不变）
- 月 PR 数量: 1-2 个（**不变**）
- 期望合并率: **60-80%**（vs V5.1 40-60%, V5 20-40%, V4 <5%）
- 维护者 review 周期: <14 天
- contributor 资格达成: 6-12 月（vs V5.1 6-12 月）

---

## V5.2 迁移说明

1. **状态兼容**: V5.2 完全兼容 V5.1 的 state.json schema（多读多写，向下兼容）
2. **目录**: V5.2 独立使用 `V5_2/WORKTREES/`，与 V5.1 的 `V5_1/WORKTREES/` 不冲突
3. **触发方式**: 定时任务 prompt 把 `v5_1/engine.py tick` 改为 `v5_2/engine.py tick`
4. **可恢复**: 如果 V5.2 出问题，回滚到 V5.1 只需要把 prompt 改回去
