## Development Workflow

For architecture or multi-file tasks:

1. Consult the Graphify knowledge graph first.
2. Use Serena for symbol-aware navigation.
3. Use Context7 for framework-specific documentation.
4. Implement changes.
5. Verify using Playwright and the validation pipeline.
6. Run Security Review before considering the task complete.

Prefer understanding the architecture before editing files.
# ForgeAI Development Guide

## Mission

ForgeAI is an AI Software Engineering platform that generates complete, production-ready full-stack applications from natural language.

The objective is reliability, correctness, maintainability, and beautiful UX.

Never generate placeholder implementations.

---

# Project Reference

## Running the Server

```bash
cd backend
$env:PYTHONIOENCODING = "utf-8"   # required for Cerebras/Unicode output on Windows
.\venv\Scripts\activate
uvicorn main:app --reload
```

Server runs on `http://localhost:8000` (FastAPI app, currently v19.0). The
codebase has accumulated many generation endpoints (`/project` through
`/project/v15`) from successive rewrites — **`/project/v15` is the current,
live pipeline** (`app/core/pipeline.py`'s `V15Pipeline`); older versions are
kept only as historical fallback, not under active development. Other
live endpoints: `/jobs` (async job queue, V19), `/queue` (worker-queue
REST API), `/deploy/{github,railway,cloudflare}`, `/health`.

## Required Environment Variables

Create `backend/.env`:
```
GEMINI_API_KEY=...
GROQ_API_KEY=...
CEREBRAS_API_KEY=...
# Optional, not part of the live auto-fallback chain:
OPENROUTER_API_KEY=...
DEEPSEEK_API_KEY=...
OPENAI_API_KEY=...
```

`app/providers/ai_provider.py`'s `auto` mode chain is **Cerebras (main)
→ Gemini (with retries) → Groq** — Cerebras re-added 2026-07-12 with a
fresh key (confirmed working via direct smoke test) and made the primary
leg specifically to conserve Gemini/Groq's daily free-tier quota, which
both run close to most days; Gemini/Groq are the fallback for when
Cerebras itself fails or cools down. OpenRouter/DeepSeek/OpenAI are still
not part of the auto chain. The response cache (`FORGE_LLM_CACHE`,
default on) checks every call regardless of provider before any of this
runs — an identical prompt costs $0 tokens on any leg.

## V15 Generation Pipeline (`app/core/pipeline.py`, `V15Pipeline`)

1. **Generation** — V6 multi-agent team (Product Manager → Architect → Tech
   Lead review → parallel Backend + Frontend generation)
2. **Deterministic Patch** — rule-based fixes applied before any LLM fix call
   (e.g. passlib→bcrypt, async/sync mismatches, DB engine wiring)
3. **Verify & Score** — `app/verification/engine.py`'s `VerificationEngine`
   runs static validators, the (warn-only) `ContractConformanceValidator`,
   runtime startup + endpoint smoke tests, the CRUD user-journey runner
   (`app/runtime/user_journey_runner.py`), the confidence engine
   (`app/confidence/engine.py`), and computes the weighted Forge Score
   (Runtime Startup 20%, API Functionality 15%, Frontend Load, Browser UX,
   Integration, Code Quality, Security, Completeness, Performance, Visual
   Judge — see any recent `experiments.md` entry for the current weights)
4. **Repair loop** — up to N fix attempts via `app/repair/` (preflight
   patcher, orchestrator, retry manager), stops early on "failure signature
   unchanged"
5. **Deploy** (optional, default off in benchmarks) — GitHub push +
   Render/Cloudflare, gated by `FORGE_DEPLOY_THRESHOLD`

## Reliability / Experiment Workflow

This is an active optimization project, not just a generator — see
`experiments.md` for the full paper trail. The loop in force:

1. Read latest telemetry (`backend/failure_memory/generation_log.jsonl`,
   `patterns.json`, canary logs) — find the biggest real failure category.
2. Root-cause it in the actual generated project output, not just the log.
3. Implement exactly one fix, verify locally (static checks / local repro),
   $0 cost.
4. Validate via `backend/scripts/run_canary.py` — a **fixed 3-app canary**
   (todo / blog_cms / crm, do not change without explicit sign-off) against
   the live `/project/v15` pipeline. `--no-deploy` unless deploy behavior is
   what's under test. `--provider gemini|groq` to force a provider past a
   quota confound.
5. Compare against the previous canary entry in
   `backend/benchmark_results/canary_history.json`, log the result honestly
   in `experiments.md` (including when a "regression" turns out to be
   unrelated LLM generation variance, not the change under test).
6. Commit only once the target improvement is confirmed or explicitly
   ruled inconclusive.

`run_forgebench.py` is the separate, heavier public ForgeBench suite runner
(multiple difficulty tiers) — reserve full-suite runs for milestone
checkpoints, not every cycle.

## Key File Locations

- `backend/main.py` — FastAPI app entry point, all endpoint registrations
- `backend/app/core/pipeline.py` — `V15Pipeline`, the live generation pipeline
- `backend/app/verification/engine.py` — `VerificationEngine`, scoring + validators
- `backend/app/providers/ai_provider.py` — provider dispatch, auto-fallback chain, cost tracking, LLM response cache
- `backend/app/repair/preflight.py` — deterministic pre-repair patchers (config attrs, import fixes, etc.)
- `backend/app/repair/orchestrator.py` — LLM-driven fix loop, snapshot/revert
- `backend/app/confidence/engine.py` — deployment-confidence scoring from `GenerationContext`
- `backend/app/runtime/user_journey_runner.py` — CRUD user-journey smoke test (register→login→create→edit→delete→verify)
- `backend/scripts/run_canary.py` — the 3-app (todo/blog_cms/crm) canary used for every reliability experiment
- `backend/failure_memory/` — telemetry: `generation_log.jsonl`, `patterns.json`, `arch_db.json`, `repair_db.json`
- `experiments.md` (repo root) — the experiment log; read the latest entries before starting new reliability work
- `generated_projects/` — output directory (git-ignored)
- `backend/llm_cache/` — cached LLM responses (git-ignored)

---

# General Rules

- Complete every requested feature fully.
- Never leave TODOs, stubs, fake implementations, or placeholder code.
- Preserve existing architecture unless explicitly instructed otherwise.
- Prefer modifying existing code over creating duplicate implementations.
- Keep functions focused and modular.
- Minimize technical debt.

---

# Planning

For complex tasks:

1. Analyze the existing implementation.
2. Produce an implementation plan.
3. Execute the plan.
4. Verify correctness.
5. Refactor if needed.

Always use the Superpowers planning workflow.

---

# Context7

Before writing code using:

- React
- FastAPI
- Vite
- Tailwind
- Next.js
- Python libraries

Consult Context7 for current APIs and best practices.

Avoid outdated syntax.

---

# Serena

For large refactors:

- Use Serena for symbol navigation.
- Prefer symbol-aware edits over text search.
- Rename symbols safely.
- Understand project structure before editing.

---

# UI

Always use UI/UX Pro Max.

Requirements:

- Premium SaaS quality
- Responsive
- Accessible
- Dark mode support
- Beautiful spacing
- Consistent typography
- Smooth animations
- Proper loading states
- Proper empty states
- Professional color palette

---

# Testing

After frontend changes:

Use Playwright.

Verify:

- navigation
- buttons
- forms
- responsive layouts
- browser console
- runtime errors

Fix discovered issues before completion.

---

# Code Quality

Always:

- simplify duplicated logic
- review security
- verify builds
- remove dead code

Never introduce unnecessary complexity.

---

# Completion Checklist

Before declaring a task complete:

- Builds successfully
- No lint errors
- No type errors
- No runtime errors
- Playwright passes
- UI polished
- Architecture maintained

## graphify

This project has a knowledge graph at graphify-out/ with god nodes, community structure, and cross-file relationships.

Rules:
- For codebase questions, first run `graphify query "<question>"` when graphify-out/graph.json exists. Use `graphify path "<A>" "<B>"` for relationships and `graphify explain "<concept>"` for focused concepts. These return a scoped subgraph, usually much smaller than GRAPH_REPORT.md or raw grep output.
- If graphify-out/wiki/index.md exists, use it for broad navigation instead of raw source browsing.
- Read graphify-out/GRAPH_REPORT.md only for broad architecture review or when query/path/explain do not surface enough context.
- After modifying code, run `graphify update .` to keep the graph current (AST-only, no API cost).
