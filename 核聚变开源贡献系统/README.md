# 核聚变开源贡献自动化系统

> 让AI持续、自动地向核聚变开源项目贡献代码的系统

## 系统设计理念

### 核心思想：多轮对话持续运行

本系统基于以下设计哲学构建：

1. **单次执行 = 推进一个节点**：每次定时任务触发，AI只完成一个小步骤
2. **进度表 = 唯一状态中枢**：所有状态信息持久化到 `进度表.md`
3. **定时任务 = 持续运行引擎**：通过高频触发实现系统不间断工作
4. **人工最小介入**：系统设计为完全自主运行，仅在异常时需要人工

### 为什么需要频繁触发？

免费版AI套餐不支持超长对话，但支持定时任务多轮触发。本系统利用这一特性：

- **每次触发 → 读取进度表 → 执行一步 → 写回进度表**
- 通过每小时触发一次，系统可以持续推进开发
- 即使单次执行失败，下一次触发会从进度表恢复状态

## 系统架构（云端原生 · V4 事件驱动引擎）

> **V4 升级（v2.1 → v4）**：从「脚本集合 + 临时决策矩阵」升级为「事件驱动 + 状态机一等公民 + 决策规则化 + 统一入口」。
> 详细推导见 [`自动化PR系统核心/V4架构.md`](自动化PR系统核心/V4架构.md)（第一性原理）。
> 关键：状态机是**代码**（`v4/core/state_machine.py`），不是文档；决策是**规则注册表**（`v4/strategies/decision_matrix.py`）；失败**可隔离**（per-strategy try/except）。

```
┌─────────────────────────────────────────────┐
│    Trae 定时任务（每小时触发，新隔离会话）    │
└─────────────────┬───────────────────────────┘
                  │
                  ▼
        ┌──────────────────────────┐
        │  Step 0: 环境准备（云端）│
        │  git pull --rebase       │
        │  + token + git 身份       │
        └──────────┬───────────────┘
                   │
                   ▼
   ┌───────────────────────────────────────┐
   │  V4 统一入口 (一条命令)               │
   │  python3 v4/engine.py tick           │
   │                                       │
   │  Step 1 env_prepare   ── git HEAD sha │
   │  Step 2 preflight     ── 5 项健康检查 │
   │  Step 3 load_state    ── parse + 子表 │
   │  Step 4 state_decide  ── 4 strategies │
   │  Step 5 execute       ── 错误隔离     │
   │  Step 6 persist       ── 原子 commit+push │
   │  Step 7 report        ── TickReport JSON │
   └───────────────┬───────────────────────┘
                   │
                   ▼
        ┌──────────────────────────┐
        │  GitHub (MrZ-zhy/HJB)    │
        │  trae/solo-agent-TbCBsF  │
        │  100% 云端状态           │
        └──────────────────────────┘
```

## V4 文件结构

