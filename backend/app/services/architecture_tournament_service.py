"""
V5.4 Architecture Benchmarking (Tournament Mode)

For a single app idea, generate N competing architectures, validate each
backend, and keep the winner. Quality boost: instead of one shot, pick
the best of N.
"""
import json
import os
import shutil
import time
from dataclasses import dataclass, field


@dataclass
class TournamentResult:
    winner_architecture: dict
    winner_index: int
    scores: list = field(default_factory=list)          # forge score per candidate
    validation_errors: list = field(default_factory=list)  # errors per candidate
    duration: float = 0.0
    candidates_tried: int = 0
    skipped: bool = False
    skip_reason: str = ""


def _write_candidate(project_name: str, index: int, files: list[dict]) -> str:
    """Write backend files to a temp candidate dir and return the path."""
    import re
    safe_name = re.sub(r"[^a-z0-9_]", "_", project_name.lower())
    candidate_name = f"{safe_name}_candidate_{index}"
    base_dir = os.path.abspath(
        os.path.join("..", "generated_projects", candidate_name)
    )
    if os.path.exists(base_dir):
        shutil.rmtree(base_dir, ignore_errors=True)
    os.makedirs(base_dir, exist_ok=True)

    from app.templates.database_template import DATABASE_PY_TEMPLATE
    from app.services.file_writer_service import _normalize_newlines, _ensure_create_all, _is_safe_to_write

    for file in files:
        path = file.get("path", "")
        content = file.get("content", "")
        if path == "app/database.py":
            content = DATABASE_PY_TEMPLATE
        content = _normalize_newlines(path, content)
        content = _ensure_create_all(path, content)
        if not _is_safe_to_write(path, content):
            continue
        abs_path = os.path.join(base_dir, path.replace("/", os.sep))
        os.makedirs(os.path.dirname(abs_path), exist_ok=True)
        try:
            with open(abs_path, "w", encoding="utf-8") as f:
                f.write(content)
        except Exception:
            pass

    return base_dir


def _cleanup(base_dir: str) -> None:
    try:
        shutil.rmtree(base_dir, ignore_errors=True)
    except Exception:
        pass


def _architectures_equal(a: dict, b: dict) -> bool:
    """Rough equality check — same endpoint paths."""
    a_paths = sorted(e.get("path", "") for e in a.get("api_endpoints", []))
    b_paths = sorted(e.get("path", "") for e in b.get("api_endpoints", []))
    return a_paths == b_paths


def run_architecture_tournament(
    plan: dict,
    provider: str = "auto",
    n_candidates: int = 3,
) -> TournamentResult:
    """
    Generate n_candidates architectures, validate each, return the best.
    Stops early if any candidate has zero validation errors.
    """
    from app.services.architect_service import generate_architecture
    from app.services.backend_service import generate_backend
    from app.services.validator_service import validate_project
    from app.services.forge_score_service import calculate_forge_score

    t0 = time.time()
    project_name = plan.get("project_name", "tournament_project")

    architectures: list[dict] = []
    candidate_dirs: list[str] = []
    candidate_scores: list[int] = []
    candidate_errors: list[list[str]] = []

    best_score = -1
    best_index = 0

    print(f"\n=== ARCHITECTURE TOURNAMENT: {n_candidates} candidates for '{project_name}' ===")

    for i in range(n_candidates):
        print(f"\n--- Candidate {i + 1}/{n_candidates} ---")
        try:
            arch = generate_architecture(plan, provider)
        except Exception as e:
            print(f"  Candidate {i+1} architect failed: {e}")
            candidate_scores.append(0)
            candidate_errors.append([str(e)])
            candidate_dirs.append("")
            continue

        # Skip duplicates
        duplicate = any(_architectures_equal(arch, prev) for prev in architectures)
        if duplicate:
            print(f"  Candidate {i+1} is identical to a prior candidate — skipping")
            candidate_scores.append(0)
            candidate_errors.append(["duplicate"])
            candidate_dirs.append("")
            architectures.append(arch)
            continue

        architectures.append(arch)

        # Generate backend only (frontend not needed for static validation)
        try:
            backend_response = generate_backend(arch, provider)
            files = backend_response.get("files", [])
        except Exception as e:
            print(f"  Candidate {i+1} backend generation failed: {e}")
            candidate_scores.append(0)
            candidate_errors.append([str(e)])
            candidate_dirs.append("")
            continue

        # Write to temp dir
        try:
            candidate_dir = _write_candidate(project_name, i, files)
            candidate_dirs.append(candidate_dir)
        except Exception as e:
            print(f"  Candidate {i+1} write failed: {e}")
            candidate_scores.append(0)
            candidate_errors.append([str(e)])
            candidate_dirs.append("")
            continue

        # Validate
        try:
            validation = validate_project(candidate_dir)
        except Exception as e:
            print(f"  Candidate {i+1} validation error: {e}")
            candidate_scores.append(0)
            candidate_errors.append([str(e)])
            continue

        score_data = calculate_forge_score(validation, runtime_result=None)
        score = score_data.get("score", 0)
        errors = validation.get("errors", [])

        candidate_scores.append(score)
        candidate_errors.append(errors)

        print(f"  Candidate {i+1}: score={score}/100, errors={len(errors)}")

        if score > best_score:
            best_score = score
            best_index = i

        # Early exit: perfect static validation
        if validation.get("passed") and score >= 100:
            print(f"  Perfect candidate found at {i+1} — stopping early")
            # Fill remaining slots
            for _ in range(i + 1, n_candidates):
                candidate_scores.append(0)
                candidate_errors.append([])
                candidate_dirs.append("")
            break

    # Cleanup all non-winner candidate dirs
    for idx, candidate_dir in enumerate(candidate_dirs):
        if candidate_dir and idx != best_index:
            _cleanup(candidate_dir)
            print(f"  Cleaned up candidate {idx+1} dir")

    winner_arch = architectures[best_index] if architectures else {}
    print(f"\nTournament winner: candidate {best_index+1} (score={best_score})")

    return TournamentResult(
        winner_architecture=winner_arch,
        winner_index=best_index,
        scores=candidate_scores,
        validation_errors=candidate_errors,
        duration=round(time.time() - t0, 2),
        candidates_tried=len([s for s in candidate_scores if s > 0]),
    )
