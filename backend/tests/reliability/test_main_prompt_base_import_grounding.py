"""
Regression test for build_main_prompt's Base/engine import grounding.

Reproduced live (ForgeBench v1.0, blog_cms, 2026-07-28): the main.py
generation prompt told the model to "Call Base.metadata.create_all(bind=engine)
AFTER model imports" but never explicitly said WHERE Base/engine come from.
The REQUIRED MODEL IMPORTS block only lists `from app.models.X import *`
lines (models import Base, they don't define it), so the model incorrectly
inferred Base was among those wildcard-imported symbols and wrote
"from app.models import Base" -- no such symbol exists there (real models:
Post, PostTag, Tag, User). The write-time hallucinated-model-attribute
guard correctly refused it, the regular fix-loop retry produced a
truncated/syntax-broken rewrite (also refused), and app/main.py was left
missing entirely -- Runtime score 25/100, overall 29.46/F for an app that
otherwise generated cleanly.

Run directly: python tests/reliability/test_main_prompt_base_import_grounding.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from app.prompts.parallel_backend_prompt import build_main_prompt


def test_prompt_states_explicit_database_import():
    prompt = build_main_prompt(
        route_files=["app/routes/post_routes.py", "app/routes/user_routes.py"],
        project_name="Blog CMS",
        contract="CONTRACT_TEXT",
        model_files=["app/models/post.py", "app/models/user.py"],
    )
    assert "from app.database import Base, engine" in prompt


def test_prompt_explicitly_forbids_importing_base_from_models():
    prompt = build_main_prompt(
        route_files=["app/routes/post_routes.py"],
        project_name="Blog CMS",
        contract="CONTRACT_TEXT",
        model_files=["app/models/post.py"],
    )
    assert "from app.models import Base" in prompt  # named as the forbidden pattern
    assert "NOT in app/models" in prompt or "never from app.models" in prompt.lower()


def test_prompt_still_works_with_no_models():
    prompt = build_main_prompt(
        route_files=["app/routes/auth_routes.py"],
        project_name="Simple App",
        contract="CONTRACT_TEXT",
        model_files=[],
    )
    assert "from app.database import Base, engine" in prompt
    assert "# no models" in prompt


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
