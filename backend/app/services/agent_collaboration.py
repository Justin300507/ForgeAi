"""
V6 Agent Collaboration Layer

Shared memory and decision log across all V6 agents.
Agents record their decisions and findings here; later agents read
prior decisions to make better-informed choices.

Example: Security Agent reads Tech Lead's auth requirements to
verify they were implemented. QA Agent reads PM spec to validate
user stories. Code Review reads architecture decisions to spot
violations.
"""
import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class AgentDecision:
    agent: str
    decision: str
    rationale: str
    data: dict
    timestamp: float = field(default_factory=time.time)


class AgentCollaboration:
    """
    Shared state passed through the V6 pipeline.

    Agents write decisions as they complete their work.
    The orchestrator passes this object to every agent so they
    can read what prior agents decided.
    """

    def __init__(self):
        self._decisions: list[AgentDecision] = []
        self._shared_context: dict[str, Any] = {}

    def record_decision(
        self,
        agent: str,
        decision: str,
        rationale: str,
        data: dict | None = None,
    ) -> None:
        self._decisions.append(AgentDecision(
            agent=agent,
            decision=decision,
            rationale=rationale,
            data=data or {},
        ))

    def set_context(self, key: str, value: Any) -> None:
        """Store arbitrary shared data (e.g. the resolved auth scheme)."""
        self._shared_context[key] = value

    def get_context(self, key: str, default: Any = None) -> Any:
        return self._shared_context.get(key, default)

    def get_decisions_by_agent(self, agent: str) -> list[AgentDecision]:
        return [d for d in self._decisions if d.agent == agent]

    def get_latest_decision(self, agent: str) -> AgentDecision | None:
        decisions = self.get_decisions_by_agent(agent)
        return decisions[-1] if decisions else None

    def get_summary(self) -> list[dict]:
        return [
            {
                "agent": d.agent,
                "decision": d.decision,
                "rationale": d.rationale[:200],
                "data": d.data,
            }
            for d in self._decisions
        ]

    def detect_conflicts(self) -> list[dict]:
        """
        Identify disagreements between agents.
        Returns a list of conflict descriptions for the orchestrator to resolve.
        """
        conflicts = []

        tech_lead = self.get_latest_decision("tech_lead")
        security = self.get_latest_decision("security")

        if tech_lead and security:
            tl_data = tech_lead.data
            sec_data = security.data
            # If tech lead required auth but security found critical auth gaps
            if tl_data.get("auth_endpoints", 0) > 0 and sec_data.get("critical", 0) > 0:
                conflicts.append({
                    "type": "auth_gap",
                    "agents": ["tech_lead", "security"],
                    "description": (
                        f"Tech Lead required {tl_data['auth_endpoints']} authenticated endpoints "
                        f"but Security found {sec_data['critical']} critical auth issues"
                    ),
                    "resolution": "Re-run backend generation with explicit auth constraints",
                })

        qa = self.get_latest_decision("qa")
        pm = self.get_latest_decision("product_manager")
        if qa and pm:
            missing = qa.data.get("missing_features", [])
            if missing:
                conflicts.append({
                    "type": "feature_gap",
                    "agents": ["product_manager", "qa"],
                    "description": f"PM specified features not found by QA: {missing[:3]}",
                    "resolution": "Consider adding missing endpoints in fix loop",
                })

        return conflicts

    def save(self, project_path: str) -> None:
        """Persist the collaboration log to the project directory."""
        log_path = Path(project_path) / "v6_collaboration_log.json"
        try:
            log_data = {
                "decisions": self.get_summary(),
                "conflicts": self.detect_conflicts(),
                "shared_context_keys": list(self._shared_context.keys()),
            }
            log_path.write_text(
                json.dumps(log_data, indent=2, default=str),
                encoding="utf-8",
            )
        except Exception:
            pass

    @classmethod
    def load(cls, project_path: str) -> "AgentCollaboration":
        """Load a collaboration log from a previous run (for multi-run analysis)."""
        collab = cls()
        log_path = Path(project_path) / "v6_collaboration_log.json"
        if log_path.exists():
            try:
                data = json.loads(log_path.read_text(encoding="utf-8"))
                for d in data.get("decisions", []):
                    collab._decisions.append(AgentDecision(
                        agent=d["agent"],
                        decision=d["decision"],
                        rationale=d["rationale"],
                        data=d.get("data", {}),
                    ))
            except Exception:
                pass
        return collab
