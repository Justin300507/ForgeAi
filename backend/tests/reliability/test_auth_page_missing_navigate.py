"""
Exp152: a signup/register/login page's success handler can store a token
via `localStorage.setItem('token', ...)` and then simply never navigate
anywhere. Confirmed live (habit_tracker, 2026-07-31): an LLM-generated
SignupPage.jsx (created by the missing-file agent, not
patch_ensure_auth_pages's own deterministic template) left only a comment
where the redirect should be. Clicking "Sign Up" did nothing visible --
the request succeeded, a valid token was stored, but the user never left
the form. Invisible to every other automated check, since the CRUD
journey obtains its token directly from the API, never by clicking
through the UI.

Run directly: python tests/reliability/test_auth_page_missing_navigate.py
"""
import os
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from app.services.deterministic_patcher import (
    _patch_auth_page_missing_post_success_navigate as _patch,
)


def _project(files: dict) -> Path:
    root = Path(tempfile.mkdtemp(prefix="exp152_test_"))
    for rel, content in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    return root


SIGNUP_MISSING_NAVIGATE = """\
import React, { useState } from 'react';
import API from '../api';

const SignupPage = () => {
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  const handleSignup = async (e) => {
    e.preventDefault();
    setLoading(true);
    setError('');

    try {
      const response = await API.post('/auth/signup', { email, password });
      localStorage.setItem('token', response.data.access_token);
      // Redirect or perform further actions after successful signup
    } catch (err) {
      setError('Signup failed. Please try again.');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div>form here</div>
  );
};

export default SignupPage;
"""


def test_injects_navigate_and_hook_and_import_when_all_missing():
    root = _project({"src/pages/SignupPage.jsx": SIGNUP_MISSING_NAVIGATE})
    try:
        assert _patch(root) == 1
        out = (root / "src/pages/SignupPage.jsx").read_text(encoding="utf-8")
        assert "import { useNavigate } from 'react-router-dom';" in out
        assert "const navigate = useNavigate();" in out
        assert "localStorage.setItem('token', response.data.access_token);\n      navigate('/dashboard');" in out
        # Only one navigate() call injected, not duplicated across passes.
        assert out.count("navigate('/dashboard')") == 1
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_idempotent_second_pass_is_noop():
    root = _project({"src/pages/SignupPage.jsx": SIGNUP_MISSING_NAVIGATE})
    try:
        _patch(root)
        first = (root / "src/pages/SignupPage.jsx").read_text(encoding="utf-8")
        n2 = _patch(root)
        second = (root / "src/pages/SignupPage.jsx").read_text(encoding="utf-8")
        assert n2 == 0
        assert first == second
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_already_navigating_page_untouched():
    schema = """\
import React from 'react';
import { useNavigate } from 'react-router-dom';
import API from '../api';

const Register = () => {
  const navigate = useNavigate();
  const handleSubmit = async (e) => {
    e.preventDefault();
    const response = await API.post('/auth/signup', {});
    localStorage.setItem('token', response.data.access_token);
    navigate('/dashboard');
  };
  return <div>form</div>;
};

export default Register;
"""
    root = _project({"src/pages/Register.jsx": schema})
    try:
        before = (root / "src/pages/Register.jsx").read_text(encoding="utf-8")
        assert _patch(root) == 0
        assert (root / "src/pages/Register.jsx").read_text(encoding="utf-8") == before
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_reuses_existing_react_router_dom_import_instead_of_duplicating():
    schema = """\
import React from 'react';
import { Link } from 'react-router-dom';
import API from '../api';

const LoginPage = () => {
  const handleSubmit = async (e) => {
    e.preventDefault();
    const response = await API.post('/auth/login', {});
    localStorage.setItem('token', response.data.access_token);
  };
  return <div>form</div>;
};

export default LoginPage;
"""
    root = _project({"src/pages/LoginPage.jsx": schema})
    try:
        assert _patch(root) == 1
        out = (root / "src/pages/LoginPage.jsx").read_text(encoding="utf-8")
        assert out.count("from 'react-router-dom'") == 1
        assert "Link" in out and "useNavigate" in out
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_non_auth_page_never_touched():
    schema = """\
import React from 'react';
import API from '../api';

const HabitsPage = () => {
  const load = async () => {
    const response = await API.get('/habits');
    localStorage.setItem('lastLoaded', Date.now());
  };
  return <div>habits</div>;
};

export default HabitsPage;
"""
    root = _project({"src/pages/HabitsPage.jsx": schema})
    try:
        before = (root / "src/pages/HabitsPage.jsx").read_text(encoding="utf-8")
        assert _patch(root) == 0
        assert (root / "src/pages/HabitsPage.jsx").read_text(encoding="utf-8") == before
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_no_pages_dir_is_a_noop():
    root = Path(tempfile.mkdtemp(prefix="exp152_test_"))
    try:
        assert _patch(root) == 0
    finally:
        shutil.rmtree(root, ignore_errors=True)


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