```
核聚变开源贡献系统/
├── README.md                          ← 本文件
├── 进度表.md                          ← 系统核心枢纽（V4 codeblock 是 EngineState 唯一真源）
│
├── 大方向/                            ← 项目选择与评估
│   ├── 项目评估总览.md                 ← 8 个项目的综合排名
│   └── 各项目评估/                     ← 每个项目的详细评估
│
├── 自动化PR系统核心/                   ← 系统运行机制 + V4 引擎源码
│   ├── V4架构.md                       ← V4 架构文档（第一性原理）
│   ├── ENGINE_PROMPT.md                ← V4 Trae 调度 prompt 模板
│   ├── 系统配置.md                     ← 系统架构、Prompt、执行逻辑
│   ├── 开发链条模板.md                 ← P1-P4 标准开发链条
│   ├── PR策略.md                       ← 贡献类型、Issue 筛选、PR 模板
│   ├── 系统维护检查清单.md             ← 维护流程、自愈、协调循环
│   ├── 贡献状态机.md                   ← 贡献任务状态转换（V4: 文档 + 代码双轨）
│   └── scripts/
│       └── v4/                         ← V4 引擎源码（统一入口）
│           ├── engine.py               ← 统一 CLI（tick / status / report / project / validate）
│           ├── core/
│           │   ├── orchestrator.py    ← 7 步工作流
│           │   ├── state_machine.py   ← 14 状态 + 转换表 + 守卫
│           │   ├── event_bus.py       ← 事件总线
│           │   └── models.py          ← ProjectState / EngineState / Action
│           ├── strategies/
│           │   ├── base.py            ← Strategy protocol + 自动发现
│           │   ├── decision_matrix.py ← 5 规则注册表
│           │   ├── pr_strategy.py     ← PR 触发器（v2 strategy_evaluator 升级）
│           │   ├── project_selector.py← 项目选择（v2 project_rotator 升级）
│           │   └── health_check.py    ← preflight（v2 preflight 升级）
│           ├── persistence/
│           │   ├── progress_table.py  ← 主表读写（治本 regex 继承）
│           │   ├── project_progress.py← 子表读写
│           │   └── git_ops.py         ← git 原子操作
│           └── observability/
│               ├── structured_log.py  ← JSON Lines 日志
│               └── metrics.py         ← RED 指标 + SLO 燃烧率
│
└── 项目/                              ← per-project 子进度表（rule #13：状态 100% 落 GitHub）
    ├── OpenReactor/
    │   └── 进度表.md                   ← 项目级状态机
    ├── TORAX/
    │   └── 进度表.md
    └── ...
```

## 核心机制

### 1. 进度表（云端状态中枢）

`进度表.md` 是整个系统的唯一状态中枢，**保存在 GitHub 仓库的 `trae/solo-agent-TbCBsF` 分支上**，不依赖任何本地会话的临时文件系统。

定时任务每次触发时：

0. **从云端同步环境**（git clone / pull --rebase）
1. **从 GitHub 读取进度表**，获取：
   - `新一轮开发状态`：进行中 / 已完成
   - `NEXT_ACTION`：INIT / DEVELOP / MAINTAIN / WAIT
   - `CURRENT_CHAIN_NODE`：当前链状节点ID
   - `DEVELOP_PHASE`：开发阶段
   - `CONTRIBUTION_STATUS`：贡献状态
   - `LOCK`：是否锁定
   - 检查点状态、WIP状态、错误预算等

2. **根据状态决定操作**：
   - `LOCK=true` → 停止操作
   - `新一轮开发状态=进行中` → 执行系统维护
   - `新一轮开发状态=已完成` → 启动新一轮开发

3. **即改即提交写回 GitHub**：
   - 每次修改文件 → 立即 `git add` + `git commit`
   - 每次执行结束 → `git push` 回 `trae/solo-agent-TbCBsF`
   - 状态变更 = commit，commit 失败 = 状态未持久化（必须重试）

### 2. 链状开发流程

每个项目的开发按 P1→P2→P3→P4 顺序推进：

```
P1 环境搭建          ── fork、clone、配置环境
  ├─ P1.1 代码研读   ── 阅读项目结构、核心模块
  ├─ P1.1.1 架构理解 ── 绘制模块依赖关系图
  ├─ P1.1.2 Issue筛选── 找到适合AI贡献的Issue
  └─ P1.2 测试验证   ── 在 clone 的工作目录里运行测试套件 [检查点]
P2 开发实施          ── 根据选定Issue开发
  ├─ P2.1 方案设计   ── 设计实现方案 [检查点]
  ├─ P2.2 编码实现   ── 编写代码 [质量门禁]
  ├─ P2.3 测试验证   ── 运行测试 [检查点]
  └─ P2.4 代码审查   ── Critic Agent审查 [检查点]
P3 PR提交            ── 创建Pull Request
  ├─ P3.1 分支整理   ── 整理commit [质量门禁]
  └─ P3.2 PR创建     ── 推送、创建PR [检查点]
P4 后续跟进          ── 跟踪PR审核
  ├─ P4.1 响应反馈   ── 根据review修改
  └─ P4.2 合并确认   ── 确认PR被合并
```

### 3. 质量保障机制

