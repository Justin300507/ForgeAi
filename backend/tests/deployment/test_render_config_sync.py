"""
Unit test proving render.yaml generation (deployment_config_service.py)
and the live Render REST API deploy payload (render_provider.py) stay
synchronized -- both now consume RENDER_BACKEND_BUILD_COMMAND /
RENDER_BACKEND_START_COMMAND from the single shared module
app/deployments/render_config.py (docs/V16_DEPLOYMENT_RELIABILITY_AUDIT.md,
Finding #1), instead of independently hardcoding the same two strings.

Neither test below hardcodes an expected literal command string --
both assert against the SAME imported constants the production code
uses, so if either production file ever reintroduces its own hardcoded
copy (reversing the fix) or someone changes render_config.py without
updating a caller, this test would catch the divergence.

Plain assert-based (no pytest installed in this project) -- run directly:
python tests/deployment/test_render_config_sync.py
"""
import os
import sys
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from app.deployments.render_config import RENDER_BACKEND_BUILD_COMMAND, RENDER_BACKEND_START_COMMAND
from app.deployments.render_provider import RenderProvider
from app.services.deployment_config_service import _build_render_yaml


def test_render_yaml_uses_shared_build_and_start_commands():
    yaml_content = _build_render_yaml("test_project", ["SECRET_KEY", "DATABASE_URL"])
    assert f"buildCommand: {RENDER_BACKEND_BUILD_COMMAND}" in yaml_content
    assert f"startCommand: {RENDER_BACKEND_START_COMMAND}" in yaml_content


def test_render_provider_api_payload_uses_shared_build_and_start_commands():
    provider = RenderProvider()
    provider.api_key = "fake-key-for-test"
    provider._owner_id = "fake-owner-for-test"  # skip the network-based auto-discovery

    captured_payload = {}

    def _fake_post(path, body):
        captured_payload.update(body)
        response = mock.Mock()
        response.ok = True
        response.json.return_value = {"service": {"id": "srv-fake"}}
        return response

    with mock.patch.object(provider, "_post", side_effect=_fake_post):
        provider._create_web_service("test-backend", "https://github.com/x/y", env_vars={})

    env_specific = captured_payload["serviceDetails"]["envSpecificDetails"]
    assert env_specific["buildCommand"] == RENDER_BACKEND_BUILD_COMMAND
    assert env_specific["startCommand"] == RENDER_BACKEND_START_COMMAND


def test_both_outputs_are_identical_to_each_other():
    # The actual synchronization proof: extract both real outputs and
    # compare them to EACH OTHER, not just to the shared constant --
    # this is what the pre-fix code could never guarantee (two
    # independently-typed literals that happened to match by coincidence).
    yaml_content = _build_render_yaml("another_project", [])

    provider = RenderProvider()
    provider.api_key = "fake-key-for-test"
    provider._owner_id = "fake-owner-for-test"
    captured_payload = {}

    def _fake_post(path, body):
        captured_payload.update(body)
        response = mock.Mock()
        response.ok = True
        response.json.return_value = {"service": {"id": "srv-fake"}}
        return response

    with mock.patch.object(provider, "_post", side_effect=_fake_post):
        provider._create_web_service("another-backend", "https://github.com/x/y", env_vars={})

    env_specific = captured_payload["serviceDetails"]["envSpecificDetails"]
    assert f"buildCommand: {env_specific['buildCommand']}" in yaml_content
    assert f"startCommand: {env_specific['startCommand']}" in yaml_content


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
