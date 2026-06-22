def calculate_forge_score(
    validation,
    runtime_result,
    frontend_build_result=None,
    playwright_result=None,
    vision_result=None,
    docker_result=None,
):

    category_weights = {
        "Missing app/main.py": 25,
        "Missing route file": 20,
        "Architecture violation": 15,
        "Schema mismatch": 8,
        "Session leak risk": 8,
        "Missing APIRouter": 8,
        "No endpoints found": 8,
        "Database symbol": 8,
        "ORM violation": 10,
        "Syntax error": 10,
        "Stub handler risk": 8,
        "Self-shadowing recursion risk": 10,
        "Missing endpoint": 5,
        "Missing symbol": 5,
        "Undefined symbol": 5,
        "Router export mismatch": 5,
        "Missing backend import target": 6,
        "Missing frontend import target": 6,
        "Import style mismatch": 6,
        "Orphan file": 3,
    }

    category_caps = {
        "Schema mismatch": 16,
        "ORM violation": 20,
        "Undefined symbol": 15,
        "Missing symbol": 15,
        "Database symbol": 16,
        "Import style mismatch": 12,
    }

    category_totals = {}
    other_total = 0

    for error in validation["errors"]:

        matched = False

        for category, weight in category_weights.items():

            if category in error:
                category_totals[category] = category_totals.get(category, 0) + weight
                matched = True
                break

        if not matched:
            other_total += 2

    score = 100

    for category, total in category_totals.items():
        cap = category_caps.get(category)
        score -= min(total, cap) if cap else total

    score -= other_total

    if not runtime_result or not runtime_result.get("success", False):
        score -= 20

    # Frontend build: -15 if Node is present but build failed; skip if Node missing
    if frontend_build_result and not frontend_build_result.get("node_missing", True):
        if not frontend_build_result.get("success", False):
            score -= 15

    # Playwright: -5 per blank page, -3 per JS console error (capped at -10 total)
    if playwright_result:
        # Support both dict and PlaywrightResult dataclass
        _get = playwright_result.get if isinstance(playwright_result, dict) else lambda k, d=None: getattr(playwright_result, k, d)
        if not _get("skipped"):
            blank_penalty = len(_get("blank_pages") or []) * 5
            error_penalty = len(_get("console_errors") or []) * 3
            score -= min(blank_penalty + error_penalty, 10)

    # Vision UI quality — tiered penalty based on how blank/broken the UI looks
    # 70+  = functional UI with real content  → 0
    # 50-69 = partial UI, missing some elements → -5
    # 30-49 = skeleton/placeholder UI           → -12
    # 0-29  = blank or completely broken        → -20
    if vision_result:
        _vget = vision_result.get if isinstance(vision_result, dict) else lambda k, d=None: getattr(vision_result, k, d)
        if not _vget("skipped"):
            ui_score = _vget("ui_score") or 100
            if ui_score < 30:
                score -= 20
            elif ui_score < 50:
                score -= 12
            elif ui_score < 70:
                score -= 5

    # Docker: -15 if build fails, -10 if container starts but health check fails
    if docker_result and not getattr(docker_result, "skipped", docker_result.get("skipped", True) if isinstance(docker_result, dict) else True):
        _dget = docker_result.get if isinstance(docker_result, dict) else lambda k, d=None: getattr(docker_result, k, d)
        if not _dget("build_passed"):
            score -= 15
        elif not _dget("health_passed"):
            score -= 10

    score = max(0, score)

    if score >= 90:
        grade = "A"

    elif score >= 80:
        grade = "B"

    elif score >= 70:
        grade = "C"

    elif score >= 60:
        grade = "D"

    else:
        grade = "F"

    return {
        "score": score,
        "grade": grade
    }