- **5个质量门禁**：编码、测试、审查、提交、PR创建
- **反思循环**：代码生成后，Critic Agent审查，最多3轮自动修订
- **检查点机制**：关键节点后保存状态，支持断点续传
- **WIP限制**：全局PR≤5，单仓库PR≤1，防止过载
- **SLO监控**：4项关键指标，错误预算耗尽时切换保守策略
- **漂移检测**：对比期望状态与实际状态，自动修复偏差
- **自愈机制**：CI失败自动修复、分支漂移自动rebase

### 4. 持续运行保障

- **每次只推进一个节点**：避免单次执行时间过长
- **每次更新进度表**：保证状态持久化
- **每小时触发一次**：保证持续推进
- **异常自动恢复**：从检查点恢复，WIP超限进入WAIT状态

## 运行流程

### 首次启动

1. 定时任务触发（每小时，新隔离会话）
2. **第 0 步**：`git clone` 仓库到 `/workspace/HJB/`，checkout `trae/solo-agent-TbCBsF`
3. 读取 `/workspace/HJB/核聚变开源贡献系统/进度表.md`，发现 `NEXT_ACTION=INIT`
4. 读取 `项目评估总览.md`，选择最高优先级项目（TORAX）
5. 读取 `PR策略.md`、`贡献状态机.md` 了解规则
6. fork 目标仓库、clone 源码到 `/workspace/HJB/项目/<name>/`、创建特性分支
7. 更新进度表 → **立即 commit + push**：
   - 写入当前开发项目信息
   - `CURRENT_CHAIN_NODE=P1`
   - `DEVELOP_PHASE=analyzing`
   - `CONTRIBUTION_STATUS=ANALYZING`
   - `新一轮开发状态=进行中`
8. 下次触发时进入P1.1代码研读阶段

### 持续开发

1. 定时任务触发
2. **第 0 步**：`git pull --rebase` 同步最新状态
3. 读取进度表，发现 `新一轮开发状态=已完成`、`NEXT_ACTION=DEVELOP`
4. 检查WIP限制、错误预算
5. 定位 `CURRENT_CHAIN_NODE` 对应的节点
6. 执行该节点的工作（如P1.1代码研读）
7. 完成后 → **立即 commit + push**：
   - 更新链条（标记当前节点✅，生长下一个节点⏳）
   - 保存检查点
   - 更新 `CURRENT_CHAIN_NODE`、`DEVELOP_PHASE`、`CONTRIBUTION_STATUS`
8. 下次触发时推进到下一个节点

### 系统维护

当 `新一轮开发状态=进行中` 时（如PR已提交等待Review）：

1. **第 0 步**：`git pull --rebase` 同步最新状态
2. 验证进度表完整性
3. 项目源码同步（`git pull`）
4. 分支状态检查
5. PR状态跟踪与漂移检测
6. 协调循环执行（自动修复漂移）
7. 自愈触发检查
8. 贡献指标采集
9. SLO监控与错误预算
10. 更新维护状态 → **立即 commit + push**

## 如何验证系统正常运行

### 1. 进度表状态验证

- 检查 `进度表.md` 的操作指令区
- 所有字段格式正确
- `新一轮开发状态` 在「进行中」和「已完成」之间合理切换
- `LAST_UPDATE` 日期与最新执行时间一致

### 2. 链条推进验证

- 检查 `进度表.md` 的链状开发进度
- 节点状态从⏳→🔄→✅正常流转
- 每完成一个节点，生长下一个⏳节点
- 关键节点后保存了检查点

### 3. PR提交验证

- 检查 `项目/` 目录下对应项目的目录
- 存在fork的源码
- 存在特性分支
- GitHub上有对应的PR
- PR使用Dependabot式模板
- 遵循Conventional Commits

### 4. 维护执行验证

- 检查系统维护状态表
- 所有检查项为✅
- `LAST_UPDATE` 已更新
- 漂移事件已记录
- 指标已采集

## 核心规则（不可违反 · V4 25 条铁律）

