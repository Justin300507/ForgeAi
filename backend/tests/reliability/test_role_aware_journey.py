"""
Verifies the V20.1.5 role-aware CRUD journey retry in
app/runtime/user_journey_runner.py: on a 403 from Create, if the app has a
discoverable role vocabulary (app/schemas/*.py declares a Field default +
pattern-constrained `role`), the journey registers an elevated identity
and retries -- continuing the REST of the journey as that identity if it
unlocks Create.

Runs a real, minimal stdlib HTTP server (no FastAPI/uvicorn needed) that
reproduces exactly the shape confirmed live in a generated restaurant app:
plain-role signups get 403 on Create, an elevated ("staff") signup gets
201. This is a genuine behavioral test against real HTTP traffic, not a
source-text assertion -- the live end-to-end proof (real generated app,
real uvicorn, real bcrypt/JWT) is documented in Experiment 043; this is
its fast, deterministic, CI-runnable regression companion.

Run directly: python tests/reliability/test_role_aware_journey.py
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

from app.runtime.user_journey_runner import run_user_journey

_ARCH = {
    "api_endpoints": [
        {"method": "POST", "path": "/auth/register"},
        {"method": "POST", "path": "/auth/login"},
        {"method": "GET", "path": "/menu"},
        {"method": "POST", "path": "/menu"},
        {"method": "GET", "path": "/menu/{id}"},
        {"method": "PUT", "path": "/menu/{id}"},
        {"method": "DELETE", "path": "/menu/{id}"},
    ]
}


class _FakeRestaurantAppHandler(BaseHTTPRequestHandler):
    users = {}   # email -> role
    items = {}   # id -> dict

    def log_message(self, *a):
        pass  # silence stdlib's default request logging

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

    def _role_for_token(self):
        auth = self.headers.get("Authorization", "")
        token = auth.replace("Bearer ", "")
        return type(self).users.get(token)

    def do_GET(self):
        if self.path == "/docs":
            self._send(200, {})
        elif self.path == "/menu":
            self._send(200, list(type(self).items.values()))
        elif self.path.startswith("/menu/"):
            item_id = self.path.split("/")[-1]
            item = type(self).items.get(item_id)
            self._send(200, item) if item else self._send(404, {"detail": "not found"})
        else:
            self._send(404, {})

    def do_POST(self):
        body = self._body()
        if self.path in ("/auth/register", "/auth/signup"):
            email = body.get("email", "")
            role = body.get("role") or "diner"
            type(self).users[email] = role  # token == email, for simplicity
            self._send(200, {"access_token": email})
        elif self.path == "/auth/login":
            self._send(200, {"access_token": body.get("email", "")})
        elif self.path == "/menu":
            role = self._role_for_token()
            if role != "staff":
                self._send(403, {"detail": "Not authorized to manage menu items"})
                return
            item_id = str(len(type(self).items) + 1)
            item = {"id": item_id, "title": body.get("title", "x")}
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
        item_id = self.path.split("/")[-1]
        type(self).items.pop(item_id, None)
        self._send(204, {})


def _run_journey_against_fake_server(role_info):
    _FakeRestaurantAppHandler.users = {}
    _FakeRestaurantAppHandler.items = {}
    server = ThreadingHTTPServer(("127.0.0.1", 0), _FakeRestaurantAppHandler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    project_root = Path(tempfile.mkdtemp(prefix="rolejourney_"))
    if role_info:
        schemas = project_root / "app" / "schemas"
        schemas.mkdir(parents=True)
        default_role, allowed = role_info
        pattern = "|".join(allowed)
        (schemas / "auth.py").write_text(
            "from pydantic import BaseModel, Field\n\n"
            "class RegisterRequest(BaseModel):\n"
            f'    role: str = Field("{default_role}", pattern="^({pattern})$")\n',
            encoding="utf-8",
        )
    try:
        return run_user_journey(str(project_root), _ARCH, backend_port=port)
    finally:
        server.shutdown()
        shutil.rmtree(project_root, ignore_errors=True)


def test_elevates_and_passes_when_role_vocabulary_is_discoverable():
    result = _run_journey_against_fake_server(("diner", ["diner", "staff"]))
    by_name = {s.name: s for s in result.steps}
    create = by_name["Create entity"]
    assert create.passed, f"Create entity should pass after elevation, got: {create.detail}"
    assert "elevated after 403" in create.detail
    assert "role=staff" in create.detail
    # The rest of the journey must continue as the elevated identity
    assert by_name["Edit entity"].passed
    assert by_name["Delete entity"].passed


def test_stays_403_when_no_role_vocabulary_discoverable():
    """No app/schemas/auth.py role pattern -- nothing to elevate to, the
    403 must be left as a real, reported failure."""
    result = _run_journey_against_fake_server(None)
    by_name = {s.name: s for s in result.steps}
    create = by_name["Create entity"]
    assert not create.passed
    assert create.detail == "403"


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
