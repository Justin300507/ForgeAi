"""
Unit tests for the top-level generate() orchestration and its fallback
boundary. Plain assert-based -- run directly:
python tests/adr002/test_generate.py
"""
import os
import sys
import tempfile
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from app.services import deterministic_seed_generator as gen

FIXTURE_PRIORITY = '''
from sqlalchemy import Column, Integer, String
from app.database import Base

class Priority(Base):
    __tablename__ = "priorities"
    id = Column(Integer, primary_key=True)
    name = Column(String(50), nullable=False)
'''

FIXTURE_TASK = '''
from sqlalchemy import Column, Integer, String, ForeignKey
from app.database import Base

class Task(Base):
    __tablename__ = "tasks"
    id = Column(Integer, primary_key=True)
    title = Column(String(200), nullable=False)
    priority_id = Column(Integer, ForeignKey("priorities.id"), nullable=False)
'''

FIXTURE_ALPHA_CYCLE = '''
from sqlalchemy import Column, Integer, String, ForeignKey
from app.database import Base

class Alpha(Base):
    __tablename__ = "alphas"
    id = Column(Integer, primary_key=True)
    name = Column(String(50), nullable=False)
    beta_id = Column(Integer, ForeignKey("betas.id"), nullable=False)
'''

FIXTURE_BETA_CYCLE = '''
from sqlalchemy import Column, Integer, String, ForeignKey
from app.database import Base

class Beta(Base):
    __tablename__ = "betas"
    id = Column(Integer, primary_key=True)
    name = Column(String(50), nullable=False)
    alpha_id = Column(Integer, ForeignKey("alphas.id"), nullable=False)
'''


def _project_with(tmp, files: dict) -> str:
    models_dir = os.path.join(tmp, "app", "models")
    os.makedirs(models_dir, exist_ok=True)
    for fname, content in files.items():
        with open(os.path.join(models_dir, fname), "w") as f:
            f.write(content)
    return tmp


def test_generate_success_returns_source_and_telemetry():
    with tempfile.TemporaryDirectory() as tmp:
        _project_with(tmp, {"priority.py": FIXTURE_PRIORITY, "task.py": FIXTURE_TASK})
        source, telemetry = gen.generate(tmp)
        assert source is not None
        assert telemetry["adr002_enabled"] is True
        assert telemetry["fallback_used"] is False
        assert telemetry["lookup_entities"] == 1
        assert telemetry["entities_discovered"] == 2
        assert telemetry["generation_time_ms"] >= 0


def test_generate_falls_back_on_empty_project():
    with tempfile.TemporaryDirectory() as tmp:
        source, telemetry = gen.generate(tmp)
        assert source is None
        assert telemetry["fallback_used"] is True
        assert telemetry["fallback_reason"] == "no models discovered"


def test_generate_falls_back_when_no_lookup_entities():
    with tempfile.TemporaryDirectory() as tmp:
        # Task has no incoming FK from anything -- zero lookup candidates.
        _project_with(tmp, {"task.py": '''
from sqlalchemy import Column, Integer, String
from app.database import Base

class Task(Base):
    __tablename__ = "tasks"
    id = Column(Integer, primary_key=True)
    title = Column(String(200), nullable=False)
'''})
        source, telemetry = gen.generate(tmp)
        assert source is None
        assert telemetry["fallback_reason"] == "no lookup entities"


def test_generate_falls_back_on_fk_cycle():
    with tempfile.TemporaryDirectory() as tmp:
        _project_with(tmp, {"alpha.py": FIXTURE_ALPHA_CYCLE, "beta.py": FIXTURE_BETA_CYCLE})
        source, telemetry = gen.generate(tmp)
        assert source is None
        assert telemetry["fallback_reason"] == "FK cycle detected"


def test_generate_never_raises_on_unexpected_exception():
    with tempfile.TemporaryDirectory() as tmp:
        _project_with(tmp, {"priority.py": FIXTURE_PRIORITY, "task.py": FIXTURE_TASK})
        with mock.patch.object(gen, "render_seed_routes", side_effect=RuntimeError("boom")):
            source, telemetry = gen.generate(tmp)
        assert source is None
        assert "boom" in telemetry["fallback_reason"]


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
