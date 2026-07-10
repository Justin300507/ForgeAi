"""
Product Analysis Agent (deterministic).

Infers the product's audience, emotional tone, data density, device
priority, and posture (consumer vs enterprise) from the idea string —
the design brief every downstream planner reads. Per-category defaults
come from a hand-tuned table; a small set of keyword modifiers adjusts
them when the idea itself signals otherwise ("internal admin tool" is
enterprise even if the category is social).
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class ProductAnalysis:
    category_key: str
    audience: str          # who uses this, in one phrase
    tone: str              # emotional register the UI should carry
    posture: str           # consumer | prosumer | enterprise
    data_density: str      # low | medium | high
    device_priority: str   # mobile-first | desktop-first | balanced
    usage_frequency: str   # daily | weekly | occasional
    accessibility_note: str  # extra a11y emphasis beyond the baseline, if any


# Per-category analysis defaults. Keys match design_system.CATEGORIES.
_ANALYSIS: dict[str, dict] = {
    "fitness": dict(
        audience="individuals building a training habit",
        tone="energetic and motivating — progress should feel like a win",
        posture="consumer", data_density="medium", device_priority="mobile-first",
        usage_frequency="daily", accessibility_note="",
    ),
    "finance": dict(
        audience="people managing their own money carefully",
        tone="calm, precise, trustworthy — never playful with amounts",
        posture="prosumer", data_density="high", device_priority="desktop-first",
        usage_frequency="weekly", accessibility_note="",
    ),
    "productivity": dict(
        audience="focused individuals and small teams organizing work",
        tone="quiet confidence — the UI recedes so the work stands out",
        posture="prosumer", data_density="high", device_priority="desktop-first",
        usage_frequency="daily", accessibility_note="",
    ),
    "social": dict(
        audience="people connecting with a community",
        tone="warm and lively — activity should feel human, not clinical",
        posture="consumer", data_density="medium", device_priority="mobile-first",
        usage_frequency="daily", accessibility_note="",
    ),
    "crm": dict(
        audience="sales and account teams working a pipeline all day",
        tone="confident and professional — dense but never cluttered",
        posture="enterprise", data_density="high", device_priority="desktop-first",
        usage_frequency="daily", accessibility_note="",
    ),
    "booking": dict(
        audience="customers reserving a time slot and staff managing them",
        tone="reassuring and unambiguous — availability must read instantly",
        posture="consumer", data_density="medium", device_priority="balanced",
        usage_frequency="weekly", accessibility_note="",
    ),
    "ecommerce": dict(
        audience="shoppers browsing and owners tracking orders",
        tone="vibrant but trustworthy — product imagery leads",
        posture="consumer", data_density="high", device_priority="mobile-first",
        usage_frequency="weekly", accessibility_note="",
    ),
    "healthcare": dict(
        audience="patients and practitioners handling sensitive records",
        tone="calm, clinical, trustworthy — zero visual noise",
        posture="prosumer", data_density="medium", device_priority="desktop-first",
        usage_frequency="weekly",
        accessibility_note="Hold this app to the strictest accessibility bar: "
                           "larger touch targets, explicit text labels beside every "
                           "status color, and high-contrast text everywhere — its "
                           "users may be older, stressed, or using assistive tech.",
    ),
    "ai_saas": dict(
        audience="technical early adopters who notice craft",
        tone="futuristic but precise — beautiful motion, no gimmicks",
        posture="prosumer", data_density="medium", device_priority="desktop-first",
        usage_frequency="daily", accessibility_note="",
    ),
    "restaurant": dict(
        audience="hungry guests deciding in seconds, staff managing service",
        tone="warm and immersive — appetite appeal over information density",
        posture="consumer", data_density="low", device_priority="mobile-first",
        usage_frequency="occasional", accessibility_note="",
    ),
    "travel": dict(
        audience="trip planners dreaming first, organizing second",
        tone="adventurous and editorial — imagery and place names lead",
        posture="consumer", data_density="low", device_priority="mobile-first",
        usage_frequency="occasional", accessibility_note="",
    ),
    "education": dict(
        audience="learners who need encouragement and clear next steps",
        tone="friendly and readable — progress is celebrated, never graded harshly",
        posture="consumer", data_density="medium", device_priority="balanced",
        usage_frequency="daily", accessibility_note="",
    ),
    "portfolio": dict(
        audience="visitors judging the owner's taste in under a minute",
        tone="refined and typographic — restraint is the statement",
        posture="consumer", data_density="low", device_priority="balanced",
        usage_frequency="occasional", accessibility_note="",
    ),
}

# Idea-level signals that override category defaults.
_ENTERPRISE_SIGNALS = ("enterprise", "internal tool", "admin panel", "back office",
                       "team dashboard", "b2b", "operations")
_MOBILE_SIGNALS = ("mobile app", "on the go", "pwa", "phone")


def analyze_product(idea: str, category_key: str) -> ProductAnalysis:
    base = _ANALYSIS.get(category_key) or _ANALYSIS["productivity"]
    idea_lower = idea.lower()

    posture = base["posture"]
    data_density = base["data_density"]
    device_priority = base["device_priority"]
    if any(sig in idea_lower for sig in _ENTERPRISE_SIGNALS):
        posture = "enterprise"
        data_density = "high"
    if any(sig in idea_lower for sig in _MOBILE_SIGNALS):
        device_priority = "mobile-first"

    return ProductAnalysis(
        category_key=category_key,
        audience=base["audience"],
        tone=base["tone"],
        posture=posture,
        data_density=data_density,
        device_priority=device_priority,
        usage_frequency=base["usage_frequency"],
        accessibility_note=base["accessibility_note"],
    )
