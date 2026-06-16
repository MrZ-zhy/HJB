# Open FUSION Toolkit

## 基本信息

| 字段 | 值 |
|------|-----|
| 项目名称 | Open FUSION Toolkit (OFT) |
| GitHub | https://github.com/openfusiontoolkit/OpenFUSIONToolkit |
| 开发者 | C. Hansen等 |
| 语言 | Python + Fortran/C++ |
| 许可证 | 开源 |
| 领域 | 聚变等离子体科学工程仿真 |

## 项目描述

开源聚变与等离子体科学工程框架，基于有限元方法。包含TokaMaker（自由边界Grad-Shafranov平衡）、ThinCurr（3D薄壁电磁模拟）、MUG（3D MHD模拟）、Marklin（3D力自由平衡求解器）等核心工具。

## AI可开发性评估（/5）

| 维度 | 得分 | 理由 |
|------|------|------|
| AI可开发性 | 3 | Python接口+Fortran核心，修改核心需Fortran知识 |
| 贡献门槛 | 4 | 学术项目，欢迎贡献，有issue跟踪 |
| 活跃度 | 4 | 持续开发，有发表记录 |
| 核工程价值 | 5 | 托卡马克平衡和MHD核心工具 |
| 技术栈匹配 | 3 | Python接口友好，但核心是Fortran |

**综合得分：19/25**

## 适合AI贡献的方向

1. **Python接口改进**：API易用性、类型注解
2. **文档和示例**：更多教程、Jupyter notebook示例
3. **测试**：Python层测试补充
4. **后处理工具**：结果可视化、数据导出
5. **安装改进**：跨平台安装脚本、依赖管理

## 风险评估

- Fortran核心代码修改门槛高
- 需要等离子体物理背景
- 构建系统可能复杂

## 推荐优先级：★★★☆☆
