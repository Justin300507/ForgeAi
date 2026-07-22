"""Regression checks for Exp119 queue-router JWT protection.

Run directly: python tests/reliability/test_exp119_queue_api_auth.py

The tests use an isolated FastAPI app and dependency overrides; they make no
network calls, touch no production database, and never start workers.
"""
import os
import secrets
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
os.environ.setdefault("SECRET_KEY", secrets.token_hex(32))


def _isolated_app():
    from fastapi import FastAPI
    from app.queue.api import router

    app = FastAPI()
    app.include_router(router)
    return app


def test_queue_routes_reject_missing_bearer_token():
    from fastapi.testclient import TestClient

    client = TestClient(_isolated_app())
    response = client.post("/queue/submit", json={"idea": "Build a secure todo app"})

    assert response.status_code == 401
    assert response.headers.get("WWW-Authenticate") == "Bearer"


def test_authenticated_queue_submit_reaches_mocked_enqueue_only():
    from fastapi.testclient import TestClient
    from app.database import get_db
    from app.dependencies import auth
    from app.queue import api as queue_api

    app = _isolated_app()
    app.dependency_overrides[get_db] = lambda: iter([object()])
    calls = []

    original_get_user = auth.get_user_by_email
    original_enqueue = queue_api.forge_queue.enqueue
    original_stats = queue_api.forge_queue.stats_for_owner
    original_status = queue_api.dispatcher.status
    try:
        auth.get_user_by_email = lambda _db, _email: SimpleNamespace(id=42)
        queue_api.forge_queue.enqueue = lambda **kwargs: calls.append(kwargs) or "test-job-1"
        queue_api.forge_queue.stats_for_owner = lambda owner_id: {"pending": 0}
        queue_api.dispatcher.status = lambda: {"alive": 0}
        token = auth.create_access_token({"sub": "queue@example.com"})

        response = TestClient(app).post(
            "/queue/submit",
            json={"idea": "Build a secure todo app", "provider": "auto", "config": {}},
            headers={"Authorization": f"Bearer {token}"},
        )
    finally:
        auth.get_user_by_email = original_get_user
        queue_api.forge_queue.enqueue = original_enqueue
        queue_api.forge_queue.stats_for_owner = original_stats
        queue_api.dispatcher.status = original_status

    assert response.status_code == 200, response.text
    assert response.json()["job_id"] == "test-job-1"
    assert calls == [{
        "idea": "Build a secure todo app", "provider": "auto", "config": {}, "owner_id": 42,
    }]


def test_every_queue_route_carries_router_auth_dependency():
    from app.dependencies.auth import get_current_user
    from app.queue.api import router

    routes = [route for route in router.routes if getattr(route, "path", "").startswith("/queue/")]
    assert routes, "queue router must expose routes"
    for route in routes:
        dependencies = getattr(route, "dependencies", [])
        assert any(getattr(dependency, "dependency", None) is get_current_user for dependency in dependencies), (
            f"{route.path} must inherit get_current_user"
        )


if __name__ == "__main__":
    import traceback

    tests = [value for name, value in list(globals().items()) if name.startswith("test_")]
    failed = 0
    for test in tests:
        try:
            test()
            print(f"PASS: {test.__name__}")
        except Exception:
            failed += 1
            print(f"FAIL: {test.__name__}")
            traceback.print_exc()
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    raise SystemExit(1 if failed else 0)
