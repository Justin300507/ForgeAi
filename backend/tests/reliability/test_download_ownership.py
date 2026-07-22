"""Zero-network regression coverage for authenticated generated-code downloads.

Run directly:
  backend\\venv\\Scripts\\python.exe backend/tests/reliability/test_download_ownership.py
"""
from __future__ import annotations

import inspect
import os
import secrets
import sys
import tempfile
from pathlib import Path

_BACKEND = Path(__file__).resolve().parents[2]
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))
os.environ.setdefault("SECRET_KEY", secrets.token_hex(32))


class User:
    def __init__(self, user_id: int):
        self.id = user_id


class Job:
    project_name = None
    zip_path = None


class Query:
    def __init__(self, result):
        self.result = result
        self.filters = ()

    def filter(self, *filters):
        self.filters = filters
        return self

    def first(self):
        return self.result


class Db:
    def __init__(self, result):
        self.query_instance = Query(result)

    def query(self, _model):
        return self.query_instance


def _download(job, user_id: int):
    import main
    return main.download_zip("job-123", Db(job), User(user_id))


def test_owner_query_is_scoped_to_current_user() -> None:
    """An owner can reach their job's existing zip-resolution behavior."""
    import main
    db = Db(Job())
    try:
        main.download_zip("job-123", db, User(7))
    except Exception as exc:
        # Job is deliberately zip-less; the authorization query must still be
        # owner-scoped before the existing missing-zip 404 is raised.
        from fastapi import HTTPException
        assert isinstance(exc, HTTPException) and exc.status_code == 404
        assert "No generated code found" in exc.detail
    else:
        raise AssertionError("zip-less fixture must retain existing 404 behavior")
    rendered_filters = " ".join(map(str, db.query_instance.filters))
    assert "generation_jobs.user_id" in rendered_filters
    assert "generation_jobs.id" in rendered_filters


def test_foreign_job_is_indistinguishable_from_missing() -> None:
    from fastapi import HTTPException
    # The database query is owner-scoped, so both a foreign job and a missing
    # job resolve to no row before any filesystem access. The handler must not
    # reveal which case happened.
    for case in ("foreign", "missing"):
        try:
            _download(None, 99)
        except HTTPException as exc:
            assert exc.status_code == 404
            assert exc.detail == "Job not found"
        else:
            raise AssertionError(f"{case} jobs must return generic 404")


def test_no_token_remains_a_route_dependency() -> None:
    import main
    from fastapi.testclient import TestClient

    route = next(route for route in main.app.routes if getattr(route, "path", None) == "/api/download/{job_id}")
    dependency_calls = {dependency.call for dependency in route.dependant.dependencies}
    assert main.get_current_user in dependency_calls
    assert "current_user" in inspect.signature(main.download_zip).parameters
    response = TestClient(main.app).get("/api/download/job-123")
    assert response.status_code == 401


def test_frontend_uses_authenticated_blob_download_not_plain_anchor() -> None:
    frontend = (_BACKEND.parent / "frontend" / "src").resolve()
    api_source = (frontend / "api.js").read_text(encoding="utf-8")
    detail_source = (frontend / "pages" / "ProjectDetail.jsx").read_text(encoding="utf-8")
    assert 'downloadZip: (id) => api.get(`/api/download/${id}`, { responseType: "blob" })' in api_source
    assert "jobsAPI.downloadZip(job.id)" in detail_source
    assert "href={`/api/download/${job.id}`}" not in detail_source


def _assert_download_is_not_served(job) -> None:
    from fastapi import HTTPException

    try:
        _download(job, 7)
    except HTTPException as exc:
        assert exc.status_code == 404
        assert "No generated code found" in exc.detail
    else:
        raise AssertionError("unsafe zip path must not be served")


def test_download_accepts_only_a_conventional_zip_inside_generated_projects() -> None:
    """A legitimate persisted job archive remains downloadable."""
    name = f"download_safe_{secrets.token_hex(6)}"
    with tempfile.TemporaryDirectory() as td:
        original_cwd = os.getcwd()
        try:
            os.chdir(td)
            archive = Path("generated_projects") / f"{name}.zip"
            archive.parent.mkdir(parents=True)
            archive.write_bytes(b"PK\\x03\\x04")
            job = Job()
            job.project_name = name
            job.zip_path = str(archive.resolve())
            response = _download(job, 7)
            assert Path(response.path).resolve() == archive.resolve()
            assert response.filename == f"{name}.zip"
        finally:
            os.chdir(original_cwd)


def test_download_rejects_poisoned_absolute_and_traversal_zip_paths() -> None:
    """Persisted zip_path values cannot escape the generated-project root."""
    name = f"download_poison_{secrets.token_hex(6)}"
    with tempfile.TemporaryDirectory() as td:
        original_cwd = os.getcwd()
        try:
            os.chdir(td)
            outside = Path("outside") / f"{name}.zip"
            outside.parent.mkdir(parents=True)
            outside.write_bytes(b"PK\\x03\\x04")
            for poisoned_path in (
                str(outside.resolve()),
                str(Path("generated_projects") / ".." / outside),
            ):
                job = Job()
                job.project_name = name
                job.zip_path = poisoned_path
                _assert_download_is_not_served(job)
        finally:
            os.chdir(original_cwd)


def test_download_rejects_unsafe_project_names_before_path_construction() -> None:
    """Traversal and Windows-drive job names never reach ZIP resolution."""
    with tempfile.TemporaryDirectory() as td:
        original_cwd = os.getcwd()
        try:
            os.chdir(td)
            for unsafe_name in ("../outside", r"C:\\outside"):
                job = Job()
                job.project_name = unsafe_name
                job.zip_path = None
                _assert_download_is_not_served(job)
        finally:
            os.chdir(original_cwd)


def main_test() -> None:
    tests = [
        test_owner_query_is_scoped_to_current_user,
        test_foreign_job_is_indistinguishable_from_missing,
        test_no_token_remains_a_route_dependency,
        test_frontend_uses_authenticated_blob_download_not_plain_anchor,
        test_download_accepts_only_a_conventional_zip_inside_generated_projects,
        test_download_rejects_poisoned_absolute_and_traversal_zip_paths,
        test_download_rejects_unsafe_project_names_before_path_construction,
    ]
    for test in tests:
        test()
        print(f"PASS {test.__name__}")
    print(f"{len(tests)}/{len(tests)} download ownership tests passed")


if __name__ == "__main__":
    main_test()
