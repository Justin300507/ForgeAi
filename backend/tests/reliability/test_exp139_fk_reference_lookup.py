"""
Exp139: independent-lookup foreign-key fields in the CRUD journey's Create
step (app/runtime/user_journey_runner.py).

Reproduces the live todo canary incident (2026-07-22): `POST /tasks`
required `priority_id: int`, a real foreign key into a `priorities` table
that only `POST /seed` populates. The journey runner's field guesser
(`_guess_field_value`) blindly filled ANY `_id`-suffixed field with the
literal `1`, with no guarantee the seeded table's first row actually got
id 1 -- when it didn't, Create 500'd via IntegrityError, and
Edit/Delete/Verify/persistence all cascade-failed with "no entity_id
captured" (Forge Score 96.5 -> 76.5 across two of three runs that day).

Fix under test: `_resolve_fk_reference_id` looks up a REAL existing row id
from the architecture's matching GET collection endpoint (or a plain
pluralized guess) before falling back to the old blind `1`. Applied at
both call sites that build the Create payload: schema-driven enrichment
and the 422 targeted-retry filler.

Self-referential FKs already in `_FIELD_DEFAULTS` (user_id, owner_id,
task_id, ...) are deliberately NOT touched -- they refer to entities this
same journey just created, where `1` is already a correct guess, and must
keep working with zero extra HTTP calls.

Same genuine-HTTP methodology as test_role_aware_journey.py /
test_exp105_journey_date_fields.py: a real stdlib HTTP server, real
requests, no source-text assertions.

Run directly: python tests/reliability/test_exp139_fk_reference_lookup.py
"""
import json
import os
import shutil
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from app.runtime.user_journey_runner import (
    run_user_journey,
    _resolve_fk_reference_id,
)

_ARCH = {
    "api_endpoints": [
        {"method": "POST", "path": "/auth/register"},
        {"method": "POST", "path": "/auth/login"},
        {"method": "GET", "path": "/priorities"},
        {"method": "GET", "path": "/tasks"},
        {"method": "POST", "path": "/tasks"},
        {"method": "GET", "path": "/tasks/{id}"},
        {"method": "PUT", "path": "/tasks/{id}"},
        {"method": "DELETE", "path": "/tasks/{id}"},
    ]
}

# The exact live-incident shape: seeded priorities start at id 7, not 1.
_SEEDED_PRIORITIES = [{"id": "7", "name": "medium"}, {"id": "8", "name": "high"}]


class _FakeTodoAppHandler(BaseHTTPRequestHandler):
    items = {}
    seeded = False
    priorities = []  # class-configurable per test

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
            self._send(200, {
                "paths": {
                    "/tasks": {
                        "post": {
                            "requestBody": {"content": {"application/json": {"schema": {
                                "properties": {
                                    "title": {"type": "string"},
                                    "priority_id": {"type": "integer"},
                                },
                                "required": ["title", "priority_id"],
                            }}}}
                        }
                    }
                }
            })
        elif self.path == "/priorities":
            self._send(200, type(self).priorities)
        elif self.path == "/tasks":
            self._send(200, list(type(self).items.values()))
        elif self.path.startswith("/tasks/"):
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
        elif self.path == "/seed":
            type(self).seeded = True
            self._send(200, {"summary": {"priorities": len(type(self).priorities)}})
        elif self.path == "/tasks":
            valid_ids = {p["id"] for p in type(self).priorities}
            sent = str(body.get("priority_id"))
            if "priority_id" not in body:
                self._send(422, {"detail": [{"type": "missing", "loc": ["body", "priority_id"],
                                              "msg": "Field required", "input": None}]})
                return
            if sent not in valid_ids:
                # The exact live failure mode: a real IntegrityError,
                # surfaced as 500 (not a coercible 422) since the FK
                # constraint is a database-level rejection, not a
                # pydantic validation error.
                self._send(500, {"detail": "IntegrityError: FOREIGN KEY constraint failed"})
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


def _run_journey(priorities):
    _FakeTodoAppHandler.items = {}
    _FakeTodoAppHandler.seeded = False
    _FakeTodoAppHandler.priorities = priorities
    server = ThreadingHTTPServer(("127.0.0.1", 0), _FakeTodoAppHandler)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    project_root = Path(tempfile.mkdtemp(prefix="fkjourney_"))
    try:
        return run_user_journey(str(project_root), _ARCH, backend_port=port)
    finally:
        server.shutdown()
        shutil.rmtree(project_root, ignore_errors=True)


# ── The live incident, end to end ────────────────────────────────────────

def test_seeded_priority_not_at_id_1_no_longer_500s():
    """The exact live shape: priorities seeded starting at id 7. The old
    blind `1` guess 500'd via IntegrityError every time; the fix must look
    up the real id and succeed."""
    result = _run_journey(_SEEDED_PRIORITIES)
    by_name = {s.name: s for s in result.steps}
    create = by_name["Create entity"]
    assert create.passed, create.detail
    assert "id=" in create.detail
    assert "(server error)" not in create.detail
    assert by_name["Edit entity"].passed, by_name["Edit entity"].detail
    assert by_name["Delete entity"].passed
    assert by_name["Verify deletion"].passed


def test_no_seeded_rows_falls_back_without_crashing():
    """An empty lookup table (seed produced nothing, or wasn't called):
    resolution finds no real id and falls back to the old blind guess
    rather than raising -- Create still gets attempted, just soft-fails
    honestly like before this fix, not a new crash."""
    result = _run_journey([])
    by_name = {s.name: s for s in result.steps}
    create = by_name["Create entity"]
    assert create.passed or "(server error)" in create.detail  # never an exception


# ── Unit guards on the resolver itself ───────────────────────────────────

def test_resolve_fk_reference_id_finds_real_row():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _FakeTodoAppHandler)
    port = server.server_address[1]
    _FakeTodoAppHandler.priorities = _SEEDED_PRIORITIES
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        import requests
        resolved = _resolve_fk_reference_id(
            requests, f"http://127.0.0.1:{port}", {}, _ARCH, "priority_id",
        )
        assert resolved == "7", resolved
    finally:
        server.shutdown()


def test_resolve_fk_reference_id_returns_none_when_nothing_found():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _FakeTodoAppHandler)
    port = server.server_address[1]
    _FakeTodoAppHandler.priorities = []
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        import requests
        resolved = _resolve_fk_reference_id(
            requests, f"http://127.0.0.1:{port}", {}, _ARCH, "priority_id",
        )
        assert resolved is None
    finally:
        server.shutdown()


def test_resolve_fk_reference_id_empty_stem_returns_none():
    import requests
    assert _resolve_fk_reference_id(requests, "http://127.0.0.1:1", {}, _ARCH, "_id") is None


def test_self_referential_fk_still_resolves_without_a_matching_collection():
    """A self-referential-style FK with no matching GET collection in the
    architecture (owner_id has no /owners endpoint here) must still fall
    back cleanly to the old blind guess rather than hang or crash --
    covers the exclusion path behaviorally, not by reading source text."""
    import requests
    resolved = _resolve_fk_reference_id(
        requests, "http://127.0.0.1:1", {}, {"api_endpoints": []}, "owner_id",
    )
    assert resolved is None


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
