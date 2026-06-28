"""
Architecture Database

The single most important optimization for generation reliability.

Instead of asking the planner to invent a new architecture for every request:

  1. Classify the user's idea into an app category
  2. Check if ForgeAI has already built a successful app in this category
  3. If yes: start from that proven architecture (inject as template)
  4. If no: generate fresh, then record it for future use

Why this works:
  - Proven architectures skip the most common structural mistakes
  - The architect only needs to customize, not invent
  - Downstream validators have less to fix
  - Success rate improves because we're starting from known-good structure

Storage: backend/failure_memory/arch_db.json
         backend/failure_memory/arch_index.json  (category → best arch)
"""
from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional


_MEM_DIR    = Path(__file__).parent.parent.parent / "failure_memory"
_DB_PATH    = _MEM_DIR / "arch_db.json"
_INDEX_PATH = _MEM_DIR / "arch_index.json"


# ── App category taxonomy ──────────────────────────────────────────────────────

APP_CATEGORIES: dict[str, list[str]] = {
    "todo_crud":        ["todo", "task", "note", "list", "reminder", "checklist", "habit"],
    "expense_tracker":  ["expense", "budget", "finance", "spending", "money", "receipt", "transaction"],
    "ecommerce":        ["shop", "store", "product", "cart", "order", "checkout", "payment", "catalog"],
    "blog_cms":         ["blog", "article", "post", "content", "cms", "publish", "editor", "newsletter"],
    "auth_saas":        ["saas", "subscription", "dashboard", "user management", "admin panel", "tenant"],
    "social":           ["chat", "message", "friend", "follow", "like", "comment", "feed", "social"],
    "lms":              ["course", "lesson", "student", "teacher", "quiz", "learning", "education", "module"],
    "crm":              ["crm", "contact", "lead", "customer", "sales", "pipeline", "deal", "prospect"],
    "inventory":        ["inventory", "stock", "warehouse", "supply", "sku", "barcode", "asset"],
    "booking":          ["booking", "appointment", "reservation", "schedule", "calendar", "slot", "availability"],
    "analytics":        ["analytics", "metric", "chart", "report", "kpi", "insight", "dashboard"],
    "game":             ["game", "player", "score", "leaderboard", "achievement", "level", "multiplayer"],
    "health_fitness":   ["health", "workout", "fitness", "exercise", "diet", "calorie", "nutrition"],
    "project_mgmt":     ["project", "sprint", "ticket", "kanban", "milestone", "team", "roadmap"],
    "food_restaurant":  ["restaurant", "menu", "food", "recipe", "delivery", "kitchen", "meal"],
    "library_system":   ["library", "book", "borrow", "return", "isbn", "author", "catalog", "member"],
    "hr_payroll":       ["employee", "payroll", "hr", "leave", "attendance", "salary", "onboard"],
    "iot_monitoring":   ["sensor", "iot", "monitor", "alert", "device", "telemetry", "threshold"],
    "event_mgmt":       ["event", "ticket", "attendee", "conference", "venue", "registration", "speaker"],
    "real_estate":      ["property", "listing", "rent", "mortgage", "agent", "estate", "lease"],
}


def classify_idea(idea: str) -> str:
    """
    Classify a user idea string into one of the app categories.
    Uses keyword matching with scoring — the category with the most hits wins.
    Returns 'generic' if no category matches.
    """
    idea_lower = idea.lower()
    scores: dict[str, int] = {}
    for category, keywords in APP_CATEGORIES.items():
        score = sum(1 for kw in keywords if kw in idea_lower)
        if score > 0:
            scores[category] = score
    if not scores:
        return "generic"
    return max(scores, key=lambda c: scores[c])


# ── Data models ───────────────────────────────────────────────────────────────

@dataclass
class ArchRecord:
    category:      str
    idea:          str          # original idea that produced this architecture
    architecture:  dict         # the full ArchitecturePlan dict
    plan:          dict         # the ProjectPlan dict
    score:         float        # forge score achieved (0-100)
    success_count: int = 1      # times this arch has been successfully reused
    generation_id: str = ""
    timestamp:     str = ""

    def summary(self) -> str:
        entities = [e.get("name", "?") for e in self.architecture.get("database_schema", {}).get("entities", [])]
        endpoints = len(self.architecture.get("api_endpoints", []))
        return (f"[{self.category}] score={self.score:.0f} "
                f"entities={entities[:4]} endpoints={endpoints}")


