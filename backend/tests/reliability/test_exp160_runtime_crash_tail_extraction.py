"""
Experiment 160: regression test for a stalled repair loop traced to a live
CRM generation (2026-08-01) that stuck oscillating at score 41.9 across 3
fix attempts, always re-patching unrelated files (app/schemas/stats.py,
app/routes/item_routes.py) while the actual startup crash -- shown in the
dashboard log only as "Runtime crash: [Unknown] Traceback (most recent call
last):\\n  File \"<frozen runp" -- was never targeted.

Root cause: app/runtime/error_parser.py's parse_runtime_error() falls back
to type="Unknown" with no "message" key when no specific pattern matches.
app/verification/engine.py's _run_runtime_validation() then built the
diagnostic message as `stderr[:400]` -- the HEAD of the captured output,
which for any crash routed through uvicorn/click/asyncio's own startup
frames is pure bootstrap noise; the real exception (what a Python traceback
always puts last) never made it into the message, into the FixCache hash
key, or into the fix LLM's prompt. A separate branch a few lines down
(guarded by `has_specific`, i.e. only for a RECOGNIZED error type) already
correctly scanned backward from the end of stderr for the exception line --
but "Unknown" is exactly the type that never reaches that branch, so the
one case that most needed tail extraction never got it.

Run directly: python tests/reliability/test_exp160_runtime_crash_tail_extraction.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from app.verification.engine import _run_runtime_validation, _tail_error_line
from app.core.context import GenerationContext


# A realistic uvicorn/click/asyncio startup traceback (>400 chars on its
# own) with the actual failure -- an IntegrityError from a Create schema
# missing a required FK field, the exact class of bug the CRM generation's
# own validation loop had already flagged for contact_id/deal_id -- at the
# very end, the way Python traceback formatting always places it.
_LONG_UNRECOGNIZED_STDERR = (
    "Traceback (most recent call last):\n"
    '  File "<frozen runpy>", line 198, in _run_module_as_main\n'
    '  File "<frozen runpy>", line 88, in _run_code\n'
    '  File "/usr/local/lib/python3.11/site-packages/uvicorn/__main__.py", line 4, in <module>\n'
    "    uvicorn.main()\n"
    '  File "/usr/local/lib/python3.11/site-packages/click/core.py", line 1569, in __call__\n'
    "    return self.main(*args, **kwargs)\n"
    '  File "/usr/local/lib/python3.11/site-packages/click/core.py", line 1490, in main\n'
    "    rv = self.invoke(ctx)\n"
    '  File "/usr/local/lib/python3.11/site-packages/click/core.py", line 1353, in invoke\n'
    "    return ctx.invoke(self.callback, **ctx.params)\n"
    '  File "/usr/local/lib/python3.11/site-packages/click/core.py", line 907, in invoke\n'
    "    return callback(*args, **kwargs)\n"
    '  File "/usr/local/lib/python3.11/site-packages/uvicorn/main.py", line 412, in main\n'
    "    run(\n"
    '  File "/usr/local/lib/python3.11/site-packages/uvicorn/main.py", line 579, in run\n'
    "    server.run()\n"
    '  File "app/routes/activity_routes.py", line 41, in create_activity\n'
    "    db.commit()\n"
    "sqlalchemy.exc.IntegrityError: NOT NULL constraint failed: activities.contact_id\n"
)

assert len(_LONG_UNRECOGNIZED_STDERR) > 400, "fixture must exceed the old head-slice length to be a real test"


def _mk_ctx() -> GenerationContext:
    return GenerationContext(
        job_id="test-job",
        idea="test idea",
        project_path=Path(tempfile.gettempdir()),
        project_name="test_project",
    )


def test_unknown_type_message_uses_tail_not_head():
    fake_result = {
        "success": False,
        "stderr": _LONG_UNRECOGNIZED_STDERR,
        "parsed_error": {"type": "Unknown", "raw_error": _LONG_UNRECOGNIZED_STDERR[:1000], "error_file": None},
        "journey": {},
    }
    with patch("app.runtime.backend_runner.run_backend_validation", return_value=fake_result):
        result = _run_runtime_validation(_mk_ctx())

    messages = [d.message for d in result.diagnostics]
    assert any("IntegrityError" in m and "activities.contact_id" in m for m in messages), (
        f"expected the tail exception in the diagnostic message, got: {messages}"
    )
    assert not any("frozen runpy" in m for m in messages), (
        "diagnostic message still carries head-of-traceback bootstrap noise instead of the real exception"
    )


def test_tail_error_line_finds_last_matching_line():
    assert _tail_error_line(_LONG_UNRECOGNIZED_STDERR) == (
        "sqlalchemy.exc.IntegrityError: NOT NULL constraint failed: activities.contact_id"
    )


def test_tail_error_line_empty_on_no_match():
    assert _tail_error_line("just some plain uvicorn INFO log lines\nnothing exceptional here\n") == ""


def test_tail_error_line_empty_on_empty_input():
    assert _tail_error_line("") == ""
    assert _tail_error_line(None) == ""


def test_recognized_type_still_uses_tail_as_before():
    # Guards against regressing the pre-existing has_specific branch while
    # refactoring both call sites onto the shared _tail_error_line helper.
    stderr = _LONG_UNRECOGNIZED_STDERR
    fake_result = {
        "success": False,
        "stderr": stderr,
        "parsed_error": {
            "type": "SQLAlchemyError",
            "exc_class": "IntegrityError",
            "error_file": "app/routes/activity_routes.py",
            "hint": "some hint",
        },
        "journey": {},
    }
    with patch("app.runtime.backend_runner.run_backend_validation", return_value=fake_result):
        result = _run_runtime_validation(_mk_ctx())
    messages = [d.message for d in result.diagnostics]
    assert any("IntegrityError" in m and "activities.contact_id" in m for m in messages)


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
