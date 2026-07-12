"""
Experiment 078 (Restore Runtime Endpoint Preservation): regression tests for
the two-part fix in app/repair/orchestrator.py identified by Exp077's
investigation.

Root cause (Exp077, confirmed live in `forge_blog_cms`'s own generation
log): the endpoint-preservation mechanism was designed to stop runtime-stage
full-file rewrites from silently dropping endpoints the static-validation
loop had already recovered, but it never actually fired:

  1. `_required_endpoints_for_files()` compared the architecture's raw
     `file` field (backslash-separated on Windows, e.g.
     `'app\\routes\\post_routes.py'`) against forward-slash runtime
     diagnostic paths with zero normalization -- `ep.get("file") in files`
     never matched, for any project, ever.
  2. `_regenerate_module()`'s backend path called `generate_architecture_fix()`
     without ever constructing/passing `required_endpoints=`, even though
     that function already accepts and uses it.

The reconstruction fixture below is the exact endpoint set from
`generated_projects/forge_blog_cms/metadata.json`'s
`architecture.api_endpoints` for `post_routes.py` -- the confirmed-live
failure Exp077 traced end-to-end (8 endpoints recovered by static
validation, then dropped again by a runtime-stage rewrite for unrelated
diagnostics).

Run directly: python tests/reliability/test_exp078_endpoint_preservation.py
"""
import os
import sys
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from app.repair.orchestrator import (
    _regenerate_module,
    _relevant_endpoints_for_files,
    _required_endpoints_for_files,
    _required_endpoints_map_for_files,
)
from app.core.context import GenerationContext, DiagnosticGroup, FixStrategy
from app.retry.manager import StrategyConfig


# The real, confirmed-live fixture: forge_blog_cms's planned post_routes.py
# endpoints, architecture-side paths exactly as generated (backslash-joined).
_BLOG_CMS_ARCHITECTURE = {
    "api_endpoints": [
        {"file": "app\\routes\\post_routes.py", "method": "GET", "path": "/posts"},
        {"file": "app\\routes\\post_routes.py", "method": "GET", "path": "/posts/{post_id}"},
        {"file": "app\\routes\\post_routes.py", "method": "GET", "path": "/authors/{author_id}/posts"},
        {"file": "app\\routes\\post_routes.py", "method": "POST", "path": "/posts"},
        {"file": "app\\routes\\post_routes.py", "method": "PUT", "path": "/posts/{post_id}"},
        {"file": "app\\routes\\post_routes.py", "method": "PATCH", "path": "/posts/{post_id}/publish"},
        {"file": "app\\routes\\post_routes.py", "method": "PATCH", "path": "/posts/{post_id}/unpublish"},
        {"file": "app\\routes\\post_routes.py", "method": "DELETE", "path": "/posts/{post_id}"},
    ]
}

# Runtime diagnostics/affected_files always use forward slashes (per
# Diagnostic.file_path construction, e.g. undefined_symbol_validator.py).
_RUNTIME_AFFECTED_FILE = "app/routes/post_routes.py"


def _ctx():
    ctx = GenerationContext(job_id="t", idea="t", project_path=None, project_name="t")
    ctx.architecture = _BLOG_CMS_ARCHITECTURE
    return ctx


def _group():
    return DiagnosticGroup(
        group_id="g1", root_cause="test", diagnostics=[],
        affected_files=[_RUNTIME_AFFECTED_FILE],
        suggested_strategy=FixStrategy.REGENERATE_MODULE, priority=1,
    )


def _cfg():
    return StrategyConfig(
        attempt=1, strategy=FixStrategy.REGENERATE_MODULE,
        provider="none", model_hint="none",
    )


# ---------------------------------------------------------------------------
# Part 1: path normalization -- the matching itself must now succeed
# ---------------------------------------------------------------------------

def test_relevant_endpoints_match_despite_backslash_architecture_path():
    relevant = _relevant_endpoints_for_files(_ctx(), [_RUNTIME_AFFECTED_FILE])
    assert len(relevant) == 8, (
        f"expected all 8 forge_blog_cms post_routes.py endpoints to match, got {len(relevant)} "
        "-- this is exactly the Exp077-confirmed bug (backslash vs forward-slash mismatch)"
    )


