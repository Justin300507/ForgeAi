"""
Component Reliability Database

Tracks which reusable components (JWT auth, file upload, WebSocket, pagination, etc.)
appear in generated projects and how often they succeed vs fail.

Over time this tells the planner:
  "JWT Auth has 99.1% success rate — prefer it."
  "Payment integration has 73% — warn the architect to keep it simple."

Usage:
    from app.knowledge.component_db import component_db

    # After a project run, scan what was used and record the outcome
    component_db.record_run(project_path, success=True, forge_score=92.0)

    # Before generation, get reliability data to inject into the planner prompt
    context = component_db.build_planner_context(idea)
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional


_MEM_DIR = Path(__file__).parent.parent.parent / "failure_memory"
_DB_PATH  = _MEM_DIR / "component_db.json"


# ── Component detection signatures ────────────────────────────────────────────

COMPONENT_SIGNATURES: dict[str, list[str]] = {
    "jwt_auth":       ["OAuth2PasswordBearer", "create_access_token", "jwt.encode", "JWT"],
    "bcrypt_hash":    ["bcrypt", "get_password_hash", "verify_password", "checkpw"],
    "file_upload":    ["UploadFile", "File(", "multipart", "shutil.copyfileobj"],
    "websocket":      ["WebSocket", "WebSocketDisconnect", "websocket.accept"],
    "pagination":     ["skip: int", "limit: int", "offset", "page: int"],
    "search_filter":  ["q: str", "search", "ilike", "contains", "filter("],
    "email_send":     ["smtplib", "sendgrid", "MIMEText", "smtp.sendmail"],
    "chat":           ["message", "conversation", "chat_room", "participants"],
    "payment":        ["stripe", "Stripe(", "checkout", "payment_intent"],
    "admin_panel":    ["is_admin", "role == 'admin'", "admin_router", "superuser"],
    "rate_limiting":  ["RateLimiter", "slowapi", "rate_limit", "limiter"],
    "background_job": ["BackgroundTasks", "background_tasks.add_task", "celery", "arq"],
    "soft_delete":    ["is_deleted", "deleted_at", "is_active", "archived"],
    "audit_log":      ["audit_log", "created_by", "updated_by", "action_log"],
    "image_resize":   ["PIL", "Pillow", "Image.open", "thumbnail"],
    "cache":          ["redis", "memcache", "lru_cache", "cache_key"],
    "docker_ready":   ["Dockerfile", "EXPOSE", "CMD uvicorn", "docker-compose"],
    "env_config":     ["os.getenv", "dotenv", "settings.py", "BaseSettings"],
}


# ── Data models ───────────────────────────────────────────────────────────────

@dataclass
class ComponentStats:
    name:          str
    seen:          int   = 0
    successes:     int   = 0    # runs where forge_score >= 80 AND runtime passed
    failures:      int   = 0    # runs where the app failed
    avg_score:     float = 0.0
    last_seen:     str   = ""

    @property
    def success_rate(self) -> float:
        return round(self.successes / self.seen, 3) if self.seen else 0.0

    @property
    def reliability_pct(self) -> str:
        return f"{self.success_rate * 100:.1f}%"


class ComponentDB:
    """
    Tracks per-component success/failure rates across all generated projects.
    """

    def __init__(self):
        self._data: dict[str, dict] = {}
        self._load()

    def _load(self):
        if _DB_PATH.exists():
            try:
                self._data = json.loads(_DB_PATH.read_text(encoding="utf-8"))
            except Exception:
                self._data = {}

    def _save(self):
        _MEM_DIR.mkdir(parents=True, exist_ok=True)
        _DB_PATH.write_text(json.dumps(self._data, indent=2), encoding="utf-8")

    def detect_components(self, project_path: str) -> list[str]:
        """
        Scan the generated project's Python and JSX files to detect which
        components are present. Returns a list of component names.
        """
        root = Path(project_path)
        detected: set[str] = set()
        text_buf: list[str] = []

        # Scan backend Python files
        for py_file in root.rglob("*.py"):
            try:
                text_buf.append(py_file.read_text(encoding="utf-8", errors="replace"))
            except Exception:
                pass

        # Scan frontend JSX/JS files
        for jsx_file in root.rglob("*.jsx"):
            try:
                text_buf.append(jsx_file.read_text(encoding="utf-8", errors="replace"))
            except Exception:
                pass

        combined = "\n".join(text_buf)

        for component, sigs in COMPONENT_SIGNATURES.items():
            if any(sig in combined for sig in sigs):
                detected.add(component)

        # Always count basic CRUD as detected (every generated app has it)
        detected.add("crud_rest")

        return sorted(detected)

    def record_run(
        self,
        project_path: str,
        success: bool,
        forge_score: float = 0.0,
        components: Optional[list[str]] = None,
    ) -> list[str]:
        """
        After a project run, detect and record each component's outcome.
        Returns the list of detected component names.
        """
        if components is None:
            try:
                components = self.detect_components(project_path)
            except Exception:
                components = ["crud_rest"]

        ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        succeeded = success and forge_score >= 80

        for comp in components:
            entry = self._data.setdefault(comp, {
                "name": comp, "seen": 0, "successes": 0, "failures": 0,
                "total_score": 0.0, "last_seen": "",
            })
            entry["seen"] += 1
            entry["total_score"] = entry.get("total_score", 0.0) + forge_score
            entry["last_seen"] = ts
            if succeeded:
                entry["successes"] += 1
            else:
                entry["failures"] += 1
            entry["avg_score"] = round(entry["total_score"] / entry["seen"], 1)

        self._save()
        return components

    def get_stats(self) -> list[ComponentStats]:
        """Return all component stats sorted by success rate (best first)."""
        stats = []
        for name, d in self._data.items():
            seen = d.get("seen", 0)
            if seen == 0:
                continue
            stats.append(ComponentStats(
                name=name,
                seen=seen,
                successes=d.get("successes", 0),
                failures=d.get("failures", 0),
                avg_score=d.get("avg_score", 0.0),
                last_seen=d.get("last_seen", ""),
            ))
        stats.sort(key=lambda s: s.success_rate, reverse=True)
        return stats

    def build_planner_context(self, idea: str) -> str:
        """
        Build a context block for the planner prompt showing which components
        are reliable and which are risky.

        The planner can use this to prefer proven patterns and warn about risky ones.
        """
        stats = self.get_stats()
        if not stats:
            return ""

        # Only include components with enough data (≥3 seen)
        qualified = [s for s in stats if s.seen >= 3]
        if not qualified:
            return ""

        reliable = [s for s in qualified if s.success_rate >= 0.90]
        risky    = [s for s in qualified if s.success_rate < 0.75]

        lines = ["COMPONENT RELIABILITY DATA (from ForgeAI's history):"]

        if reliable:
            lines.append("  High reliability (prefer these patterns):")
            for s in reliable[:6]:
                lines.append(f"    {s.name:<20} {s.reliability_pct:>6}  ({s.seen} apps)")

        if risky:
            lines.append("  Lower reliability (simplify or avoid):")
            for s in risky[:4]:
                lines.append(f"    {s.name:<20} {s.reliability_pct:>6}  ({s.seen} apps)")

        lines += [
            "",
            "When designing this app, prefer components with high reliability scores.",
            "For risky components, choose the simplest possible implementation.",
        ]
        return "\n".join(lines)

    def leaderboard(self) -> dict:
        stats = self.get_stats()
        return {
            "components": [
                {
                    "name": s.name,
                    "seen": s.seen,
                    "success_rate": s.success_rate,
                    "reliability_pct": s.reliability_pct,
                    "avg_score": s.avg_score,
                }
                for s in stats
            ],
            "total_components_tracked": len(stats),
            "most_reliable": stats[0].name if stats else None,
            "least_reliable": stats[-1].name if stats else None,
        }


# ── Singleton ──────────────────────────────────────────────────────────────────
component_db = ComponentDB()
