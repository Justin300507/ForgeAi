"""
ForgeAI V5 Benchmark Suite — 100 app ideas across 10 categories.

Extends the original 50-app V3 suite with 50 more challenging apps
that stress-test: multi-tenant patterns, complex relationships, auth flows,
file uploads, real-time features, and domain-specific business logic.
"""

# Original 50 from V3 (imported to keep parity)
from benchmark.ideas import BENCHMARK_IDEAS as _V3_IDEAS

# 50 additional V5 ideas — harder, more complex
_V5_EXTRA_IDEAS = [
    # ── Advanced Productivity ────────────────────────────────────────────────
    "A kanban board with columns, cards, drag ordering, and team assignments",
    "A document version control system with files, revisions, diffs, and restore",
    "A wiki with pages, page hierarchy, edit history, and backlinks",
    "A shared calendar with events, recurring schedules, invitations, and reminders",
    "A goal-setting platform with OKRs, key results, check-ins, and progress tracking",

    # ── Advanced Social ──────────────────────────────────────────────────────
    "A Q&A platform with questions, answers, votes, and accepted answers",
    "A social network with profiles, friend requests, posts, and news feed",
    "A job board with job listings, companies, applications, and recruiter accounts",
    "A dating app profile system with profiles, matches, and message threads",
    "A neighborhood community app with posts, local events, and classified ads",

    # ── Advanced E-commerce ──────────────────────────────────────────────────
    "A multi-vendor marketplace with vendor dashboards, products, and revenue splits",
    "A flash sale platform with time-limited deals, stock countdown, and waitlists",
    "A gift registry with wishlists, purchased items, and thank-you tracking",
    "A loyalty rewards system with points, tiers, redemption, and purchase history",
    "A B2B procurement system with purchase orders, approvals, and vendor catalogs",

    # ── Advanced Finance ─────────────────────────────────────────────────────
    "A budget planning tool with envelopes, income sources, and projections",
    "A stock portfolio tracker with positions, dividends, and performance charts",
    "A loan calculator and tracker with amortization schedules and payment history",
    "A tax document organizer with categories, deductions, and year-end summaries",
    "A crowdfunding platform with campaigns, pledges, rewards, and backer updates",

    # ── Healthcare & Medical ─────────────────────────────────────────────────
    "A patient management system with appointments, medical history, and prescriptions",
    "A telemedicine platform with doctor profiles, consultations, and health records",
    "A fitness coaching app with client profiles, workout plans, and progress reviews",
    "A medication reminder system with prescriptions, doses, and adherence logs",
    "A mental wellness platform with mood tracking, therapy sessions, and coping tools",

    # ── Advanced Education ───────────────────────────────────────────────────
    "A coding challenge platform with problems, solutions, test cases, and rankings",
    "A virtual classroom with courses, live sessions, assignments, and grades",
    "A tutoring marketplace with tutor profiles, bookings, and session reviews",
    "A certification tracker with courses, exams, credentials, and expiry dates",
    "A school management system with students, classes, attendance, and parent portals",

    # ── SaaS & Developer Tools ───────────────────────────────────────────────
    "An API key management system with keys, rate limits, usage logs, and billing",
    "A feature flag service with flags, environments, rollout percentages, and audit logs",
    "A webhook management platform with endpoints, events, delivery logs, and retries",
    "A cron job scheduler with jobs, schedules, execution history, and alerting",
    "A monitoring dashboard with metrics, alerts, thresholds, and notification channels",

    # ── Real Estate & Property ───────────────────────────────────────────────
    "A property listing platform with listings, agents, inquiries, and virtual tours",
    "A rental management system with properties, tenants, leases, and maintenance requests",
    "A real estate CRM with leads, properties, showings, and deal pipelines",
    "A home services marketplace with providers, services, bookings, and reviews",
    "A construction project tracker with projects, milestones, materials, and contractors",

    # ── Travel & Hospitality ─────────────────────────────────────────────────
    "A travel review platform with destinations, reviews, photos, and recommendations",
    "A hostel management system with rooms, bookings, check-ins, and guest reviews",
    "A tour operator platform with tours, guides, bookings, and itineraries",
    "A car rental system with vehicles, reservations, pricing tiers, and damage reports",
    "A cruise booking system with ships, cabins, excursions, and passenger manifests",

    # ── Specialized Domains ──────────────────────────────────────────────────
    "A charity donation platform with causes, campaigns, donations, and impact reports",
    "A volunteer management system with opportunities, sign-ups, hours, and recognition",
    "A legal case management system with cases, clients, documents, and billing hours",
    "A pet care platform with pet profiles, vet visits, vaccinations, and grooming logs",
    "A sports league management app with teams, players, schedules, and standings",
]

# Combined 100-app suite
BENCHMARK_IDEAS_V5 = _V3_IDEAS + _V5_EXTRA_IDEAS

# ── Category groupings for leaderboard analysis ──────────────────────────────

CATEGORIES = {
    "Productivity": list(range(0, 5)) + list(range(50, 55)),
    "Social / Content": list(range(5, 10)) + list(range(55, 60)),
    "E-commerce": list(range(10, 15)) + list(range(60, 65)),
    "Finance": list(range(15, 20)) + list(range(65, 70)),
    "Health & Fitness": list(range(20, 25)) + list(range(70, 75)),
    "Education": list(range(25, 30)) + list(range(75, 80)),
    "Business / SaaS": list(range(30, 35)) + list(range(80, 85)),
    "Inventory / Logistics": list(range(35, 40)) + list(range(85, 90)),
    "Events & Bookings": list(range(40, 45)) + list(range(90, 95)),
    "Media & Entertainment": list(range(45, 50)) + list(range(95, 100)),
}
