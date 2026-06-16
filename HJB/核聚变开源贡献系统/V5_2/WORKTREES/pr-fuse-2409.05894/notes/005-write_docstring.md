# Docstring plan (iter=0) for 1 file(s)

**Paper reference**: arXiv:2409.05894
**Paper title**: FUSE (Fusion Synthesis Engine): A Next Generation Framework for Integrated Design of Fusion Pilot Plants
**Authors**: O. Meneghini, T. Slendebroek, B. C. Lyons, K. McLaughlin, J. McClenaghan

## Target files

- `docs/src/pubs.md`

## Current state of target file

```
# References

```
@article{meneghini2024fuse,
author = {Meneghini, O. and Slendebroek, T. and Lyons, B.C. and McLaughlin, K. and McClenaghan, J. and Stagner, L. and Harvey, J. and Neiser, T.F. and Ghiozzi, A. and Dose, G. and Guterl, J. and Zalzali, A. and Cote, T. and Shi, N. and Weisberg, D. and Smith, S.P. and Grierson, B.A. and Candy, J.},
doi = {10.48550/arXiv.2409.05894},
journal = {arXiv},
title = {{FUSE (Fusion Synthesis Engine): A Next Generation Framework for Integrated Design of Fusion Pilot Plants}},
year = {2024}
}
```

```
@article{Slendebroek_2026,
doi = {10.1088/1741-4326/ae27e
```

> 已存在 arXiv:2409.05894 引用 → 不要再加 bib 条目，应加 method/abstract 说明

## Proposed additions (paper-grounded)

### 论文摘要（中英摘要）

The Fusion Synthesis Engine (FUSE) is a state-of-the-art software suite designed to revolutionize fusion power plant design. FUSE integrates first-principle models, machine learning, and reduced models into a unified framework, enabling comprehensive simulations that go beyond traditional 0D systems studies. FUSE's modular structure supports a hierarchy of model fidelities, from steady-state to time-dependent simulations, allowing for both pre-conceptual design and operational scenario developme

### 方法概述（引自 paper）

FUSE 整合 first-principle 模型、机器学习和降阶模型，
支持稳态到时间相关的多保真度仿真，应用于聚变电厂的预概念设计和运行场景开发。

### 推荐插入位置（target_file 顶部 References 之后）

```markdown

## About FUSE paper (arXiv:2409.05894)

**FUSE (Fusion Synthesis Engine): A Next Generation Framework for Integrated Design of Fusion Pilot Plants**

_O. Meneghini, T. Slendebroek, B. C. Lyons, K. McLaughlin, J. McClenaghan_

The Fusion Synthesis Engine (FUSE) is a state-of-the-art software suite designed to revolutionize fusion power plant design. FUSE integrates first-principle models, machine learning, and reduced models into a unified framework, enabling comprehensive simulations that go beyond traditional 0D systems...

Key contributions:
- First-principle + ML + reduced-model unified framework
- Hierarchy of model fidelities (steady-state → time-dependent)
- Self-consistent solutions across physics, engineering, control
- Open-source (https://github.com/ProjectTorreyPines/FUSE.jl)
```

## 差距提示 (来自 st-004 cross_check)

- Fusion
- Synthesis
- Generation
- Integrated
- Pilot
