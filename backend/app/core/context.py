"""
GenerationContext — the single shared state object threaded through every V15 stage.

Every stage reads from and writes to this. Immutable plan/architecture data is set once;
mutable verification/score data accumulates across the fix loop.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Optional


# ── Enumerations ──────────────────────────────────────────────────────────────

class StageStatus(str, Enum):
    PENDING  = "pending"
    RUNNING  = "running"
    PASSED   = "passed"
    FAILED   = "failed"
    SKIPPED  = "skipped"


class ErrorSeverity(str, Enum):
    CRITICAL = "critical"   # prevents compilation or startup
    HIGH     = "high"       # runtime crash
    MEDIUM   = "medium"     # feature broken
    LOW      = "low"        # code quality
    INFO     = "info"       # suggestion


class ErrorCategory(str, Enum):
    SYNTAX        = "syntax"
    IMPORT        = "import"
    RUNTIME       = "runtime"
    BROWSER       = "browser"
    API           = "api"
    DEPENDENCY    = "dependency"
    ARCHITECTURE  = "architecture"
    SECURITY      = "security"
    ENV_VAR       = "env_var"
    CONTRACT      = "contract"
    INTEGRATION   = "integration"
    REGRESSION    = "regression"


class FixStrategy(str, Enum):
    PATCH_FILE        = "patch_file"         # targeted line changes via LLM
    REGENERATE_FILE   = "regenerate_file"    # rewrite entire file
    REGENERATE_MODULE = "regenerate_module"  # rewrite related file group
    SWITCH_MODEL      = "switch_model"       # same prompt, stronger model
    REGENERATE_ARCH   = "regenerate_arch"    # redesign architecture + regen


# ── Diagnostic models ─────────────────────────────────────────────────────────

@dataclass
class Diagnostic:
    error_id:      str
    category:      ErrorCategory
    severity:      ErrorSeverity
    source:        str               # "static" | "runtime" | "browser" | "api"
    message:       str
    file_path:     Optional[str] = None
    line_number:   Optional[int] = None
    stack_trace:   Optional[str] = None
    fix_hint:      Optional[str] = None
    related_ids:   list[str]     = field(default_factory=list)
    metadata:      dict[str,Any] = field(default_factory=dict)

    @staticmethod
    def make_id() -> str:
        return uuid.uuid4().hex[:8]


@dataclass
class DiagnosticGroup:
    group_id:          str
    root_cause:        str
    diagnostics:       list[Diagnostic]
    affected_files:    list[str]
    suggested_strategy: FixStrategy
    priority:          int              # 1 = highest, fix first


@dataclass
class VerificationResult:
    stage:       str
    status:      StageStatus
    diagnostics: list[Diagnostic] = field(default_factory=list)
    duration_ms: float = 0.0
    metadata:    dict[str,Any] = field(default_factory=dict)

    @property
    def passed(self) -> bool:
        return self.status == StageStatus.PASSED

    @property
    def critical_count(self) -> int:
        return sum(1 for d in self.diagnostics if d.severity == ErrorSeverity.CRITICAL)


# ── Runtime / Browser result models ──────────────────────────────────────────

@dataclass
class RuntimeResult:
    success:         bool
    backend_started: bool = False
    health_passed:   bool = False
    stderr:          str  = ""
    stdout:          str  = ""
    crash_reason:    Optional[str] = None
    endpoint_results: dict[str, dict] = field(default_factory=dict)
    diagnostics:     list[Diagnostic] = field(default_factory=list)
    duration_ms:     float = 0.0


@dataclass
class BrowserTestResult:
    success:               bool
    page_loaded:           bool = False
    blank_page:            bool = False
    console_errors:        list[str] = field(default_factory=list)
    network_failures:      list[str] = field(default_factory=list)
    screenshots:           list[str] = field(default_factory=list)  # file paths or b64
    navigation_passed:     bool = False
    forms_tested:          int  = 0
    forms_passed:          int  = 0
    workflow_steps_passed: list[str] = field(default_factory=list)
    workflow_steps_failed: list[str] = field(default_factory=list)
    diagnostics:           list[Diagnostic] = field(default_factory=list)
    duration_ms:           float = 0.0
    skipped:               bool = False
    skip_reason:           str  = ""


# ── Scoring models ────────────────────────────────────────────────────────────

@dataclass
class ScoreDimension:
    name:    str
    score:   float   # 0–100
    weight:  float   # fraction; all weights sum to 1.0
    passed:  bool
    details: str = ""

    @property
    def weighted(self) -> float:
        return self.score * self.weight


@dataclass
class QualityScore:
    overall:          float                  # 0–100 weighted sum
    dimensions:       list[ScoreDimension]
    timestamp:        datetime
    attempt_number:   int
    deployment_ready: bool                   # overall >= DEPLOY_THRESHOLD

    @property
    def grade(self) -> str:
        s = self.overall
        if s >= 95: return "A+"
        if s >= 90: return "A"
        if s >= 80: return "B"
        if s >= 70: return "C"
        if s >= 60: return "D"
        return "F"

    def dim(self, name: str) -> Optional[ScoreDimension]:
        return next((d for d in self.dimensions if d.name == name), None)


# ── Fix attempt record ────────────────────────────────────────────────────────

@dataclass
class FixAttempt:
    attempt_number:     int
    strategy:           FixStrategy
    groups:             list[DiagnosticGroup]
    files_modified:     list[str]
    provider:           str
    model:              str
    duration_ms:        float
    score_before:       float
    score_after:        Optional[float] = None
    regression_detected: bool = False
    success:            bool = False


# ── Observability models ──────────────────────────────────────────────────────

@dataclass
class TimelineEvent:
    stage:       str
    status:      StageStatus
    started_at:  datetime
    finished_at: Optional[datetime] = None
    duration_ms: Optional[float]    = None
    metadata:    dict[str,Any]      = field(default_factory=dict)

    def finish(self, **meta):
        self.finished_at = datetime.utcnow()
        self.duration_ms = (self.finished_at - self.started_at).total_seconds() * 1000
        self.metadata.update(meta)


@dataclass
class TokenUsage:
    prompt_tokens:     int   = 0
    completion_tokens: int   = 0
    total_tokens:      int   = 0
    estimated_cost_usd: float = 0.0
    by_stage:          dict[str,int] = field(default_factory=dict)

    def add(self, stage: str, prompt: int, completion: int, cost: float = 0.0):
        self.prompt_tokens     += prompt
        self.completion_tokens += completion
        self.total_tokens      += prompt + completion
        self.estimated_cost_usd += cost
        self.by_stage[stage]   = self.by_stage.get(stage, 0) + prompt + completion


# ── Main shared context ───────────────────────────────────────────────────────

class GenerationContext:
    """
    Mutable shared state for one V15 generation run.
    Passed by reference through every stage and fix loop iteration.
    """

    DEPLOY_THRESHOLD = 95.0   # score required for deployment

    def __init__(
        self,
        job_id:       str,
        idea:         str,
        project_path: Path,
        project_name: str,
    ):
        self.job_id       = job_id
        self.idea         = idea
        self.project_path = project_path
        self.project_name = project_name

        # ── Generation outputs (set once by generation stages) ────────────
        self.plan:         Any = None
        self.architecture: Any = None

        # ── Verification state (accumulated per fix loop iteration) ───────
        self.static_results:  list[VerificationResult] = []
        self.runtime_result:  Optional[RuntimeResult]  = None
        self.browser_result:  Optional[BrowserTestResult] = None

        # ── Scoring ───────────────────────────────────────────────────────
        self.score_history:   list[QualityScore]        = []
        self.current_score:   Optional[QualityScore]    = None
        # snapshot of passing diagnostic IDs before a fix (for regression detection)
        self._passing_snapshot: set[str]                = set()

        # ── Fix history ───────────────────────────────────────────────────
        self.fix_attempts:    list[FixAttempt]          = []
        self.max_fix_attempts: int = 5

        # ── Observability ─────────────────────────────────────────────────
        self.timeline:        list[TimelineEvent]       = []
        self.token_usage:     TokenUsage                = TokenUsage()
        self.providers_used:  list[str]                 = []
        self.started_at:      datetime                  = datetime.utcnow()

        # ── Runtime processes & ports ─────────────────────────────────────
        self.backend_url:     Optional[str]             = None
        self.frontend_url:    Optional[str]             = None
        self.backend_port:    int                       = 8001
        self.frontend_port:   int                       = 5174

        # ── Provider rotation (for attempt 4: switch model) ───────────────
        self._provider_seq:   list[str]                 = ["groq","gemini","openrouter","cerebras"]
        self._provider_idx:   int                       = 0
        self.current_provider: str                      = "auto"

        # ── Deployment result ─────────────────────────────────────────────
        self.deployment_result: Optional[dict]          = None

    # ── Timeline helpers ──────────────────────────────────────────────────────

    def begin_stage(self, stage: str, **meta) -> TimelineEvent:
        evt = TimelineEvent(
            stage=stage, status=StageStatus.RUNNING,
            started_at=datetime.utcnow(), metadata=meta,
        )
        self.timeline.append(evt)
        return evt

    def end_stage(self, evt: TimelineEvent, status: StageStatus, **meta):
        evt.status = status
        evt.finish(**meta)

    # ── Diagnostics helpers ───────────────────────────────────────────────────

    def all_diagnostics(self) -> list[Diagnostic]:
        out: list[Diagnostic] = []
        for r in self.static_results:
            out.extend(r.diagnostics)
        if self.runtime_result:
            out.extend(self.runtime_result.diagnostics)
        if self.browser_result:
            out.extend(self.browser_result.diagnostics)
        return out

    def critical_errors(self) -> list[Diagnostic]:
        return [d for d in self.all_diagnostics() if d.severity == ErrorSeverity.CRITICAL]

    def has_critical(self) -> bool:
        return bool(self.critical_errors())

    # ── Regression protection helpers ─────────────────────────────────────────

    def snapshot_passing(self):
        """Record which diagnostics are currently absent (= test passing).
        Call this BEFORE a fix attempt."""
        self._passing_snapshot = {
            d.error_id for d in self.all_diagnostics()
        }

    def detect_regression(self) -> list[Diagnostic]:
        """Return diagnostics that were NOT present before the fix but are now.
        These represent regressions introduced by the fix."""
        current_ids = {d.error_id for d in self.all_diagnostics()}
        new_ids     = current_ids - self._passing_snapshot
        return [d for d in self.all_diagnostics() if d.error_id in new_ids]

    # ── Provider rotation ─────────────────────────────────────────────────────

    def next_provider(self) -> str:
        self._provider_idx = (self._provider_idx + 1) % len(self._provider_seq)
        p = self._provider_seq[self._provider_idx]
        self.current_provider = p
        return p

    def reset_provider(self):
        self._provider_idx = 0
        self.current_provider = "auto"

    # ── Score helpers ─────────────────────────────────────────────────────────

    @property
    def latest_score(self) -> float:
        return self.current_score.overall if self.current_score else 0.0

    @property
    def is_deployment_ready(self) -> bool:
        return self.latest_score >= self.DEPLOY_THRESHOLD

    def record_score(self, score: QualityScore):
        self.current_score = score
        self.score_history.append(score)

    # ── Summary for API response ──────────────────────────────────────────────

    def to_summary(self) -> dict:
        elapsed = (datetime.utcnow() - self.started_at).total_seconds()
        return {
            "job_id":         self.job_id,
            "idea":           self.idea,
            "project_name":   self.project_name,
            "forge_score":    self.latest_score,
            "grade":          self.current_score.grade if self.current_score else "F",
            "deployed":       bool(self.deployment_result and self.deployment_result.get("success")),
            "backend_url":    (self.deployment_result or {}).get("backend_url"),
            "frontend_url":   (self.deployment_result or {}).get("frontend_url"),
            "fix_attempts":   len(self.fix_attempts),
            "total_tokens":   self.token_usage.total_tokens,
            "estimated_cost": self.token_usage.estimated_cost_usd,
            "elapsed_s":      round(elapsed, 1),
            "providers_used": list(set(self.providers_used)),
            "timeline": [
                {
                    "stage":      e.stage,
                    "status":     e.status.value,
                    "duration_ms": round(e.duration_ms or 0, 1),
                }
                for e in self.timeline
            ],
            "score_history": [
                {"attempt": s.attempt_number, "score": s.overall, "grade": s.grade}
                for s in self.score_history
            ],
        }
