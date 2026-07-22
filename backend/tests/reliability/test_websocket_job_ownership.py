"""Zero-network regression checks for authenticated job WebSocket streams.

Run directly:
  backend\\venv\\Scripts\\python.exe backend/tests/reliability/test_websocket_job_ownership.py
"""
from __future__ import annotations

import os
import secrets
import sys
from pathlib import Path

_BACKEND = Path(__file__).resolve().parents[2]
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))
os.environ.setdefault("SECRET_KEY", secrets.token_hex(32))


def test_http_and_websocket_share_token_verifier() -> None:
    from app.dependencies import auth

    source = Path(auth.__file__).read_text(encoding="utf-8")
    assert "return get_user_from_access_token(token, db)" in source
    assert "def get_user_from_access_token(token: str, db: Session) -> User:" in source


def test_shared_token_verifier_rejects_invalid_token() -> None:
    from fastapi import HTTPException
    from app.dependencies import auth

    try:
        auth.get_user_from_access_token("not-a-jwt", object())
    except HTTPException as exc:
        assert exc.status_code == 401
        assert exc.detail == "Could not validate credentials"
    else:
        raise AssertionError("malformed token must be rejected")


def test_websocket_rejects_invalid_token_before_streaming() -> None:
    from fastapi.testclient import TestClient
    from starlette.websockets import WebSocketDisconnect
    import main

    try:
        with TestClient(main.app).websocket_connect("/ws/foreign-job") as socket:
            socket.send_json({"type": "auth", "token": "not-a-jwt"})
            socket.receive_json()
    except WebSocketDisconnect as exc:
        assert exc.code == 1008
    else:
        raise AssertionError("invalid WebSocket token must get a generic policy close")


def test_websocket_authenticates_before_reading_job_store() -> None:
    import inspect
    import main

    source = inspect.getsource(main.ws_job)
    assert "websocket.receive_json()" in source
    assert "get_user_from_access_token(token, db)" in source
    assert source.index("get_user_from_access_token(token, db)") < source.index("JOB_STORE.get(job_id)")


def test_websocket_scopes_job_lookup_to_authenticated_owner() -> None:
    import inspect
    import main

    source = inspect.getsource(main.ws_job)
    assert "GenerationJob.id == job_id" in source
    assert "GenerationJob.user_id == current_user.id" in source
    assert "await websocket.close(code=1008)" in source
    assert '"Job not found"' not in source


def test_frontend_sends_token_only_after_socket_opens() -> None:
    source = (_BACKEND.parent / "frontend" / "src" / "pages" / "ProjectDetail.jsx").read_text(encoding="utf-8")
    assert "new WebSocket(`${proto}/ws/${id}`)" in source
    assert 'ws.onopen = () => ws.send(JSON.stringify({ type: "auth", token }))' in source
    assert "?token=" not in source


def main_test() -> None:
    tests = [value for name, value in globals().items() if name.startswith("test_")]
    for test in tests:
        test()
        print(f"PASS {test.__name__}")
    print(f"{len(tests)}/{len(tests)} WebSocket ownership tests passed")


if __name__ == "__main__":
    main_test()
