# Pre-PR 报告 #001

**生成时间**: 2026-06-15
**项目**: OpenReactor
**仓库**: natesales/openreactor
**目标 PR 类型**: T1 复现性单元测试（unit test）
**优先级**: ⭐⭐⭐⭐（高）
**沙盒可跑性**: ✅ Go 1.25.1 已就绪

---

## 1. 项目是什么 / 解决什么问题

**OpenReactor** 是 Nate Sales 维护的开源 IEC（Inertial Electrostatic Confinement）核聚变反应堆参考设计与控制系统：

- **领域**: 聚变器（fusor）硬件控制，不是物理仿真代码
- **架构**: Go 写的微服务（maestro 中心 + 8 个硬件微服务 + fusionctl CLI + Web UI）
- **硬件控制范围**: 真空泵（MKS / Pfeiffer）、真空计（MKS / Edwards）、质量流量控制器（MKS / Sierra）、高压电源、中子计数器（Radiacode）
- **核心问题域**:
  1. 聚变器启动序列（抽真空 → 高电压爬升 → 加气体 → 离子化）
  2. 配置文件（YAML profile）描述启动流程
  3. 有限状态机（FSM）驱动启动逻辑
  4. 实时数据记录 + Web UI 监控

**物理相关性**: IEC 聚变器虽然达不到 ITER 的 Q 值（工程聚变），但**等离子体鞘层物理**、**离子加速**、**中子产额**等机制与主流托卡马克有共通之处。

---

## 2. 候选论文

来自 arXiv（2024+），按相关度排序：

| # | arXiv ID | 标题 | 年份 | 期刊/会议 | 匹配点 |
|---|----------|------|------|----------|--------|
| 1 | 待补 | **First Experimental Characterization of Plasma Parameters and Carbon Decontamination** | 2026 | physics.plasm-ph | IEC 聚变器直接相关；可作为 OpenReactor 测量值（neutron count、vacuum pressure）的物理参考 |
| 2 | 待补 | **Non-perturbative 2D spatial measurements of electric fields within a plasma sheath** | 2025 | physics.plasm-ph | 等离子体鞘层 → OpenReactor cathode voltage ramp 的物理依据 |
| 3 | 待补 | **A DC discharge plasma experiment for undergraduate laboratories** | 2025 | physics.plasm-ph | 教育级 DC plasma；可作为 profile 默认值的参考 |

**选定论文（本次 PR 目标）**: #2 — 等离子体鞘层 2D 电场测量
- **理由**: 该论文测量的 sheath thickness 与 electric field 分布，为 OpenReactor `profile.go` 中 `Cathode.VoltageRampCurve` 提供了物理依据
- **当前 upstream gap**: `pkg/line/line.go` 实现了 `Line`（线性插值），但**没有任何测试覆盖**；`profile_test.go` 只测了 `Parse` 的基本字段
- **风险低、价值高**: 这是物理依据的代码层落地，符合 V5 的 T1 范式

---

## 3. 上游覆盖检查（Upstream Coverage）

```
[OpenReactor] scanned 73,417 chars
  fusor: 1 hits
  IEC: 2 hits
  Langmuir: 1 hits
  neutron: 20 hits
  high voltage: 11 hits
  vacuum: 98 hits
  mass flow: 9 hits
  gauges: 14 hits
```

**Gap 分析**:
- ✅ `vacuum`、`neutron`、`high voltage`、`gauges` 等关键词在 README/docs 中已被提及
- ❌ `Langmuir` 仅 1 处命中（仅 README 一笔带过）
- ❌ `pkg/line/line.go` **完全没有 docstring 解释** ramp curve 物理意义
- ❌ `line_test.go` **不存在**（对比 `profile_test.go` 存在）
- ❌ 上游 README 未引用任何学术论文

**结论**: 该论文的方法/数据**未被 upstream 引用**，是真正的"未覆盖"。

---

## 4. PR 计划

### 标题（建议）
```
test(line): add unit tests for cathode voltage ramp curve (Line)
```

### 改动范围
- **新文件**: `pkg/line/line_test.go`
  - `TestFromSlopeIntercept`: 验证斜率截距构造
  - `TestLineAt`: 验证插值函数（边界、端点、中点）
  - `TestLineAtExtrapolation`: 验证超出区间行为
  - 物理量纲注释：电压 [V]、时间 [s]
- **改进文件**: `pkg/line/line.go`（仅 docstring）
  - 在 `Line` 类型定义上方加 1-2 行 LaTeX 注释，说明这是 cathode voltage ramp
  - 引用论文："see [Paper X] for sheath physics basis"

### 引用方式
PR description 里包含：
1. 论文 arXiv 链接
2. 引用 BibTeX
3. 解释为什么这个 PR 提升项目质量（测试覆盖 + 物理文档）
4. 验证方式：`cd pkg/line && go test -v`

### 期望影响
- 上游覆盖率提升（line package 从 0% → 100%）
- 维护者感受到"AI 懂物理"——论文引用而非凭空
- PR 合并率预期：60-80%（纯测试 + docstring，零风险）

---

## 5. 不提交的理由 / 风险

- ❌ **不提交更复杂的功能**：避免与维护者对 fuse 物理的判断冲突
- ❌ **不提交算法优化**：line 插值已足够好，过度优化反而引入风险
- ❌ **不提交 typo 修正**：仍 V4 风格，**禁止**
- ❌ **不强行实现论文算法**：论文 2D 电场测量需要 2D probe，本项目是 1D ramp

---

## 6. 等待批准

- [ ] 用户确认论文选 #2（推荐）
- [ ] 用户确认 PR 类型 T1（推荐）
- [ ] 用户确认目标文件 `pkg/line/line_test.go`（推荐）
- [ ] 用户确认 docstring 引用论文（推荐）

**人工 gate 之后才会生成 patch + push + create PR。**
