"""
Verifies scripts/observatory.py's render_html: produces well-formed HTML
(via stdlib HTMLParser tag-balance check) for empty, populated, and
divergent-failure telemetry states -- the states compute_observatory's
own tests already cover data-correctness for; this checks the HTML layer
doesn't break on any of them. Also covers the Reliability Timeline and
Experiment Attribution sections added after the user's data-quality
feedback (confidence labeling, before/after canary transitions).

Run directly: python tests/reliability/test_observatory_render.py
"""
import os
import sys
from html.parser import HTMLParser

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "scripts"))

from app.memory.reliability_metrics import (
    compute_observatory, compute_reliability_metrics,
    compute_reliability_timeline, compute_experiment_attribution,
)
from observatory import render_html


class _TagBalanceChecker(HTMLParser):
    VOID = {"br", "img", "input", "hr", "meta", "link"}

    def __init__(self):
        super().__init__()
        self.stack = []
        self.mismatches = []

    def handle_starttag(self, tag, attrs):
        if tag not in self.VOID:
            self.stack.append(tag)

    def handle_endtag(self, tag):
        if self.stack and self.stack[-1] == tag:
            self.stack.pop()
        else:
            self.mismatches.append(tag)


def _assert_well_formed(html: str):
    checker = _TagBalanceChecker()
    checker.feed(html)
    assert not checker.mismatches, f"tag mismatches: {checker.mismatches}"
    assert not checker.stack, f"unclosed tags at EOF: {checker.stack}"


def _render(entries, canary_runs, window=30):
    obs = compute_observatory(entries, canary_runs, window=window)
    rel = compute_reliability_metrics(entries, canary_runs, window=window)
    timeline = compute_reliability_timeline(canary_runs)
    attribution = compute_experiment_attribution(canary_runs)
    return render_html(obs, rel, len(entries), len(canary_runs), timeline, attribution)


def test_renders_well_formed_html_for_empty_telemetry():
    html = _render([], [])
    _assert_well_formed(html)
    assert "ForgeAI Observatory" in html


def test_renders_well_formed_html_for_populated_telemetry():
    entries = [{"succeeded": True, "fix_count": 0, "final_score": 90,
                "dominant_errors": [], "regression_count": 0,
                "prevention_counts": {"stage.import_closure": 5, "_patch_missing_create_update_fields": 2}}
               for _ in range(10)]
    entries += [{"succeeded": False, "fix_count": 2, "final_score": 40,
                 "dominant_errors": ["[JourneyCRUDFailure] x"], "regression_count": 1}
                for _ in range(5)]
    canary_runs = [{"label": "x", "timestamp": "2026-07-01T00:00:00",
                     "results": [{"forge_score": 90, "crashed": False}]}]
    html = _render(entries, canary_runs)
    _assert_well_formed(html)
    assert "Import validation" in html or "Schema validator" in html


def test_renders_well_formed_html_when_failure_shifted():
    historically = [{"succeeded": False, "dominant_errors": ["[JourneyCRUDFailure] x"],
                      "final_score": 40, "fix_count": 2} for _ in range(20)]
    now = [{"succeeded": False, "dominant_errors": ["[SQLAlchemyError] y"],
            "final_score": 40, "fix_count": 2} for _ in range(5)]
    html = _render(historically + now, [], window=5)
    _assert_well_formed(html)
    assert "SQLAlchemyError" in html
    assert "JourneyCRUDFailure" in html


def test_renders_timeline_and_attribution_with_multiple_canary_runs():
    canary_runs = [
        {"label": "m0-baseline", "timestamp": "2026-07-01T00:00:00",
         "results": [{"forge_score": 46, "crashed": False}] * 3},
        {"label": "m1-fix", "timestamp": "2026-07-05T00:00:00",
         "results": [{"forge_score": 62, "crashed": False}] * 3},
    ]
    html = _render([], canary_runs)
    _assert_well_formed(html)
    assert "Reliability Timeline" in html
    assert "Experiment Attribution" in html
    assert "m0-baseline" in html
    assert "m1-fix" in html
    assert "Confidence: Low" in html  # north-star confidence, 0 generations


def test_renders_confidence_pill_on_north_star():
    entries = [{"succeeded": True, "fix_count": 0, "final_score": 90} for _ in range(35)]
    html = _render(entries, [], window=35)
    _assert_well_formed(html)
    assert "Confidence: High" in html


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
