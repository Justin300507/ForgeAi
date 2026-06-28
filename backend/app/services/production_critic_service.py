"""
V5.2 Production Readiness Critic

Static analysis (no LLM) of generated Python files. Scores 0-100 across
four production-readiness dimensions: security, performance, reliability,
observability.

This is intentionally separate from forge_score — forge_score measures
generation quality (does it compile/run?), production_score measures
deployment fitness (is it safe to ship?).
"""
import re
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class ProductionIssue:
    severity: str        # "critical" | "warning" | "info"
    category: str        # "security" | "performance" | "reliability" | "observability"
    file: str
    line: int
    description: str
    fix_hint: str


@dataclass
class ProductionCriticResult:
    production_score: int
    issues: list = field(default_factory=list)
    categories: dict = field(default_factory=dict)
    skipped: bool = False
    skip_reason: str = ""


# ---------------------------------------------------------------------------
# Patterns
# ---------------------------------------------------------------------------

_SECRET_VAR_RE = re.compile(
    r'^\s*(SECRET_KEY|JWT_SECRET|PASSWORD|DB_PASSWORD|API_KEY|AUTH_TOKEN|PRIVATE_KEY'
    r'|ACCESS_TOKEN|CLIENT_SECRET)\s*=\s*["\'](?!.*os\.environ|.*getenv)[^"\']{4,}["\']',
    re.IGNORECASE | re.MULTILINE,
)

_SQL_INJECTION_RE = re.compile(
    r'f["\'].*SELECT.*\{|f["\'].*INSERT.*\{|f["\'].*UPDATE.*\{|f["\'].*DELETE.*\{',
    re.IGNORECASE,
)

_QUERY_ALL_RE = re.compile(r'db\.query\([^)]*\)\.all\(\)')
_QUERY_LIMIT_RE = re.compile(r'\.limit\(')

_LIST_ROUTE_RE = re.compile(
    r'@\w+_router\.(get|post)\(["\'][^"\']*["\'][^\n]*\)\s*\ndef\s+\w+\s*\(([^)]*)\)',
)
_PAGINATION_PARAMS_RE = re.compile(r'\b(limit|offset|page|per_page|skip)\b', re.IGNORECASE)

# N+1 heuristic: check for db.query() inside a for-loop block (line-based, not regex)
_N_PLUS_ONE_RE = None  # replaced by _has_n_plus_one()

_FIRST_NO_CHECK_RE = re.compile(
    r'=\s*db\.query\([^)]*\)\.first\(\)\s*$',
    re.MULTILINE,
)

_TRY_EXCEPT_RE = re.compile(r'\btry\s*:', re.MULTILINE)
_HTTP_EXCEPTION_RE = re.compile(r'\bHTTPException\b')
_ROUTE_HANDLER_RE = re.compile(
    r'@\w+_router\.\w+\([^\n]*\)\s*\n(?:async\s+)?def\s+\w+\([^)]*\)\s*:',
)

_LOGGING_IMPORT_RE = re.compile(r'\bimport\s+logging\b|\blogger\s*=\s*')
_HEALTH_ENDPOINT_RE = re.compile(r'["\'/]health["\']')
_MIDDLEWARE_RE = re.compile(r'add_middleware|BaseHTTPMiddleware|Request.*X-Request-ID', re.IGNORECASE)

_OPTIONAL_HINT_RE = re.compile(r'Optional\[')
_NULLABLE_FIELD_RE = re.compile(r':\s*(str|int|float|bool)\s*=\s*None')


# ---------------------------------------------------------------------------
# Per-file scanners
# ---------------------------------------------------------------------------

def _scan_security(path: Path, content: str, rel: str) -> list[ProductionIssue]:
    issues = []
    for m in _SECRET_VAR_RE.finditer(content):
        line = content[:m.start()].count("\n") + 1
        issues.append(ProductionIssue(
            severity="critical", category="security", file=rel, line=line,
            description=f"Hardcoded secret: {m.group().strip()[:60]}",
            fix_hint="Use os.environ.get('SECRET_KEY') and a .env file instead.",
        ))
    for m in _SQL_INJECTION_RE.finditer(content):
        line = content[:m.start()].count("\n") + 1
        issues.append(ProductionIssue(
            severity="critical", category="security", file=rel, line=line,
            description="Possible SQL injection via f-string query",
            fix_hint="Use parameterised SQLAlchemy queries, never string interpolation.",
        ))
    return issues


