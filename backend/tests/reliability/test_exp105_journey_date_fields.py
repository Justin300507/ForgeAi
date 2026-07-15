"""
Exp105: date-typed required fields in the CRUD journey's Create step
(app/runtime/user_journey_runner.py).

Reproduces the live expense_tracker incident (2026-07-15, Railway log):
the app's /openapi.json 500'd (unrelated Pydantic forward-ref bug), so
schema introspection was blind; the required `date` field was discovered
only via the 422 "missing" branch, which filled it with "journey-test";
the second 422 (`date_from_datetime_parsing`) had no coercion entry and
the single-shot retry gave up — Create never captured an id and
Edit/Delete/Verify/persistence all cascade-failed (Runtime 20/100).

Three fixes under test:
1. the missing-field filler now uses the shared name heuristics
   (_guess_field_value), so a field named `date` gets a real date;
2. _TYPE_COERCIONS knows the pydantic-v2 date/datetime/time error types;
3. the targeted 422 retry runs up to two rounds, because round 1 often
   only REVEALS the next constraint (fill missing -> now type is wrong).

Same genuine-HTTP methodology as test_role_aware_journey.py: a real
stdlib HTTP server, real requests, no source-text assertions.

Run directly: python tests/reliability/test_exp105_journey_date_fields.py
"""
import json
import os
import re
import shutil
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from app.runtime.user_journey_runner import (
    run_user_journey,
    _guess_field_value,
    _TYPE_COERCIONS,
)

_ARCH = {
    "api_endpoints": [
        {"method": "POST", "path": "/auth/register"},
        {"method": "POST", "path": "/auth/login"},
        {"method": "GET", "path": "/expenses"},
        {"method": "POST", "path": "/expenses"},
        {"method": "GET", "path": "/expenses/{id}"},
        {"method": "PUT", "path": "/expenses/{id}"},
        {"method": "DELETE", "path": "/expenses/{id}"},
    ]
}

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}")


class _FakeExpenseAppHandler(BaseHTTPRequestHandler):
    """Required-field + date-validation shapes are class-configurable so
    each test arms exactly the incident variant it needs."""
    items = {}
    # field -> "date" (must look like an ISO date) or "any"
    required_fields = {"date": "date", "amount": "any", "category": "any"}
    # a field that 422s with an unfixable error type no matter what
    poison_field = None

    def log_message(self, *a):
        pass

    def _send(self, status, body):
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _body(self):
        length = int(self.headers.get("Content-Length", 0))
        return json.loads(self.rfile.read(length) or b"{}")

    def do_GET(self):
        if self.path == "/docs":
            self._send(200, {})
        elif self.path == "/openapi.json":
            # The live incident's exact precondition: schema introspection
            # is blind because openapi generation itself crashes.
            self._send(500, {"detail": "PydanticUserError: not fully defined"})
        elif self.path == "/expenses":
            self._send(200, list(type(self).items.values()))
        elif self.path.startswith("/expenses/"):
            item = type(self).items.get(self.path.split("/")[-1])
            self._send(200, item) if item else self._send(404, {"detail": "not found"})
        else:
            self._send(404, {})

    def do_POST(self):
        body = self._body()
        if self.path in ("/auth/register", "/auth/signup"):
            self._send(200, {"access_token": body.get("email", "t")})
        elif self.path == "/auth/login":
            self._send(200, {"access_token": body.get("email", "t")})
        elif self.path == "/expenses":
            errors = []
            for field, kind in type(self).required_fields.items():
                if field not in body:
                    errors.append({"type": "missing", "loc": ["body", field],
                                   "msg": "Field required", "input": None})
                elif kind == "date" and not (
                    isinstance(body[field], str) and _DATE_RE.match(body[field])
                ):
                    # pydantic v2's exact error type from the live log
                    errors.append({
                        "type": "date_from_datetime_parsing",
                        "loc": ["body", field],
                        "msg": "Input should be a valid date or datetime, "
                               "invalid character in year",
                        "input": body[field],
                    })
            if type(self).poison_field is not None:
                errors.append({"type": "value_error",
                               "loc": ["body", type(self).poison_field],
                               "msg": "Value error, always rejected",
                               "input": body.get(type(self).poison_field)})
            if errors:
                self._send(422, {"detail": errors})
                return
            item_id = str(len(type(self).items) + 1)
            item = {"id": item_id, **body}
            type(self).items[item_id] = item
            self._send(201, item)
        else:
            self._send(404, {})

    def do_PUT(self):
        item_id = self.path.split("/")[-1]
        if item_id in type(self).items:
            self._send(200, type(self).items[item_id])
        else:
            self._send(404, {})

    def do_DELETE(self):
        type(self).items.pop(self.path.split("/")[-1], None)
        self._send(204, {})


