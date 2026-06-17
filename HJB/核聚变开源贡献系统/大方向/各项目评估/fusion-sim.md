# fusion-sim - 浏览器端托卡马克等离子体模拟器

## 基本信息

| 字段 | 值 |
|------|-----|
| 项目名称 | fusion-sim |
| 全称 | fusionsimulator.io |
| GitHub | https://github.com/d-burg/fusion-sim |
| 开发者 | Daniel Burgess (Columbia University) |
| 语言 | Rust + WebAssembly + React + Three.js |
| 许可证 | 开源 |
| 领域 | 托卡马克放电实时模拟 |

## 项目描述

哥伦比亚大学开发的浏览器端托卡马克等离子体实时模拟器。基于Rust+WebAssembly的自定义物理引擎，支持DIII-D、JET、ITER、CENTAUR四种装置。使用IPB98(y,2)约束定标律、0D功率平衡、Bosch-Hale聚变反应率等物理模型，200步/秒实时运行。

## AI可开发性评估（/5）

| 维度 | 得分 | 理由 |
|------|------|------|
| AI可开发性 | 3 | Rust+WASM+React，技术栈较复杂 |
| 贡献门槛 | 4 | 学术项目，欢迎贡献 |
| 活跃度 | 3 | 个人/小团队项目 |
| 核工程价值 | 3 | 教育工具，非工程级模拟 |
| 技术栈匹配 | 2 | Rust+WASM，AI生成代码能力较弱 |

**综合得分：15/25**

## 适合AI贡献的方向

1. **React前端**：UI改进、响应式设计
2. **文档**：物理模型说明、开发者指南
3. **测试**：Rust单元测试、前端测试
4. **新装置配置**：添加更多托卡马克参数
5. **Three.js可视化**：3D渲染改进

## 风险评估

- Rust语言AI代码生成质量不如Python
- 需要前端+后端+物理三方面知识
- WASM编译调试复杂

## 推荐优先级：★★☆☆☆