def _scan_performance(path: Path, content: str, rel: str) -> list[ProductionIssue]:
    issues = []

    # db.query(...).all() without .limit()
    for m in _QUERY_ALL_RE.finditer(content):
        surrounding = content[max(0, m.start() - 200):m.end() + 200]
        if not _QUERY_LIMIT_RE.search(surrounding):
            line = content[:m.start()].count("\n") + 1
            issues.append(ProductionIssue(
                severity="warning", category="performance", file=rel, line=line,
                description="Unbounded db.query().all() — no .limit() applied",
                fix_hint="Add .limit(limit).offset(offset) and expose limit/offset as query params.",
            ))

    # List routes missing pagination params
    for m in _LIST_ROUTE_RE.finditer(content):
        method = m.group(1).upper()
        params_block = m.group(2)
        if method == "GET" and not _PAGINATION_PARAMS_RE.search(params_block):
            line = content[:m.start()].count("\n") + 1
            issues.append(ProductionIssue(
                severity="info", category="performance", file=rel, line=line,
                description="GET list endpoint has no pagination parameters",
                fix_hint="Add limit: int = Query(20), offset: int = Query(0) to the handler.",
            ))

    # N+1 smell — line-based check avoids catastrophic regex backtracking
    lines_list = content.splitlines()
    in_for_block = False
    for_indent = -1
    for i, ln in enumerate(lines_list[:200]):  # cap at 200 lines for speed
        stripped = ln.lstrip()
        indent = len(ln) - len(stripped)
        if stripped.startswith("for ") and " in " in stripped:
            in_for_block = True
            for_indent = indent
        elif in_for_block:
            if indent <= for_indent and stripped and not stripped.startswith("#"):
                in_for_block = False
            elif "db.query(" in stripped:
                issues.append(ProductionIssue(
                    severity="warning", category="performance", file=rel, line=i + 1,
                    description="Possible N+1 query: db.query() inside a for loop",
                    fix_hint="Use joinedload() or a single query with IN clause instead.",
                ))
                break

    return issues


def _scan_reliability(path: Path, content: str, rel: str) -> list[ProductionIssue]:
    issues = []

    # .first() result used without None check — verify next line has guard
    lines_list = content.splitlines()
    for m in _FIRST_NO_CHECK_RE.finditer(content):
        line_no = content[:m.start()].count("\n")
        next_line = lines_list[line_no + 1].lstrip() if line_no + 1 < len(lines_list) else ""
        if not re.match(r'(if|assert|raise)\b', next_line):
            issues.append(ProductionIssue(
                severity="warning", category="reliability", file=rel, line=line_no + 1,
                description=".first() result may be None — no null-check follows",
                fix_hint="Add: if result is None: raise HTTPException(status_code=404, detail='Not found')",
            ))

    # Route files without any error handling
    if "router" in content and not _TRY_EXCEPT_RE.search(content) and not _HTTP_EXCEPTION_RE.search(content):
        issues.append(ProductionIssue(
            severity="warning", category="reliability", file=rel, line=0,
            description="Route file has no try/except or HTTPException usage",
            fix_hint="Wrap db operations in try/except and raise HTTPException on failure.",
        ))

    # Nullable fields typed without Optional
    for m in _NULLABLE_FIELD_RE.finditer(content):
        line = content[:m.start()].count("\n") + 1
        surrounding = content[max(0, m.start() - 100):m.start()]
        if "Optional" not in surrounding:
            issues.append(ProductionIssue(
                severity="info", category="reliability", file=rel, line=line,
                description=f"Field with `= None` default not typed Optional: {m.group().strip()}",
                fix_hint="Change to Optional[type] = None and import Optional from typing.",
            ))
            break  # one per file to avoid noise

    return issues