def _run_journey(required_fields, poison_field=None):
    _FakeExpenseAppHandler.items = {}
    _FakeExpenseAppHandler.required_fields = required_fields
    _FakeExpenseAppHandler.poison_field = poison_field
    server = HTTPServer(("127.0.0.1", 0), _FakeExpenseAppHandler)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    project_root = Path(tempfile.mkdtemp(prefix="datejourney_"))
    try:
        return run_user_journey(str(project_root), _ARCH, backend_port=port)
    finally:
        server.shutdown()
        shutil.rmtree(project_root, ignore_errors=True)


# ── The live incident, end to end ────────────────────────────────────────

def test_missing_date_field_is_filled_with_a_real_date():
    """expense_tracker's exact shape: required `date` (date-typed),
    `amount`, `category`, discovered only via the 422 missing branch."""
    result = _run_journey({"date": "date", "amount": "any", "category": "any"})
    by_name = {s.name: s for s in result.steps}
    create = by_name["Create entity"]
    assert create.passed and "422-fixed" in create.detail, create.detail
    assert "id=" in create.detail
    assert by_name["Edit entity"].passed, by_name["Edit entity"].detail
    assert by_name["Delete entity"].passed


def test_second_round_coerces_date_rejection_on_unheuristic_name():
    """A required date-typed field whose NAME gives no hint (`start`):
    round 1 fills "journey-test" (missing), the server answers
    date_from_datetime_parsing, round 2's coercion substitutes a valid
    date. Exercises both the new coercion entries and the second round —
    the pre-Exp105 single-shot retry dead-ended exactly here."""
    result = _run_journey({"start": "date"})
    by_name = {s.name: s for s in result.steps}
    create = by_name["Create entity"]
    assert create.passed and "422-fixed" in create.detail, create.detail
    assert "id=" in create.detail


def test_unfixable_422_still_soft_passes_and_terminates():
    """A field that 422s with an uncoercible error type no matter what:
    the bounded loop must give up after its rounds and preserve the
    pre-Exp105 outcome — soft-pass with the real detail surfaced, no id
    captured, downstream steps failing honestly."""
    result = _run_journey({"weird": "any"}, poison_field="weird")
    by_name = {s.name: s for s in result.steps}
    create = by_name["Create entity"]
    assert create.passed  # soft-pass: server alive, schema mismatch surfaced
    assert "schema mismatch" in create.detail
    assert "id=" not in create.detail
    assert not by_name["Edit entity"].passed


# ── Unit guards on the shared pieces ─────────────────────────────────────

def test_guess_field_value_date_names():
    for name in ("date", "due_date", "start_date", "deadline", "expires"):
        v = _guess_field_value(name)
        assert isinstance(v, str) and _DATE_RE.match(v), (name, v)


def test_guess_field_value_defaults_dict_wins():
    assert _guess_field_value("status", {"status": "active"}) == "active"


def test_guess_field_value_fallback_unchanged():
    assert _guess_field_value("something_opaque") == "journey-test"


def test_type_coercions_cover_pydantic_date_family():
    for t in ("date_type", "date_parsing", "date_from_datetime_parsing",
              "datetime_type", "datetime_parsing", "datetime_from_date_parsing",
              "time_type", "time_parsing"):
        assert t in _TYPE_COERCIONS, t
        assert isinstance(_TYPE_COERCIONS[t]("journey-test"), str)
    assert _DATE_RE.match(_TYPE_COERCIONS["date_from_datetime_parsing"]("x"))


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
