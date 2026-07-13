"""
Verifies Experiment 095: the CRUD journey runner uses whichever HTTP
method (PUT or PATCH) the architecture actually declares for the
detected entity's update route, instead of a hardcoded PUT.

Root cause (Experiment 094): the architect is legitimately allowed to
choose PATCH for an update endpoint; a hardcoded `requests.put()` in
`do_edit()` then 405s against otherwise-correct, spec-compliant
generated code.

Run directly: python tests/reliability/test_exp095_journey_method_alignment.py
"""
import json
import os
import shutil
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from app.runtime.user_journey_runner import (
    _detect_crud_entity, _detect_update_method, run_user_journey,
)


# ── _detect_update_method: unit-level ──────────────────────────────────

def test_detect_update_method_returns_put_when_declared():
    arch = {"api_endpoints": [
        {"method": "GET", "path": "/products/{id}"},
        {"method": "PUT", "path": "/products/{id}"},
    ]}
    assert _detect_update_method(arch, "", "products") == "PUT"


def test_detect_update_method_returns_patch_when_only_patch_declared():
    # Exact shape confirmed live in sports_league_manager (Experiment 094):
    # no PUT declared anywhere for this resource, only PATCH.
    arch = {"api_endpoints": [
        {"method": "GET", "path": "/leagues/{id}"},
        {"method": "PATCH", "path": "/leagues/{id}"},
    ]}
    assert _detect_update_method(arch, "", "leagues") == "PATCH"


def test_detect_update_method_ignores_patch_on_action_subpaths():
    # forge_blog_cms shape: PUT is the real update verb for /posts/{id},
    # PATCH is used only for unrelated /posts/{id}/publish|unpublish
    # action endpoints -- must not be confused with the canonical update.
    arch = {"api_endpoints": [
        {"method": "GET", "path": "/posts/{id}"},
        {"method": "PUT", "path": "/posts/{id}"},
        {"method": "PATCH", "path": "/posts/{id}/publish"},
        {"method": "PATCH", "path": "/posts/{id}/unpublish"},
    ]}
    assert _detect_update_method(arch, "", "posts") == "PUT"


def test_detect_update_method_defaults_to_put_when_neither_declared():
    # volunteer_management_system shape (Experiment 094): the selected
    # entity has no update endpoint at all -- must preserve prior
    # behavior (still try PUT), not invent a new failure mode.
    arch = {"api_endpoints": [
        {"method": "GET", "path": "/events/{id}"},
        {"method": "POST", "path": "/events/{id}/publish"},
    ]}
    assert _detect_update_method(arch, "", "events") == "PUT"


def test_detect_update_method_respects_api_prefix():
    arch = {"api_endpoints": [
        {"method": "GET", "path": "/api/v1/tasks/{id}"},
        {"method": "PATCH", "path": "/api/v1/tasks/{id}"},
    ]}
    assert _detect_update_method(arch, "/api/v1", "tasks") == "PATCH"


# ── _detect_crud_entity: now returns (resource, method) ────────────────

def test_detect_crud_entity_returns_tuple_with_put():
    arch = {"api_endpoints": [
        {"method": "GET", "path": "/products"},
        {"method": "POST", "path": "/products"},
        {"method": "GET", "path": "/products/{id}"},
        {"method": "PUT", "path": "/products/{id}"},
        {"method": "DELETE", "path": "/products/{id}"},
    ]}
    assert _detect_crud_entity(arch, "") == ("products", "PUT")


def test_detect_crud_entity_returns_tuple_with_patch():
    arch = {"api_endpoints": [
        {"method": "GET", "path": "/leagues"},
        {"method": "POST", "path": "/leagues"},
        {"method": "GET", "path": "/leagues/{id}"},
        {"method": "PATCH", "path": "/leagues/{id}"},
    ]}
    # No PUT anywhere -> falls through to the GET+POST fallback tier,
    # still correctly resolves PATCH as the update verb for that entity.
    assert _detect_crud_entity(arch, "") == ("leagues", "PATCH")


def test_detect_crud_entity_none_when_no_architecture():
    assert _detect_crud_entity({}, "") is None
    assert _detect_crud_entity(None, "") is None


# ── End-to-end: real HTTP journey against a PATCH-only server ──────────

_PATCH_ONLY_ARCH = {
    "api_endpoints": [
        {"method": "POST", "path": "/auth/register"},
        {"method": "POST", "path": "/auth/login"},
        {"method": "GET", "path": "/leagues"},
        {"method": "POST", "path": "/leagues"},
        {"method": "GET", "path": "/leagues/{id}"},
        {"method": "PATCH", "path": "/leagues/{id}"},
        {"method": "DELETE", "path": "/leagues/{id}"},
    ]
}


class _FakePatchOnlyHandler(BaseHTTPRequestHandler):
    items = {}

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
        elif self.path == "/leagues":
            self._send(200, list(type(self).items.values()))
        elif self.path.startswith("/leagues/"):
            item = type(self).items.get(self.path.split("/")[-1])
            self._send(200, item) if item else self._send(404, {"detail": "not found"})
        else:
            self._send(404, {})

    def do_POST(self):
        body = self._body()
        if self.path in ("/auth/register", "/auth/signup", "/auth/login"):
            self._send(200, {"access_token": "tok"})
        elif self.path == "/leagues":
            item_id = str(len(type(self).items) + 1)
            item = {"id": item_id, "name": body.get("name", "x")}
            type(self).items[item_id] = item
            self._send(201, item)
        else:
            self._send(404, {})

    def do_PATCH(self):
        item_id = self.path.split("/")[-1]
        if item_id in type(self).items:
            type(self).items[item_id].update(self._body())
            self._send(200, type(self).items[item_id])
        else:
            self._send(404, {})

    # Deliberately no do_PUT -- BaseHTTPRequestHandler returns 501 for an
    # unimplemented verb, reproducing "this verb doesn't work here" the
    # same way FastAPI's real 405 does for the confirmed live case.

    def do_DELETE(self):
        type(self).items.pop(self.path.split("/")[-1], None)
        self._send(204, {})


def _run_against_fake_server():
    _FakePatchOnlyHandler.items = {}
    server = HTTPServer(("127.0.0.1", 0), _FakePatchOnlyHandler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    project_root = Path(tempfile.mkdtemp(prefix="exp095_"))
    try:
        return run_user_journey(str(project_root), _PATCH_ONLY_ARCH, backend_port=port)
    finally:
        server.shutdown()
        shutil.rmtree(project_root, ignore_errors=True)


def test_edit_entity_passes_against_patch_only_backend():
    result = _run_against_fake_server()
    by_name = {s.name: s for s in result.steps}
    edit = by_name["Edit entity"]
    assert edit.passed, f"Edit entity should pass via PATCH, got: {edit.detail}"
    assert result.success


if __name__ == "__main__":
    import traceback
    tests = [obj for name, obj in list(globals().items()) if name.startswith("test_")]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"PASS: {t.__name__}")
        except AssertionError as e:
            print(f"FAIL: {t.__name__}: {e}")
            failed += 1
        except Exception as e:
            print(f"ERROR: {t.__name__}: {e}")
            traceback.print_exc()
            failed += 1
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    sys.exit(1 if failed else 0)