def _scan_observability(content: str, rel: str, is_service: bool, is_main: bool) -> list[ProductionIssue]:
    issues = []

    if is_service and not _LOGGING_IMPORT_RE.search(content):
        issues.append(ProductionIssue(
            severity="info", category="observability", file=rel, line=0,
            description="Service file has no logging",
            fix_hint="Add: import logging; logger = logging.getLogger(__name__)",
        ))

    if is_main and not _HEALTH_ENDPOINT_RE.search(content):
        issues.append(ProductionIssue(
            severity="warning", category="observability", file=rel, line=0,
            description="No /health endpoint in main.py",
            fix_hint="Add: @app.get('/health') def health(): return {'status': 'ok'}",
        ))

    if is_main and not _MIDDLEWARE_RE.search(content):
        issues.append(ProductionIssue(
            severity="info", category="observability", file=rel, line=0,
            description="No request middleware (CORS, request-ID, etc.) configured",
            fix_hint="Consider adding CORSMiddleware and a request-ID middleware.",
        ))

    return issues


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

_CATEGORY_WEIGHTS = {
    "security": 30,
    "performance": 20,
    "reliability": 25,
    "observability": 25,
}

_SEVERITY_PENALTY = {"critical": 10, "warning": 3, "info": 1}
_CATEGORY_CAP = {"security": 30, "performance": 20, "reliability": 25, "observability": 25}


def _compute_score(issues: list[ProductionIssue]) -> tuple[int, dict]:
    penalties: dict[str, int] = {}
    for issue in issues:
        cat = issue.category
        penalties[cat] = penalties.get(cat, 0) + _SEVERITY_PENALTY.get(issue.severity, 1)

    score = 100
    cat_scores = {}
    for cat, weight in _CATEGORY_WEIGHTS.items():
        penalty = min(penalties.get(cat, 0), _CATEGORY_CAP[cat])
        cat_score = max(0, weight - penalty)
        cat_scores[cat] = round(cat_score / weight * 100)
        score -= penalty

    return max(0, score), cat_scores


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def run_production_critic(project_path: str) -> ProductionCriticResult:
    app_dir = Path(project_path) / "app"
    if not app_dir.exists():
        return ProductionCriticResult(
            production_score=0, skipped=True,
            skip_reason="app/ directory not found",
        )

    all_issues: list[ProductionIssue] = []

    py_files = list(app_dir.rglob("*.py"))
    if not py_files:
        return ProductionCriticResult(
            production_score=0, skipped=True,
            skip_reason="No Python files found in app/",
        )

    for py_path in py_files:
        try:
            content = py_path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue

        rel = str(py_path.relative_to(project_path)).replace("\\", "/")
        is_service = "/services/" in rel
        is_main = rel.endswith("main.py")

        all_issues += _scan_security(py_path, content, rel)
        all_issues += _scan_performance(py_path, content, rel)
        all_issues += _scan_reliability(py_path, content, rel)
        all_issues += _scan_observability(content, rel, is_service, is_main)

    score, cat_scores = _compute_score(all_issues)

    return ProductionCriticResult(
        production_score=score,
        issues=all_issues,
        categories=cat_scores,
    )


def print_production_report(result: ProductionCriticResult) -> None:
    print(f"\n=== PRODUCTION READINESS ===")
    if result.skipped:
        print(f"  Skipped: {result.skip_reason}")
        return
    print(f"  Score: {result.production_score}/100")
    cats = result.categories
    print(f"  Security:{cats.get('security',0)}  Perf:{cats.get('performance',0)}"
          f"  Reliability:{cats.get('reliability',0)}  Observability:{cats.get('observability',0)}")
    shown = 0
    for issue in sorted(result.issues, key=lambda i: ("info", "warning", "critical").index(i.severity)):
        if shown >= 8:
            remaining = len(result.issues) - shown
            print(f"  ... and {remaining} more issues")
            break
        print(f"  [{issue.severity}] {issue.category}: {issue.description}"
              f" ({issue.file}:{issue.line})")
        shown += 1
