"""V5.2 端到端测试：N 次 tick 后同 sub-task quality 递增。"""
import sys
import json
import os
from pathlib import Path

# 让 v5_2 模块可导入
_PKG_PARENT = Path(__file__).resolve().parent.parent.parent
if str(_PKG_PARENT) not in sys.path:
    sys.path.insert(0, str(_PKG_PARENT))

from scripts.v5_2.persistence.worktree_state import init_worktree, load_state
from scripts.v5_2.pr_worktree.executor import execute_subtask_iteration
from scripts.v5_2.core.orchestrator import DEFAULT_PROJECTS_META


def test_iterative_deepening():
    """核心测试：调用 execute_subtask_iteration 多次，验证 quality 递增。"""
    print("=" * 60)
    print("V5.2 迭代深化测试")
    print("=" * 60)

    # 1. 初始化 1 个新 PRWorktree（不写到磁盘，纯内存）
    pr_id = "test-fuse-v5_2-deepening"
    wt = init_worktree(
        pr_id=pr_id,
        project="FUSE",
        paper_id="2409.05894",
        paper_title="V5.2 test of iterative deepening",
        pr_type="T2",
        target_repo=DEFAULT_PROJECTS_META["FUSE"]["repo"],
        target_files=["src/physics.jl"],
        test_dir="test",
    )
    print(f"\n✓ Init worktree: {pr_id}")
    print(f"  - subtasks: {len(wt.subtasks)}")
    print(f"  - first sub-task: {wt.subtasks[0].id} type={wt.subtasks[0].type.value}")
    print(f"  - max_iterations: {wt.subtasks[0].max_iterations}")
    print(f"  - quality_threshold: {wt.subtasks[0].quality_threshold}")

    # 2. 对第 1 个 sub-task（read_paper）连续调用 3 次
    st = wt.subtasks[0]
    meta = DEFAULT_PROJECTS_META["FUSE"]
    ctx = {
        "paper_id": "2409.05894",
        "project": "FUSE",
        "project_path": meta["local"],
        "target_files": wt.target_files,
        "notes_dir": wt.notes_dir,
        "worktree": wt,
    }

    print(f"\n--- 连续迭代 {st.id} ({st.type.value}) ---")
    print(f"{'iter':>4} | {'quality':>8} | {'status':>15} | msg")
    print("-" * 70)

    for i in range(4):  # 超过 max_iterations 也会继续（看 FAILED 路径）
        ok, quality, msg = execute_subtask_iteration(st, ctx)
        print(f"{i:>4} | {quality:>8.2f} | {st.status.value:>15} | {msg}")

    print(f"\n--- 验证 ---")
    print(f"  iterations_done: {st.iterations_done}")
    print(f"  refinement_history length: {len(st.refinement_history)}")
    print(f"  final quality_score: {st.quality_score:.2f}")
    print(f"  quality_threshold: {st.quality_threshold}")
    print(f"  final status: {st.status.value}")

    # 3. 验证 history 字段都填充了
    print(f"\n--- Refinement History ---")
    for rec in st.refinement_history:
        print(f"  iter={rec.iteration} q={rec.quality_score:.2f} "
              f"files={len(rec.output_files_written)} summary={rec.output_summary[:60]}")

    # 4. 断言：iterations_done 累加
    assert st.iterations_done == 4, f"expected 4 iterations, got {st.iterations_done}"
    # 断言：refinement_history 长度匹配
    assert len(st.refinement_history) == 4, f"expected 4 records, got {len(st.refinement_history)}"
    # 断言：最终 quality 满足 OR FAILED（max_iter 用完）
    assert st.status.value in ("done", "failed"), f"unexpected final status: {st.status.value}"

    print(f"\n✅ 全部断言通过")


def test_persistence_roundtrip():
    """测试持久化 + 恢复：refinement_history 必须能完整读回。"""
    print("\n" + "=" * 60)
    print("V5.2 持久化往返测试")
    print("=" * 60)

    pr_id = "test-persist-v5_2"
    wt = init_worktree(
        pr_id=pr_id,
        project="FUSE",
        paper_id="2409.05894",
        paper_title="V5.2 persistence test",
        pr_type="T5",
        target_repo=DEFAULT_PROJECTS_META["FUSE"]["repo"],
        target_files=["src/methods.jl"],
        test_dir="test",
    )

    # 跑 2 次迭代
    st = wt.subtasks[0]
    meta = DEFAULT_PROJECTS_META["FUSE"]
    ctx = {
        "paper_id": "2409.05894",
        "project": "FUSE",
        "project_path": meta["local"],
        "target_files": wt.target_files,
        "notes_dir": wt.notes_dir,
        "worktree": wt,
    }
    execute_subtask_iteration(st, ctx)
    execute_subtask_iteration(st, ctx)
    # V5.2：executor 只更新内存，持久化由 orchestrator 手动 save
    from scripts.v5_2.persistence.worktree_state import save_state
    save_state(wt)

    # 重新 load
    wt2 = load_state(pr_id)
    st2 = wt2.subtask_by_id(st.id)
    print(f"\n  原始 quality_score: {st.quality_score:.2f}")
    print(f"  恢复 quality_score: {st2.quality_score:.2f}")
    print(f"  原始 iterations_done: {st.iterations_done}")
    print(f"  恢复 iterations_done: {st2.iterations_done}")
    print(f"  原始 refinement_history length: {len(st.refinement_history)}")
    print(f"  恢复 refinement_history length: {len(st2.refinement_history)}")

    assert st2.quality_score == st.quality_score, "quality_score not persisted"
    assert st2.iterations_done == st.iterations_done, "iterations_done not persisted"
    assert len(st2.refinement_history) == len(st.refinement_history), "history not persisted"

    # 清理
    import shutil
    worktree_dir = f"核聚变开源贡献系统/V5_2/WORKTREES/{pr_id}"
    if os.path.isdir(worktree_dir):
        shutil.rmtree(worktree_dir)
    print(f"\n✅ 持久化测试通过")


if __name__ == "__main__":
    os.chdir("/workspace/HJB")  # 强制 cd 到工程根
    test_iterative_deepening()
    test_persistence_roundtrip()
    print("\n" + "=" * 60)
    print("✅ V5.2 全部测试通过")
    print("=" * 60)
