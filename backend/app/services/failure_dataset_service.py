"""
V7 Failure Dataset Builder

Stores complete run records: idea, plan, architecture, generated code,
validation errors, runtime errors, fixes applied, and final score.

This builds a long-term training dataset that can be used for:
- Fine-tuning future LLMs on ForgeAI's domain
- Statistical analysis of failure causes
- Identifying which fix strategies succeed
- Tracking improvement across V5 → V6 → V7

Storage: backend/failure_memory/dataset/  (one JSON file per run)
Index: backend/failure_memory/dataset_index.jsonl  (one line per run)
"""
import json
import hashlib
import time
from pathlib import Path
from dataclasses import dataclass, field

_DATASET_DIR = Path(__file__).parent.parent.parent / "failure_memory" / "dataset"
_INDEX_PATH = Path(__file__).parent.parent.parent / "failure_memory" / "dataset_index.jsonl"


@dataclass
class RunRecord:
    run_id: str
    timestamp: str
    version: str           # v5 | v6 | v7
    idea: str
    project_name: str
    provider: str
    plan: dict
    architecture: dict
    validation_errors: list[str]
    runtime_error: str | None
    runtime_error_type: str | None
    fixes_applied: list[dict]       # [{file, error_type, fix_strategy}]
    score: int
    grade: str
    runtime_passed: bool
    frontend_passed: bool
    generation_time: float
    # V6 extras
    security_score: int | None = None
    performance_score: int | None = None
    maintainability_score: int | None = None
    qa_score: int | None = None
    v6_score: int | None = None
    # V7 extras
    prompt_version: str = "default"
    active_rules: list[str] = field(default_factory=list)


def save_run_record(record: RunRecord) -> str:
    """Persist a run record and update the index. Returns the run_id."""
    _DATASET_DIR.mkdir(parents=True, exist_ok=True)

    record_dict = {
        "run_id": record.run_id,
        "timestamp": record.timestamp,
        "version": record.version,
        "idea": record.idea,
        "project_name": record.project_name,
        "provider": record.provider,
        "plan": record.plan,
        "architecture": {
            "endpoint_count": len(record.architecture.get("api_endpoints", [])),
            "endpoints": record.architecture.get("api_endpoints", [])[:10],
            "db_schema_keys": list(record.architecture.get("db_schema", {}).keys()),
        },
        "validation_errors": record.validation_errors,
        "runtime_error": record.runtime_error,
        "runtime_error_type": record.runtime_error_type,
        "fixes_applied": record.fixes_applied,
        "score": record.score,
        "grade": record.grade,
        "runtime_passed": record.runtime_passed,
        "frontend_passed": record.frontend_passed,
        "generation_time": record.generation_time,
        "security_score": record.security_score,
        "performance_score": record.performance_score,
        "maintainability_score": record.maintainability_score,
        "qa_score": record.qa_score,
        "v6_score": record.v6_score,
        "prompt_version": record.prompt_version,
        "active_rules": record.active_rules,
    }

    record_path = _DATASET_DIR / f"{record.run_id}.json"
    record_path.write_text(
        json.dumps(record_dict, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    index_entry = {
        "run_id": record.run_id,
        "timestamp": record.timestamp,
        "version": record.version,
        "idea": record.idea[:80],
        "project_name": record.project_name,
        "score": record.score,
        "grade": record.grade,
        "runtime_passed": record.runtime_passed,
        "error_count": len(record.validation_errors),
        "runtime_error_type": record.runtime_error_type,
        "v6_score": record.v6_score,
    }
    with open(_INDEX_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(index_entry) + "\n")

    return record.run_id


def make_run_id(idea: str, version: str) -> str:
    """Generate a deterministic but unique run ID."""
    h = hashlib.sha256(f"{idea}{time.time()}".encode()).hexdigest()[:8]
    ts = time.strftime("%Y%m%d_%H%M%S")
    return f"{version}_{ts}_{h}"


def load_run_record(run_id: str) -> dict | None:
    record_path = _DATASET_DIR / f"{run_id}.json"
    if not record_path.exists():
        return None
    try:
        return json.loads(record_path.read_text(encoding="utf-8"))
    except Exception:
        return None


def get_index(limit: int = 1000) -> list[dict]:
    """Return the most recent N index entries."""
    if not _INDEX_PATH.exists():
        return []
    try:
        lines = _INDEX_PATH.read_text(encoding="utf-8").strip().splitlines()
        entries = [json.loads(line) for line in lines if line.strip()]
        return entries[-limit:]
    except Exception:
        return []


def get_dataset_stats() -> dict:
    """Aggregate statistics from the dataset index."""
    entries = get_index()
    if not entries:
        return {"total_runs": 0}

    total = len(entries)
    by_version = {}
    for e in entries:
        v = e.get("version", "unknown")
        by_version.setdefault(v, {"count": 0, "total_score": 0, "runtime_passed": 0})
        by_version[v]["count"] += 1
        by_version[v]["total_score"] += e.get("score", 0)
        if e.get("runtime_passed"):
            by_version[v]["runtime_passed"] += 1

    version_stats = {}
    for v, data in by_version.items():
        n = data["count"]
        version_stats[v] = {
            "count": n,
            "avg_score": round(data["total_score"] / n, 1),
            "pass_rate": round(data["runtime_passed"] / n, 3),
        }

    recent = entries[-10:]
    recent_scores = [e.get("score", 0) for e in recent]
    avg_recent = sum(recent_scores) / len(recent_scores) if recent_scores else 0

    return {
        "total_runs": total,
        "by_version": version_stats,
        "recent_avg_score": round(avg_recent, 1),
        "recent_pass_rate": round(sum(1 for e in recent if e.get("runtime_passed")) / len(recent), 3) if recent else 0,
        "top_error_types": _top_error_types(entries),
    }


def _top_error_types(entries: list[dict]) -> list[dict]:
    """Find the most common runtime_error_types across the dataset."""
    from collections import Counter
    error_types = [
        e.get("runtime_error_type") for e in entries
        if e.get("runtime_error_type")
    ]
    counts = Counter(error_types)
    return [
        {"type": k, "count": v}
        for k, v in counts.most_common(10)
    ]
