# Pre-PR 报告 #003

**生成时间**: 2026-06-15
**项目**: gym-torax
**仓库**: antoine-mouchamps/gymtorax
**目标 PR 类型**: T1 复现性单元测试
**优先级**: ⭐⭐⭐⭐（高）
**沙盒可跑性**: ✅ Python 3.14.4 + poetry 已就绪

---

## 1. 项目是什么 / 解决什么问题

**Gym-TORAX** 是比利时列日大学（Université de Liège, Montefiore Institute）2025 年发布的开源 Python 包，**把 Google DeepMind 的 TORAX 等离子体仿真器包装成 Gymnasium 环境**，让 RL 研究者可以训练 tokamak 等离子体控制策略：

- **领域**: RL 应用于 tokamak 等离子体控制（plasma current、NBI、ECRH 控制）
- **架构**:
  - `gymtorax/envs/`：Gymnasium 环境（当前只有 ITER ramp-up）
  - `gymtorax/rewards.py`：从 TORAX state dict 提取 Q、β_N、τ_E、H_98 等标量
  - `gymtorax/action_handler.py`：动作空间映射
  - `gymtorax/observation_handler.py`：观测空间映射
  - `gymtorax/torax_wrapper/`：TORAX 接口
- **核心问题域**:
  1. 把物理仿真器变成 RL 训练可用的环境
  2. 提供标准的 obs/action/reward 接口
  3. 让 RL 研究者无需了解 TORAX 内部就能训练控制策略
- **首个环境**: ITER 启动（ramp-up）场景

---

## 2. 候选论文

来自 arXiv（2024+）：

| # | arXiv ID | 标题 | 年份 | 期刊/会议 | 匹配点 |
|---|----------|------|------|----------|--------|
| 1 | [2510.11283](https://arxiv.org/abs/2510.11283) | **Gym-TORAX: Open-source software for integrating reinforcement learning with plasma control simulators in tokamak research** | 2025 (v2 2026) | arXiv cs.LG | **gym-torax 自己的论文**（Mouchamps et al.）|
| 2 | 待补 | **Offline Reinforcement Learning for Plasma Control in Nuclear Fusion: Codebase and Benchmark** | 2026 | arXiv cs.LG | 离线 RL → gym-torax 在线训练的补充 |
| 3 | 待补 | **Plasma Shape Control via Zero-shot Generative Reinforcement Learning** | 2025 | arXiv | shape control → gym-torax action space 扩展 |

**选定论文（本次 PR 目标）**: #1 — Gym-TORAX 自己的论文
- **理由**: 项目自己的论文**定义了 state dict 的标量键名**（Q_fusion、beta_N、tau_E、h_98 等），而 `rewards.py` 的所有函数都基于这些键名——**这是"论文 → 代码"最直接的对应**
- **当前 upstream gap**:
  - `rewards.py` 有 7+ 个 getter 函数，**全部没有单元测试**
  - 论文里 Fig. 2-3 展示了 state dict 的具体结构
  - **任何键名拼写错误都会让 RL 训练静默失败**——单元测试可以 1 行 `assert` 抓住
- **价值高**：纯测试 + 论文引用 = 维护者最欢迎的 PR 类型

---

## 3. 上游覆盖检查（Upstream Coverage）

```
[gym-torax] scanned 238,025 chars
  tokamak: 4 hits
  plasma: 75 hits
  control: 64 hits
  TORAX: 474 hits
  ITER: 81 hits
  ramp-up: 2 hits
  magnetic: 10 hits
  gym: 182 hits
```

**Gap 分析**:
- ✅ `TORAX`、`ITER`、`gym`、`plasma`、`control` 高频出现
- ❌ `tests/` 目录存在但**没有 `test_rewards.py`**
- ❌ `rewards.py` 函数 `get_fusion_gain` / `get_beta_N` / `get_tau_E` / `get_h98` 等**没有 docstring 引用论文**
- ❌ state dict 标量键名（`Q_fusion`, `beta_N` 等）**没有 constant 文件**集中定义，全是 magic string
- ❌ README 引用了论文但**代码注释里没引**

**结论**: 论文定义了接口契约，但代码层**没有测试 + 没有集中常量**。

---

## 4. PR 计划

### 标题（建议）
```
test(rewards): add unit tests for state-scalar getters (arXiv:2510.11283)
```

### 改动范围
- **新文件**: `tests/test_rewards.py`
  - 用 synthetic state dict（结构与论文 Fig. 2 一致）
  - 测试每个 getter 返回正确值
  - 测试键名拼写错误的早期检测
  - 总共 ~50-80 行
- **改进文件**: `gymtorax/rewards.py`
  - 在模块 docstring 顶部加 arXiv:2510.11283 引用
  - 简述 state dict 标量键名来源（论文 Section 2.2）

### 引用方式
PR description 里包含：
1. 论文 arXiv 链接
2. 完整 BibTeX
3. 解释：state dict 键名是论文接口契约，单元测试确保不漂移
4. 验证：`poetry run pytest tests/test_rewards.py -v`

### 期望影响
- `rewards.py` 从 0% 测试覆盖 → 100% 覆盖
- 未来 TORAX 升级时键名变化的 regression test
- 维护者感受到"AI 引用了自己论文 + 加了测试"——典型 contributor PR
- PR 合并率预期：70-90%

---

## 5. 不提交的理由 / 风险

- ❌ **不引入新 gym 环境**：当前是 ITER ramp-up only，加新环境需要 RL 验证
- ❌ **不修改 TORAX wrapper 接口**：那是 TORAX 团队职责
- ❌ **不实现论文里的新算法**（如 zero-shot generative RL）：需要 GPU + 大规模实验
- ✅ **只动 tests + 模块 docstring**，零功能变化

---

## 6. 等待批准

- [ ] 用户确认论文 #1（gym-torax 自己的）
- [ ] 用户确认 PR 类型 T1
- [ ] 用户确认目标文件 `tests/test_rewards.py`（推荐新建）+ `gymtorax/rewards.py` docstring
- [ ] 用户确认沙盒可跑 pytest

**人工 gate 之后才会生成 patch + push + create PR。**