# ── Architecture Database ──────────────────────────────────────────────────────

class ArchDB:
    """
    Stores and retrieves successful architectures by app category.

    Usage:
        db = ArchDB()

        # Before generation — check for a proven template
        template = db.lookup(idea)
        if template:
            # inject template.architecture into architect prompt
            pass

        # After successful generation — record it
        db.record(idea, architecture_dict, plan_dict, score=97.2)
    """

    def __init__(self):
        self._records: dict[str, list[dict]] = {}   # category → [ArchRecord dict, ...]
        self._index:   dict[str, dict]        = {}   # category → best ArchRecord dict
        self._load()

    def _load(self):
        if _DB_PATH.exists():
            try:
                self._records = json.loads(_DB_PATH.read_text(encoding="utf-8"))
            except Exception:
                self._records = {}
        if _INDEX_PATH.exists():
            try:
                self._index = json.loads(_INDEX_PATH.read_text(encoding="utf-8"))
            except Exception:
                self._index = {}

    def _save(self):
        _MEM_DIR.mkdir(parents=True, exist_ok=True)
        _DB_PATH.write_text(
            json.dumps(self._records, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        _INDEX_PATH.write_text(
            json.dumps(self._index, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    def record(
        self,
        idea:         str,
        architecture: dict,
        plan:         dict,
        score:        float,
        generation_id: str = "",
    ) -> str:
        """
        Store a successful architecture.
        Only stores if score >= 80 (we only want to reuse good architectures).
        Returns the category it was stored under.
        """
        if score < 80:
            return ""   # don't learn from low-quality outcomes

        category  = classify_idea(idea)
        ts        = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        gen_id    = generation_id or hashlib.sha256(idea[:100].encode()).hexdigest()[:10]

        record = ArchRecord(
            category=category,
            idea=idea[:200],
            architecture=architecture,
            plan=plan,
            score=score,
            generation_id=gen_id,
            timestamp=ts,
        )
        record_dict = asdict(record)

        # Keep last 5 records per category (rolling window)
        bucket = self._records.setdefault(category, [])
        bucket.append(record_dict)
        if len(bucket) > 5:
            bucket.pop(0)

        # Update index: best-scoring record per category
        best = self._index.get(category)
        if not best or record.score > best.get("score", 0):
            self._index[category] = record_dict

        self._save()
        print(f"  [arch_db] Stored arch for category '{category}' (score={score:.1f})")
        return category

    def lookup(self, idea: str, min_score: float = 85.0) -> Optional[ArchRecord]:
        """
        Look up the best proven architecture for an app idea.
        Returns None if no suitable template exists.
        """
        category = classify_idea(idea)
        if category == "generic":
            return None

        best = self._index.get(category)
        if not best:
            return None
        if best.get("score", 0) < min_score:
            return None

        try:
            rec = ArchRecord(**best)
            print(f"  [arch_db] Found proven template: {rec.summary()}")
            return rec
        except Exception:
            return None

    def build_template_injection(self, rec: ArchRecord) -> str:
        """
        Build a text block to inject into the architect prompt when reusing a template.
        Tells the LLM: "start from this structure, customise where needed."
        """
        entities = rec.architecture.get("database_schema", {}).get("entities", [])
        endpoints = rec.architecture.get("api_endpoints", [])
        files = rec.architecture.get("folder_structure", {}).get("backend_files", [])

        entities_txt  = "\n".join(f"  - {e.get('name', '?')}: {e.get('fields', [])}" for e in entities[:8])
        endpoints_txt = "\n".join(f"  - {ep.get('method','GET')} {ep.get('path','/')} → {ep.get('file','')}" for ep in endpoints[:15])
        files_txt     = "\n".join(f"  - {f}" for f in files[:15])

        return f"""
ARCHITECTURE TEMPLATE (from a previously verified {rec.category} app, score={rec.score:.0f}):
You SHOULD reuse this structure as your starting point. Adapt it to the user's specific requirements.
Do NOT reinvent the database schema or file structure if the template already covers those needs.

DATABASE ENTITIES:
{entities_txt}

API ENDPOINTS:
{endpoints_txt}

BACKEND FILES:
{files_txt}

Original idea this was based on: "{rec.idea}"
"""

    def lookup_top_k(self, idea: str, k: int = 5, min_score: float = 75.0) -> list[ArchRecord]:
        """
        Architecture Evolution: retrieve the top-K most relevant proven architectures.

        Scoring logic:
          - +10 if same category
          - +score/10 for the arch's forge score
          - +1 per shared keyword with the idea

        Returns up to k ArchRecord objects sorted by relevance (best first).
        """
        category = classify_idea(idea)
        idea_lower = idea.lower()
        scored: list[tuple[float, ArchRecord]] = []

        for cat, records in self._records.items():
            for rec_dict in records:
                if rec_dict.get("score", 0) < min_score:
                    continue
                try:
                    rec = ArchRecord(**rec_dict)
                except Exception:
                    continue

                relevance = 0.0
                if cat == category:
                    relevance += 10.0

                # Multi-dimensional composite score (not just forge_score)
                # Weights: score(40%), deploy(25%), crud(15%), fixes(10%), endpoints(5%), cost(5%)
                rec_dict = asdict(rec)
                forge_s    = rec.score * 0.40
                deploy_s   = (25.0 if rec_dict.get("deployment_success") else 0.0)
                crud_s     = (15.0 if rec_dict.get("crud_passed") else 0.0)
                fix_pen    = max(0, 10.0 - rec_dict.get("fix_count", 0) * 2)
                ep_s       = rec_dict.get("endpoint_pass_rate", 1.0) * 5.0
                composite  = (forge_s + deploy_s + crud_s + fix_pen + ep_s) / 10.0
                relevance += composite

                # Keyword overlap between this idea and the stored idea
                stored_lower = rec.idea.lower()
                cat_keywords = APP_CATEGORIES.get(cat, [])
                overlap = sum(1 for kw in cat_keywords if kw in idea_lower and kw in stored_lower)
                relevance += overlap

                scored.append((relevance, rec))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [rec for _, rec in scored[:k]]

    def build_evolution_context(self, idea: str, k: int = 5) -> str:
        """
        Architecture Evolution: build a rich context block from top-K proven architectures.

        Instead of copying one template, the LLM sees patterns that worked across
        multiple similar projects and can synthesize the best combination.
        """
        records = self.lookup_top_k(idea, k=k)
        if not records:
            return ""

        lines = [
            f"ARCHITECTURE EVOLUTION CONTEXT",
            f"The following {len(records)} proven architectures are most relevant to this idea.",
            f"Extract the patterns that appear repeatedly — those are the most reliable choices.",
            f"",
        ]

        for i, rec in enumerate(records, 1):
            endpoints = rec.architecture.get("api_endpoints", [])
            entities = rec.architecture.get("database_schema", {})
            if isinstance(entities, dict):
                entities = entities.get("entities", []) or entities.get("tables", [])

            ep_lines = [
                f"    {ep.get('method','?')} {ep.get('path','/')}"
                for ep in endpoints[:10]
            ]
            ent_names = [e.get("name", e.get("table_name", "?")) for e in (entities or [])[:6]]

            lines += [
                f"  [{i}] {rec.category} — score={rec.score:.0f} — \"{rec.idea[:60]}\"",
                f"      Entities: {', '.join(ent_names) or 'none'}",
                f"      Endpoints ({len(endpoints)} total):",
            ]
            lines += ep_lines
            lines.append("")

        lines += [
            "INSTRUCTION: Design an architecture that incorporates the strongest patterns",
            "from the above. Prefer endpoint structures and entity relationships that",
            "appear in multiple proven examples. Do not copy any single example verbatim;",
            "synthesize the best combination for the current idea.",
        ]
        context = "\n".join(lines)
        print(f"  [arch_db] Evolution context: {len(records)} similar apps found")
        return context

    def stats(self) -> dict:
        return {
            "categories_with_templates": len(self._index),
            "total_records": sum(len(v) for v in self._records.values()),
            "best_per_category": {
                cat: {"score": rec.get("score", 0), "idea": rec.get("idea", "")[:60]}
                for cat, rec in self._index.items()
            },
        }


# ── Singleton ──────────────────────────────────────────────────────────────────
arch_db = ArchDB()
