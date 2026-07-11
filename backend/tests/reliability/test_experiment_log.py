"""
Observatory cockpit: experiments.md -> structured entries for the
/observatory endpoint. $0, read-only, no new tracking system.

Run directly: python tests/reliability/test_experiment_log.py
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from app.memory.experiment_log import parse_recent_experiments

SAMPLE_MD = """\
# ForgeAI Experiment Log

Some header prose that isn't an experiment entry.

## Experiment 001 — First fix ($0, no LLM calls)

Root cause was X. Shipped Y.

More detail here that shouldn't appear in the summary.

## Experiment 002 — Second fix

**Bold claim** about `code` with _emphasis_.

Multiple    spaces   collapse.

## Experiment 003 — Third fix, no cost marker in title

This one costs money -- a live canary confirmed it, not $0.
"""


def _write(tmp_path, text):
    p = tmp_path / "experiments.md"
    p.write_text(text, encoding="utf-8")
    return p


def test_parses_all_entries_newest_first(tmp_path):
    p = _write(tmp_path, SAMPLE_MD)
    entries = parse_recent_experiments(p, limit=8)
    assert [e["number"] for e in entries] == ["003", "002", "001"]


def test_summary_is_first_paragraph_only(tmp_path):
    p = _write(tmp_path, SAMPLE_MD)
    entries = parse_recent_experiments(p, limit=8)
    exp001 = next(e for e in entries if e["number"] == "001")
    assert exp001["summary"].startswith("Root cause was X. Shipped Y.")
    assert "More detail here" not in exp001["summary"]


def test_markdown_markers_stripped_and_whitespace_collapsed(tmp_path):
    p = _write(tmp_path, SAMPLE_MD)
    entries = parse_recent_experiments(p, limit=8)
    exp002 = next(e for e in entries if e["number"] == "002")
    assert "*" not in exp002["summary"]
    assert "`" not in exp002["summary"]
    assert "_" not in exp002["summary"]
    assert "  " not in exp002["summary"]


def test_cost_free_detected_from_title_or_body(tmp_path):
    p = _write(tmp_path, SAMPLE_MD)
    entries = {e["number"]: e for e in parse_recent_experiments(p, limit=8)}
    assert entries["001"]["cost_free"] is True   # "$0" in title
    assert entries["003"]["cost_free"] is False  # no $0 marker anywhere relevant


def test_limit_respected(tmp_path):
    p = _write(tmp_path, SAMPLE_MD)
    entries = parse_recent_experiments(p, limit=2)
    assert len(entries) == 2
    assert [e["number"] for e in entries] == ["003", "002"]


def test_missing_file_returns_empty_list(tmp_path):
    entries = parse_recent_experiments(tmp_path / "does_not_exist.md", limit=8)
    assert entries == []


def test_long_summary_truncated_with_ellipsis(tmp_path):
    long_para = "x" * 500
    p = _write(tmp_path, f"## Experiment 999 — Long\n\n{long_para}\n")
    entries = parse_recent_experiments(p, limit=8)
    assert len(entries[0]["summary"]) <= 321
    assert entries[0]["summary"].endswith("…")


if __name__ == "__main__":
    import tempfile
    import traceback

    tests = [obj for name, obj in list(globals().items()) if name.startswith("test_")]
    failed = 0
    for t in tests:
        try:
            with tempfile.TemporaryDirectory() as td:
                t(Path(td))
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
