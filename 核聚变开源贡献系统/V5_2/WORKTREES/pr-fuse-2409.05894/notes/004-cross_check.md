# Cross-Check: Paper vs Code (iter=1)

## Paper contract source: `002-read_paper.md`
## Code surface source: `003-analyze_code.md`

**Paper keywords sampled**: 25
**In code**: 1  |  **Paper-only (gap)**: 24

## Paper mentions but Code lacks

- Fusion
- Synthesis
- Generation
- Integrated
- Pilot
- Plants

## Code has but Paper doesn't mention

- `test/runtests_study_database.jl`
- `test/runtests_basics.jl`
- `test/runtests_cases.jl`
- `test/runtests_init_expressions.jl`
- `test/test_zmq_actor.jl`

## Both have, need test coverage

- physics

## Refined gap (iter=1)

- Top-3 paper-only terms: Fusion, Synthesis, Generation
- Recommendation: prioritize docs/tests for: Fusion, Synthesis, Generation, Integrated, Pilot