> V4 铁律分三组（详见 `ENGINE_PROMPT.md`）：
> - **系统铁律 S1-S8**（继承 v1/v2）
> - **状态机铁律 M1-M7**（v4 状态机实现强制执行）
> - **策略铁律 P1-P10**（decision_matrix 规则化）

1. **核心进度表机制不变**：所有状态信息都在 `进度表.md` 中持久化（V4 codeblock 是 EngineState 唯一真源）
2. **一次只开发一个项目**：遵循 WIP 限制（单仓 PR ≤ 1）
3. **每次执行只推进一个节点**：避免单次执行时间过长
4. **完成 PR 后必须修改进度表状态为「进行中」**：进入维护模式
5. **PR 遵循 Dependabot 式模板**：包含变更摘要、关联 Issue、测试方法、自检清单、回滚方案
6. **不要创建新文件除非项目开发确实需要**
7. **所有项目源码 clone 到 `/workspace/HJB/项目/<name>/` 下**（rule #7 源码头）
8. **每个关键节点后保存检查点**：P1.2、P2.1、P2.3、P2.4、P3.2
9. **反思循环最多 3 轮**：3 轮未通过则回退到 P2.1
10. **WIP 超限时进入 WAIT 状态**：等待下次触发重新检查
11. **错误预算耗尽时切换保守策略**：暂停新 PR 提交
12. **漂移检测发现不一致时自动修复**：无法修复则标记告警
13. **状态 100% 落 GitHub**：不依赖本地文件
14. **每次执行结束必须有 commit 或明确的 no-op 证据**
15. **任何失败必须更新 LAST_HEARTBEAT_STATUS**
16. **任何 sed 状态写入必须经 engine_helper 验证**（V4 治本：`_atomic_update_fields`）
17. **任何 PR 创建前必须经 strategy_evaluator 评估**（V4: pr_strategy.evaluate）
18. **任何新轮次启动必须经 project_rotator 选项目**（V4: project_selector.evaluate）
19. **任何凭据调用必须先经 preflight 验证**（V4: health_check.evaluate_health）
20. **helper 脚本运行产物（__pycache__/）必须 .gitignore 排除**
21. **状态机转换必须合法**（V4 state_machine.is_legal 守卫）
22. **策略失败可隔离**（V4 per-strategy try/except，axiom A3）
23. **决策可审计**（V4 Action 携带 rationale + payload）
24. **错误预算耗尽 → conservative 模式自动应用**（V4 pr_strategy R4）
25. **V4 自检每日跑**（`engine.py validate`，状态机不变量 + 模块导入 + 铁律完整性）

## 当前状态

- **架构版本**：V4（v2.1 已退役；旧脚本 engine_helper.py / preflight.py / strategy_evaluator.py / project_rotator.py 已删除）
- **存储**：100% GitHub 云端（`MrZ-zhy/HJB` · `trae/solo-agent-TbCBsF` 分支）
- **本地作用**：仅作为 clone 后的临时工作区，每次执行结束必须 push 回去
- **定时任务**：每小时触发一次
- **调度**：0 * * * * （每小时整点）— V4 任务已替换 v2 任务
- **执行引擎**：V4 `核聚变开源贡献自动化引擎 v4`（统一入口 `engine.py tick`）
- **系统状态**：V4 ready（dry-run 验证通过，7 步工作流跑通，decision_matrix 选 action）
- **活跃项目**：OpenReactor (SUBMITTED) + TORAX (BACKLOG)
- **首个目标项目**：OpenReactor (PR #6 live) + TORAX (P1.1 待启动)

## V4 升级到 V5 的触发条件

- ≥ 5 个 PR 走完 V4 全生命周期
- decision_matrix 规则命中 ≥ 1 次真实触发
- ≥ 2 个项目通过 V4 on-board
- Trae 调度连续 7 天无 preflight_failed
- 用户验收 V4 报告格式

否则不升 V5。

## 后续优化方向

- 增加更多项目评估维度（社区氛围、文档质量等）
- 实现更智能的Issue选择（基于历史成功率）
- 增强自愈能力（更多异常场景自动修复）
- 实现并行开发（在WIP允许范围内）
- 集成GitHub Actions实现PR状态实时通知
