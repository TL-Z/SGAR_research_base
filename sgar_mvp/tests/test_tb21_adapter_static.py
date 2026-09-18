from pathlib import Path

from sgar_mvp.benchmarks.terminalbench21.task_loader import (
    CANONICAL_TASK_COUNT,
    CANONICAL_TASK_TREE_SHA256,
    load_task,
    load_tasks,
)


TASK_ROOT = Path(
    "/ssd/zhoutianle/sgar-benchmarks/terminalbench21/official_tb21/terminal-bench-2-1"
)


def test_frozen_tb21_inventory_is_admissible():
    tasks, tree_hash = load_tasks(TASK_ROOT)
    assert len(tasks) == CANONICAL_TASK_COUNT
    assert tree_hash == CANONICAL_TASK_TREE_SHA256
    assert all(task.test_script.is_file() for task in tasks)
    assert all("-vfready" not in task.image for task in tasks)


def test_task_loader_preserves_instruction_and_official_verifier():
    task = load_task(TASK_ROOT, "regex-log")
    assert task.instruction.startswith("Write a regex expression")
    assert task.test_script.name == "test.sh"
    assert task.allow_internet is True
    assert task.verifier_timeout_sec == 900.0
