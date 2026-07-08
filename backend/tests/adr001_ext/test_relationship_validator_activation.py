"""
Fixture-based tests for ADR-001 extension Phase B: enrich_relationships_from_models()
(app/contract/adapter.py) feeding real relationship data into
ContractEntity.relationships, activating the previously permanently-inert
check_contract_conformance()._check_relationship_targets_exist().

Plain assert-based (no pytest installed in this project) -- run directly:
python tests/adr001_ext/test_relationship_validator_activation.py
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from app.contract.adapter import enrich_relationships_from_models
from app.contract.models import AppContract, ContractApp, ContractEntity
from app.contract.validator import check_contract_conformance

FIXTURE_POST = '''
from sqlalchemy import Column, Integer, String, ForeignKey
from sqlalchemy.orm import relationship
from app.database import Base

class Post(Base):
    __tablename__ = "posts"
    id = Column(Integer, primary_key=True)
    title = Column(String(250), nullable=False)
    author_id = Column(Integer, ForeignKey("users.id"), nullable=False)

    author = relationship("User", back_populates="posts")
    tags = relationship("Tag", secondary="post_tags", back_populates="posts")
'''

FIXTURE_POST_BROKEN_TARGET = '''
from sqlalchemy import Column, Integer, String, ForeignKey
from sqlalchemy.orm import relationship
from app.database import Base

class Post(Base):
    __tablename__ = "posts"
    id = Column(Integer, primary_key=True)
    title = Column(String(250), nullable=False)
    author_id = Column(Integer, ForeignKey("users.id"), nullable=False)

    author = relationship("NonexistentEntity", back_populates="posts")
'''


def _make_project(model_content: str) -> str:
    tmp = tempfile.mkdtemp()
    models_dir = os.path.join(tmp, "app", "models")
    os.makedirs(models_dir, exist_ok=True)
    with open(os.path.join(models_dir, "post.py"), "w", encoding="utf-8") as f:
        f.write(model_content)
    return tmp


def test_enrich_populates_relationships_and_infers_kind():
    project_path = _make_project(FIXTURE_POST)
    contract = AppContract(
        app=ContractApp(name="test"),
        entities=[
            ContractEntity(name="Post", table_name="posts"),
            ContractEntity(name="User", table_name="users"),
            ContractEntity(name="Tag", table_name="tags"),
        ],
    )
    added = enrich_relationships_from_models(contract, project_path)
    assert added == 2, added

    post_entity = contract.entity_by_name("Post")
    by_target = {r.target: r for r in post_entity.relationships}

    assert by_target["User"].kind == "many_to_one"  # Post holds the FK to users
    assert by_target["User"].back_populates == "posts"

    assert by_target["Tag"].kind == "many_to_many"  # secondary= present
    assert by_target["Tag"].back_populates == "posts"


def test_validator_was_inert_without_enrichment():
    # Confirms the "previously permanently inert" claim directly: build
    # the same contract, skip enrichment, run the conformance check --
    # zero relationship-related findings possible, since
    # ContractEntity.relationships is still empty (the actual gap this
    # phase closes).
    contract = AppContract(
        app=ContractApp(name="test"),
        entities=[
            ContractEntity(name="Post", table_name="posts"),
            ContractEntity(name="User", table_name="users"),
        ],
    )
    diagnostics = check_contract_conformance(contract, __import__("pathlib").Path("."))
    relationship_diags = [d for d in diagnostics if "relationship" in d.message.lower()]
    assert relationship_diags == []


def test_validator_activated_flags_broken_relationship_target():
    project_path = _make_project(FIXTURE_POST_BROKEN_TARGET)
    contract = AppContract(
        app=ContractApp(name="test"),
        entities=[
            ContractEntity(name="Post", table_name="posts"),
            ContractEntity(name="User", table_name="users"),
            # Note: "NonexistentEntity" deliberately never added here.
        ],
    )
    added = enrich_relationships_from_models(contract, project_path)
    assert added == 1

    from pathlib import Path
    diagnostics = check_contract_conformance(contract, Path(project_path))
    relationship_diags = [d for d in diagnostics if "relationship" in d.message.lower()]
    assert len(relationship_diags) == 1, diagnostics
    assert "NonexistentEntity" in relationship_diags[0].message


def test_validator_activated_passes_valid_relationship_target():
    project_path = _make_project(FIXTURE_POST)
    contract = AppContract(
        app=ContractApp(name="test"),
        entities=[
            ContractEntity(name="Post", table_name="posts"),
            ContractEntity(name="User", table_name="users"),
            ContractEntity(name="Tag", table_name="tags"),
        ],
    )
    enrich_relationships_from_models(contract, project_path)

    from pathlib import Path
    diagnostics = check_contract_conformance(contract, Path(project_path))
    relationship_diags = [d for d in diagnostics if "relationship" in d.message.lower()]
    assert relationship_diags == [], relationship_diags


def test_enrich_no_models_dir_returns_zero():
    with tempfile.TemporaryDirectory() as tmp:
        contract = AppContract(app=ContractApp(name="test"))
        assert enrich_relationships_from_models(contract, tmp) == 0


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