def test_relevant_endpoints_no_match_without_normalization_would_have_failed():
    # Characterizes the pre-fix behavior directly: a naive `in` check against
    # the raw (unnormalized) architecture file field never matches.
    raw_file_field = _BLOG_CMS_ARCHITECTURE["api_endpoints"][0]["file"]
    assert raw_file_field != _RUNTIME_AFFECTED_FILE
    assert raw_file_field.replace("\\", "/") == _RUNTIME_AFFECTED_FILE


def test_required_endpoints_prompt_block_lists_all_endpoints():
    block = _required_endpoints_for_files(_ctx(), [_RUNTIME_AFFECTED_FILE])
    assert "REQUIRED ENDPOINTS" in block
    for method, path in [
        ("GET", "/posts"), ("PUT", "/posts/{post_id}"),
        ("PATCH", "/posts/{post_id}/publish"), ("DELETE", "/posts/{post_id}"),
    ]:
        assert f"{method} {path}" in block


def test_required_endpoints_map_shaped_for_generate_architecture_fix():
    mapping = _required_endpoints_map_for_files(_ctx(), [_RUNTIME_AFFECTED_FILE])
    assert set(mapping.keys()) == {_RUNTIME_AFFECTED_FILE}
    assert len(mapping[_RUNTIME_AFFECTED_FILE]) == 8
    assert "PUT /posts/{post_id}" in mapping[_RUNTIME_AFFECTED_FILE]


def test_no_endpoints_returned_for_unrelated_file():
    mapping = _required_endpoints_map_for_files(_ctx(), ["app/routes/comment_routes.py"])
    assert mapping == {}


# ---------------------------------------------------------------------------
# Part 2: wiring -- _regenerate_module must now actually pass the map through
# ---------------------------------------------------------------------------

def test_regenerate_module_passes_required_endpoints_to_generate_architecture_fix(tmp_path=None):
    import tempfile
    from pathlib import Path

    captured = {}

    def fake_generate_architecture_fix(architecture, messages, provider, required_endpoints=None):
        captured["required_endpoints"] = required_endpoints
        return {"files": [{"path": _RUNTIME_AFFECTED_FILE, "content": "x = 1\n"}]}

    with tempfile.TemporaryDirectory() as td:
        ctx = GenerationContext(job_id="t", idea="t", project_path=Path(td), project_name="t")
        ctx.architecture = _BLOG_CMS_ARCHITECTURE
        with mock.patch(
            "app.services.architecture_fix_service.generate_architecture_fix",
            side_effect=fake_generate_architecture_fix,
        ):
            _regenerate_module(_group(), ctx, _cfg())

    assert "required_endpoints" in captured, (
        "generate_architecture_fix() was never called, or called positionally "
        "without required_endpoints -- activation did not occur"
    )
    assert captured["required_endpoints"] != {}, (
        "this is the Exp077-confirmed second gap: required_endpoints was passed "
        "as an empty dict, i.e. never actually constructed from the architecture"
    )
    assert set(captured["required_endpoints"].keys()) == {_RUNTIME_AFFECTED_FILE}
    assert len(captured["required_endpoints"][_RUNTIME_AFFECTED_FILE]) == 8


def test_regenerate_module_unrelated_repair_still_works_with_no_architecture():
    # Verifies unrelated runtime repairs (no architecture context at all, or
    # a file with no matching endpoints) are unaffected by this change --
    # required_endpoints just comes back {} and the regen proceeds normally.
    import tempfile
    from pathlib import Path

    def fake_generate_architecture_fix(architecture, messages, provider, required_endpoints=None):
        assert required_endpoints == {}
        return {"files": [{"path": "app/routes/other.py", "content": "y = 2\n"}]}

    with tempfile.TemporaryDirectory() as td:
        ctx = GenerationContext(job_id="t", idea="t", project_path=Path(td), project_name="t")
        group = DiagnosticGroup(
            group_id="g2", root_cause="test", diagnostics=[],
            affected_files=["app/routes/other.py"],
            suggested_strategy=FixStrategy.REGENERATE_MODULE, priority=1,
        )
        with mock.patch(
            "app.services.architecture_fix_service.generate_architecture_fix",
            side_effect=fake_generate_architecture_fix,
        ):
            modified, _ = _regenerate_module(group, ctx, _cfg())

    assert modified == ["app/routes/other.py"]


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
