"""
Unit tests for Design Memory (app/design/design_memory.py). Plain
assert-based -- run directly:
python tests/design_intelligence/test_design_memory.py
"""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import app.memory.design_fingerprint as dfp
from app.design.brief import compose_design_brief
from app.design.design_memory import (
    SIMILARITY_THRESHOLD,
    build_design_record,
    divergence_directive,
    most_similar_recent,
    similarity,
)
from app.prompts.design_system import CATEGORIES, build_design_system_injection

IDEA_CRM = "A sales CRM with leads, deals, pipeline and contacts"
IDEA_CRM_2 = "A customer relationship manager for tracking sales deals and pipeline stages"
IDEA_TRAVEL = "A travel itinerary planner with destinations and trip budgets"

_TEST_STORE = os.path.join(os.path.dirname(__file__), "_test_design_memory.json")


def _with_store(entries):
    """Point the fingerprint store at a seeded test file."""
    with open(_TEST_STORE, "w", encoding="utf-8") as f:
        json.dump(entries, f)
    dfp._LOG_PATH = _TEST_STORE


def _restore(original):
    dfp._LOG_PATH = original
    if os.path.exists(_TEST_STORE):
        os.remove(_TEST_STORE)


def _record(idea):
    brief = compose_design_brief(idea)
    return build_design_record(brief, CATEGORIES[brief.category_key], "test_proj")


def test_record_has_all_design_memory_dimensions():
    rec = _record(IDEA_CRM)
    for field in ("design_id", "category", "layout", "motion", "typography",
                  "component_mix", "density", "navigation", "hero_style",
                  "palette", "interaction_style", "experience_flow", "idea_hash"):
        assert rec.get(field) not in (None, ""), f"missing {field}"
    assert isinstance(rec["component_mix"], list)


def test_similarity_extremes():
    rec = _record(IDEA_CRM)
    assert similarity(rec, dict(rec)) >= 0.99
    other = _record(IDEA_TRAVEL)
    assert similarity(rec, other) < SIMILARITY_THRESHOLD, (
        f"CRM vs travel scored {similarity(rec, other)}"
    )
    # Pre-V19 record (only category/style) must score low, not crash.
    legacy = {"category": rec["category"], "style": rec["style"]}
    assert similarity(rec, legacy) <= 0.35


def test_same_idea_records_are_excluded():
    rec = _record(IDEA_CRM)
    score, nearest = most_similar_recent(rec, [dict(rec), dict(rec)])
    assert score == 0.0 and nearest is None, "same-idea history must be ignored"


def test_divergence_fires_for_near_duplicate_and_not_for_distinct():
    original = dfp._LOG_PATH
    try:
        # Seed memory with IDEA_CRM's record; IDEA_CRM_2 detects the same
        # category — if it also lands on the same style, similarity is high.
        seeded = _record(IDEA_CRM)
        _with_store([seeded])
        rec2 = _record(IDEA_CRM_2)
        expected_fire = similarity(rec2, seeded) >= SIMILARITY_THRESHOLD

        directive = divergence_directive(IDEA_CRM_2, CATEGORIES["crm"])
        if expected_fire:
            assert "NEW DIRECTION REQUIRED" in directive
            assert directive.count("\n  1. ") == 1 and "\n  2. " in directive
        else:
            assert directive == ""

        # A travel app against CRM-only memory must never fire.
        assert divergence_directive(IDEA_TRAVEL, CATEGORIES["travel"]) == ""

        # Determinism: same idea + same memory state -> same directive.
        assert directive == divergence_directive(IDEA_CRM_2, CATEGORIES["crm"])
    finally:
        _restore(original)


def test_divergence_never_fires_for_same_idea_rerun():
    """Check & Fix contract: an app re-run compares only against OTHER apps."""
    original = dfp._LOG_PATH
    try:
        _with_store([_record(IDEA_CRM)] * 5)
        assert divergence_directive(IDEA_CRM, CATEGORIES["crm"]) == ""
    finally:
        _restore(original)


def test_injection_includes_new_direction_when_memory_is_saturated():
    original = dfp._LOG_PATH
    try:
        seeded = _record(IDEA_CRM)
        rec2 = _record(IDEA_CRM_2)
        if similarity(rec2, seeded) >= SIMILARITY_THRESHOLD:
            _with_store([seeded])
            injection = build_design_system_injection(IDEA_CRM_2)
            assert "NEW DIRECTION REQUIRED" in injection
        _with_store([])
        injection_clean = build_design_system_injection(IDEA_CRM_2)
        assert "NEW DIRECTION" not in injection_clean
    finally:
        _restore(original)


def test_directive_never_raises_on_corrupt_store():
    original = dfp._LOG_PATH
    try:
        with open(_TEST_STORE, "w", encoding="utf-8") as f:
            f.write("{not json")
        dfp._LOG_PATH = _TEST_STORE
        assert divergence_directive(IDEA_CRM, CATEGORIES["crm"]) == ""
    finally:
        _restore(original)


if __name__ == "__main__":
    failures = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"  PASS {name}")
            except AssertionError as e:
                failures += 1
                print(f"  FAIL {name}: {e}")
    print(f"\n{'ALL PASS' if failures == 0 else f'{failures} FAILURE(S)'}")
    sys.exit(1 if failures else 0)
