"""
Verifies the journey runner attaches request/response evidence to failed
steps (for the Forensic Bundle system) without changing any step
closure's return signature, and that backend_runner.py surfaces it.
Run directly: python tests/reliability/test_journey_evidence_capture.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from app.runtime.user_journey_runner import JourneyStep, _ExchangeRecorder


class _FakeResponse:
    def __init__(self, status_code, body):
        self.status_code = status_code
        self._body = body
        self.text = str(body)

    def json(self):
        if isinstance(self._body, dict):
            return self._body
        raise ValueError("not json")


class _FakeRequestsModule:
    def __init__(self, response):
        self._response = response
        self.calls = []

    def post(self, url, **kwargs):
        self.calls.append(("POST", url, kwargs))
        return self._response

    def get(self, url, **kwargs):
        self.calls.append(("GET", url, kwargs))
        return self._response


def test_journey_step_defaults_request_response_to_none():
    step = JourneyStep(name="x", passed=True, duration_ms=1.0)
    assert step.request is None
    assert step.response is None


def test_recorder_captures_last_exchange_on_post():
    fake = _FakeRequestsModule(_FakeResponse(422, {"detail": "bad field"}))
    recorder = _ExchangeRecorder(fake)
    resp = recorder.post("http://x/items", json={"a": 1}, headers={"Authorization": "Bearer secret"})
    assert resp.status_code == 422
    assert recorder.last_exchange["request"]["method"] == "POST"
    assert recorder.last_exchange["request"]["json"] == {"a": 1}
    assert recorder.last_exchange["request"]["has_auth"] is True
    assert "Authorization" not in recorder.last_exchange["request"]
    assert recorder.last_exchange["response"]["status_code"] == 422
    assert recorder.last_exchange["response"]["body"] == {"detail": "bad field"}


def test_backend_runner_surfaces_request_response_in_journey_data():
    src_path = os.path.join(
        os.path.dirname(__file__), "..", "..", "app", "runtime", "backend_runner.py"
    )
    with open(src_path, "r", encoding="utf-8") as f:
        src = f.read()
    block_start = src.index('journey_data = {')
    block = src[block_start:block_start + 900]
    assert '"request": s.request' in block
    assert '"response": s.response' in block


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
