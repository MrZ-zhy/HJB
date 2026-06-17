# Pre-PR 报告 #002

**生成时间**: 2026-06-15
**项目**: FUSE
**仓库**: ProjectTorreyPines/FUSE.jl
**目标 PR 类型**: T2 数学/算法文档增强（docstring enhancement）
**优先级**: ⭐⭐⭐⭐⭐（最高，论文就是 FUSE 自己的）
**沙盒可跑性**: ⚠️ Julia 未安装（仅能 PR docs/，不能跑 test suite）

---

## 1. 项目是什么 / 解决什么问题

**FUSE（Fusion Synthesis Engine）** 是 General Atomics 发布的开源聚变电站综合设计框架：

- **领域**: 聚变示范电站（FPP, Fusion Pilot Plant）整体设计
- **架构**: Julia 写的模块化框架，集成第一性原理模型 + 机器学习 + 降阶模型
- **核心抽象**:
  - `dd`：遵循 ITER IMAS ontology 的数据结构
  - `Actors`：物理与工程计算模块（plasma equilibrium、heating、neutronics、balance of plant...）
  - `act`：Actor 控制参数
  - `ini`：0D 初始化参数
- **使用流程**: `ini, act = case_parameters(:FPP) → FUSE.init(ini, act) → FUSE.ActorStationaryPlasma(dd, act)`
- **核心问题域**: 解决传统 0D systems code（如 PROCESS、ARIES、GASC）的过度简化问题，把 plasma physics、engineering、control、costing 统一到一个框架

---

## 2. 候选论文

来自 arXiv（2024+）：

| # | arXiv ID | 标题 | 年份 | 期刊/会议 | 匹配点 |
|---|----------|------|------|----------|--------|
| 1 | [2409.05894](https://arxiv.org/abs/2409.05894) | **FUSE (Fusion Synthesis Engine): A Next Generation Framework for Integrated Design of Fusion Pilot Plants** | 2024 | arXiv preprint → 目标 J. Plasma Phys. | **FUSE 自己的论文**（Meneghini et al.）|
| 2 | 待补 | **DKEKAN: A single-parameterized KAN surrogate for Drift Kinetic Equation** | 2026 | physics.plasm-ph | DKE → FUSE 内部 transport actor |
| 3 | 待补 | **Machine learning prediction of plasma behavior from discharge configurations** | 2026 | physics.plasm-ph | ML plasma → FUSE 的 ML 降阶模型 |

**选定论文（本次 PR 目标）**: #1 — FUSE 自己的论文
- **理由**: 这是 FUSE 项目**官方引用**论文，**FUSE 内部代码应该明确引用它**
- **当前 upstream gap**:
  - `docs/src/pubs.md` 虽有 "Related publications" 段落，但**FUSE 自己的 arXiv 论文 (2409.05894) 未列入**
  - `src/actors/` 大部分 actor 文件**没有 docstring** 说明对应的物理方程 + 论文引用
  - `README.md` 的 "Citation" 段落指向 DOI 但不直接给 arXiv 链接
- **价值高**：维护者对"自引"最欢迎——这是项目作者本人的论文

---

## 3. 上游覆盖检查（Upstream Coverage）

```
[FUSE] scanned 1,796,185 chars
  FUSE: 1747 hits
  synthesis: 4 hits
  tokamak: 84 hits
  pilot plant: 4 hits
  IMAS: 2242 hits
```

**Gap 分析**:
- ✅ `FUSE`、`IMAS`、`tokamak` 高频出现，文档丰富
- ❌ 论文 arXiv ID `2409.05894` **几乎不被引用**（除 CITATION.cff 之外）
- ❌ `src/actors/` 多数 actor 文件缺少顶层 docstring 解释物理方程来源
- ❌ `docs/src/pubs.md` 未列 FUSE 2024 论文（这是项目核心引用）

**结论**: FUSE 自己的论文在文档/代码层**未被充分引用**。

---

## 4. PR 计划

### 标题（建议）
```
docs(citation): add FUSE 2024 arXiv paper to pubs.md and actor docstrings
```

### 改动范围
- **修改文件 1**: `docs/src/pubs.md`
  - 在 "Related publications" 段首添加：
    ```
    - **FUSE: A Next Generation Framework for Integrated Design of Fusion Pilot Plants**
      O. Meneghini et al. (General Atomics), arXiv:2409.05894 (2024)
      https://arxiv.org/abs/2409.05894
    ```
- **修改文件 2**: `src/actors/stationary_plasma_actor.jl`（或类似核心 actor）
  - 在文件顶部 docstring 引用 arXiv:2409.05894
  - 简述该 actor 求解的物理方程（Grad-Shafranov 平衡 + transport）
- **修改文件 3**: `README.md`（可选）
  - 在 "Citation" 段增加 arXiv 链接（除 DOI 之外）

### 引用方式
PR description 里包含：
1. 论文 arXiv 链接
2. 完整 BibTeX
3. 解释这是 FUSE 官方论文，应该被项目所有文档引用
4. 验证：纯 docs 改动，CI 应自动通过 markdown lint

### 期望影响
- 上游文档自洽性提升（"这是 FUSE 自己的论文，没被自己引用"是尴尬漏洞）
- 维护者感受到"AI 关注项目元数据"——T2/T5 是低风险高价值
- PR 合并率预期：**85-95%**（自引 + 纯文档）

---

## 5. 不提交的理由 / 风险

- ❌ **不重写 actor 物理**：物理实现由 GA 团队负责，AI 改不动也不该改
- ❌ **不引入新依赖**：FUSE 的 Project.toml 不能乱动
- ❌ **不动 IMAS 集成代码**：那是 FUSE 的核心契约
- ✅ **只动 docs + docstring**，零代码逻辑变化

---

## 6. 等待批准

- [ ] 用户确认论文 #1（FUSE 自己的）
- [ ] 用户确认 PR 类型 T2
- [ ] 用户确认目标文件 `docs/src/pubs.md` + actor docstring
- [ ] 用户确认沙盒跑不了 Julia（不强求 test verification）

**人工 gate 之后才会生成 patch + push + create PR。**
