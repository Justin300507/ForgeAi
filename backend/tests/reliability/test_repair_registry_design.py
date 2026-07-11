"""
Experiment 053, Task 3: RepairRegistry design tests.

This is a standalone module NOT yet wired into any live dispatch
mechanism (see app/repair/registry.py's own docstring for why). These
tests prove the design actually replicates the properties that matter for
a future migration: deterministic priority ordering, per-entry failure
isolation, and the `fn(root) or 0` convention every existing dispatch
mechanism already uses -- so a future migration changes call sites, not
every individual patcher's contract.

Run directly: python tests/reliability/test_repair_registry_design.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from app.repair.registry import RepairRegistry


def test_runs_in_priority_order_not_registration_order():
    registry = RepairRegistry()
    order = []
    registry.register("third", lambda root: order.append("third") or 0, priority=30)
    registry.register("first", lambda root: order.append("first") or 0, priority=10)
    registry.register("second", lambda root: order.append("second") or 0, priority=20)
    registry.run("fake_root")
    assert order == ["first", "second", "third"]


def test_ties_broken_by_registration_order_stable():
    registry = RepairRegistry()
    order = []
    registry.register("a", lambda root: order.append("a") or 0, priority=10)
    registry.register("b", lambda root: order.append("b") or 0, priority=10)
    registry.register("c", lambda root: order.append("c") or 0, priority=10)
    registry.run("fake_root")
    assert order == ["a", "b", "c"]


def test_default_priority_when_unspecified():
    registry = RepairRegistry()
    order = []
    registry.register("explicit_low", lambda root: order.append("explicit_low") or 0, priority=1)
    registry.register("default", lambda root: order.append("default") or 0)  # priority=100
    registry.run("fake_root")
    assert order == ["explicit_low", "default"]


def test_one_raising_entry_does_not_stop_the_rest():
    registry = RepairRegistry()
    ran = []
    registry.register("first", lambda root: ran.append("first") or 0, priority=1)

    def boom(root):
        raise RuntimeError("simulated crash")
    registry.register("crashes", boom, priority=2)
    registry.register("third", lambda root: ran.append("third") or 0, priority=3)

    counts = registry.run("fake_root")
    assert ran == ["first", "third"]
    assert counts["crashes"] == 0


def test_fn_or_zero_convention_for_none_return():
    registry = RepairRegistry()
    registry.register("returns_none", lambda root: None, priority=1)
    counts = registry.run("fake_root")
    assert counts["returns_none"] == 0


def test_fn_or_zero_convention_preserves_real_counts():
    registry = RepairRegistry()
    registry.register("returns_five", lambda root: 5, priority=1)
    counts = registry.run("fake_root")
    assert counts["returns_five"] == 5


def test_args_and_kwargs_pass_through_to_every_entry():
    received = []
    registry = RepairRegistry()
    registry.register("a", lambda root, flag=None: received.append((root, flag)) or 0)
    registry.run("the_root", flag="skip_protected")
    assert received == [("the_root", "skip_protected")]


def test_register_as_decorator():
    registry = RepairRegistry()
    calls = []

    @registry.register("decorated", priority=5)
    def _fn(root):
        calls.append(root)
        return 1

    counts = registry.run("fake_root")
    assert calls == ["fake_root"]
    assert counts["decorated"] == 1


def test_ordered_names_exposes_execution_order_for_migration_assertions():
    registry = RepairRegistry()
    registry.register("b", lambda root: 0, priority=20)
    registry.register("a", lambda root: 0, priority=10)
    # A future migration would assert this matches the exact sequence
    # being replaced (e.g. REPAIR_GRAPH.md's documented 40-call order)
    # BEFORE switching a live call site over -- this is what makes that
    # assertion possible without running the pipeline.
    assert registry.ordered_names() == ["a", "b"]


def test_empty_registry_runs_cleanly():
    registry = RepairRegistry()
    assert registry.run("fake_root") == {}


if __name__ == "__main__":
    import traceback
    tests = [obj for name, obj in list(globals().items()) if name.startswith("test_")]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"PASS: {t.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"FAIL: {t.__name__}: {e}")
        except Exception:
            failed += 1
            print(f"ERROR: {t.__name__}:")
            traceback.print_exc()
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    raise SystemExit(1 if failed else 0)
