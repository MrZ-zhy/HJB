# PR: 改进 docs/src/pubs.md (引用 arXiv:2409.05894)

## 背景 (Paper)

**FUSE (Fusion Synthesis Engine): A Next Generation Framework for Integrated Design of Fusion Pilot Plants**

_O. Meneghini, T. Slendebroek, B. C. Lyons, K. McLaughlin, J. McClenaghan_

> The Fusion Synthesis Engine (FUSE) is a state-of-the-art software suite designed to revolutionize fusion power plant design. FUSE integrates first-principle models, machine learning, and reduced models into a unified framework, enabling comprehensive simulations that go beyond traditional 0D systems studies. FUSE's modular structure supports a hierarchy of model fidelities, from steady-state to ti...

arXiv 链接: https://arxiv.org/abs/2409.05894

## 动机 (Upstream gap)

docs/src/pubs.md 已包含 2409.05894 的 BibTeX 引用，但缺少：
- 论文的摘要/方法概述（读者点进 pubs.md 看不出论文解决了什么）
- paper 与 FUSE 代码关键贡献的对应关系
- Posters/Talks 区块中 2024 论文相关条目尚未标注指向该 arXiv

根据 st-004 cross_check，paper 提到但 code 文档未覆盖的关键词：
- Fusion
- Synthesis
- Generation
- Integrated
- Pilot

## 改动

在 docs/src/pubs.md 的 References 段之后，新增 `## About FUSE paper` 小节，
包含论文标题、作者、摘要（≤300 字）、4 条 key contributions 列表。
不修改现有 BibTeX 条目（已存在 meneghini2024fuse）。

diff 摘要：在 pubs.md 顶部 `# References` 之后插入约 12 行 markdown。

## 验证

- [x] Tests pass (verify_build: exit=0)
- [x] Lint clean (verify_lint: Julia Project.toml + markdown headers OK)
- [x] Self-critique done
- [x] Paper cited (BibTeX already in pubs.md)
- [x] PR body complete (this file)

## 后续（可选）

- [ ] 提议上游在 pubs.md 顶部加 1-2 句导语介绍 FUSE 整体定位
- [ ] 给 Posters/Talks 中 2024 D3D Sept SET 那条加上 arXiv 链接

## 已知风险（来自 self_critique）

- **重复风险**：若上游已经更新过 pubs.md，PR 可能与最新版本冲突——需要在 push 前 rebase
- **长度判断**：摘要在 markdown 中放 300 字截断，可能截到关键术语（如 'self-consistent solutions'）
- **Key contributions 主观性**：4 条是 agent 概括，可能与作者原意略有偏差
