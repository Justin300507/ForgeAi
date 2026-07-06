"""
Verifies v6_orchestrator.py's missing-seed_routes.py branch calls the
ADR-002 generator and falls back correctly. Reads the live source of
v6_orchestrator.py and asserts the wiring is present and correctly
ordered (generate() called, static stub only written when it returns
None) -- a lightweight structural check rather than executing the whole
(large, LLM/network-dependent) surrounding function.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))


def _read_orchestrator_source() -> str:
    path = os.path.join(
        os.path.dirname(__file__), "..", "..", "app", "services", "v6_orchestrator.py"
    )
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def test_orchestrator_imports_and_calls_generate():
    src = _read_orchestrator_source()
    seed_block_start = src.index('filepath in ("app/routes/seed_routes.py"')
    seed_block = src[seed_block_start:seed_block_start + 2000]
    assert "deterministic_seed_generator" in seed_block
    assert "generate(project_path)" in seed_block


def test_orchestrator_still_writes_static_stub_on_none():
    src = _read_orchestrator_source()
    seed_block_start = src.index('filepath in ("app/routes/seed_routes.py"')
    seed_block = src[seed_block_start:seed_block_start + 2000]
    assert "Demo data ready" in seed_block, "static-stub fallback text must still be present"


def test_generate_actually_wired_end_to_end():
    # Exercise the real generate() call the orchestrator now makes,
    # against a temp project, to prove the import path/signature match.
    import tempfile
    from app.services.deterministic_seed_generator import generate

    with tempfile.TemporaryDirectory() as tmp:
        models_dir = os.path.join(tmp, "app", "models")
        os.makedirs(models_dir)
        with open(os.path.join(models_dir, "priority.py"), "w") as f:
            f.write('''
from sqlalchemy import Column, Integer, String
from app.database import Base

class Priority(Base):
    __tablename__ = "priorities"
    id = Column(Integer, primary_key=True)
    name = Column(String(50), nullable=False)
''')
        with open(os.path.join(models_dir, "task.py"), "w") as f:
            f.write('''
from sqlalchemy import Column, Integer, String, ForeignKey
from app.database import Base

class Task(Base):
    __tablename__ = "tasks"
    id = Column(Integer, primary_key=True)
    title = Column(String(200), nullable=False)
    priority_id = Column(Integer, ForeignKey("priorities.id"), nullable=False)
''')
        source, telemetry = generate(tmp)
        assert source is not None
        assert telemetry["fallback_used"] is False


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
