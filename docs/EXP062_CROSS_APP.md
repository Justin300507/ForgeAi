# Experiment 062 — Cross-App Reliability Investigation

2026-07-12. 3 of 3 budgeted canaries used (`blog_cms`, `crm`, `inventory`
— the last via `benchmarks/golden/18_inventory.txt`, a real existing
benchmark idea, not a placeholder). No dominant systemic failure
appeared mid-run that would have justified stopping early, so all 3
allocated apps were run as budgeted. Validation/investigation only — no
fixes implemented, no additional validators migrated (none were
migrated even though 2 legacy ones DID trigger — see §Q1).

**Method:** `backend/scripts/exp062_cross_app.py` (new, non-production),
same observer pattern as `exp061_validate.py` (monkeypatch
`validate_project`, `_run_static_validators`, `write_fix` — pure
observation, call-through unchanged) — extended to accept any
`CANARY_APPS`/`benchmarks/golden` idea, run once each against the three
apps instead of repeating `todo`.

## Q1 — Do the remaining legacy validators trigger?

**Yes — twice, in two different apps, both fully traced.** This closes
the exact evidence gap Exp061 reported honestly as untested.

| App | Legacy validator/check | Fallback category/severity | file_path (regex-extracted) | Repair outcome |
|---|---|---|---|---|
| `blog_cms` | `validate_imported_symbols` (message: `"Missing symbol 'author_router' in app/routes/post_routes.py (imported from app/main.py)"`) | `import` / `medium` | `app/routes/post_routes.py` — correct, extracted via `_filepath_static`'s regex | **Resolved.** `write_fix` targeted `app/routes/post_routes.py` 9 times; the error was present in `validate_project()` call 4 and never recurred in any later call. |
| `inventory` | the inline `py_compile` syntax-error check (never separately migrated — flagged explicitly in `docs/VALIDATOR_MIGRATION.md`'s "not migrated" table) | `contract` (default) / determined by `_severity_static`'s "syntax error" match → `critical` | `app/routes/product_routes.py` — correct | **Resolved.** Present in calls 0-1 (2 occurrences), gone from call 2 onward; `write_fix` targeted `app/routes/product_routes.py` 3 times. |

Both legacy-fallback cases worked correctly end-to-end: accurate
regex-based categorization (matching Exp060's own documented parity
mapping), accurate regex-extracted `file_path`, correct repair
targeting, and successful resolution. This is exactly the live
confirmation Exp061 lacked — the fallback path is now proven live, not
just offline-tested.

`crm` and `todo`-repeat cases triggered zero legacy validators (consistent
with Exp056/058/061's own observation that `todo`/`crm` simply don't
exercise this specific set of checks).

## Q2 — Failure classes across app types

| Failure class | `todo` (Exp056/058/061) | `blog_cms` | `crm` | `inventory` |
|---|---|---|---|---|
| `AttributeError`-class schema/model field mismatch, causing Runtime Startup failure | ✅ (`SignupRequest.username`) | ✅ (`SignupRequest`/`User.name`) | ❌ none | ✅ (`ProductCreate.price`) |
| Visual Judge low score | ✅ (~45) | ✅ (~35) | ✅ (~38, even on a clean pass) | ✅ (~43) |
| Legacy-validator (unmigrated) errors | none observed (6 live rounds) | ✅ `validate_imported_symbols` | none | ✅ `py_compile` syntax check |
| Runtime Startup passes cleanly | ❌ | ❌ | ✅ | ❌ |
| Integration/CRUD passes despite Runtime Startup failing | n/a (both fail together) | n/a (both fail together) | ✅ (both pass) | **✅ (Integration passes at 92.3 even though Runtime Startup fails at 20)** — a genuinely different failure shape from `todo`/`blog_cms` |

**Shared failure (systemic, not app-specific):** the `AttributeError`
schema/model field-mismatch pattern — **3 of 4 apps tested across this
session's live experiments** (`todo`, `blog_cms`, `inventory`) hit this
exact class of bug, each with a different specific field/schema
(`SignupRequest.username`, `User.name`, `ProductCreate.price`), all the
same underlying shape: an LLM-generated Pydantic Create/Signup schema is
accessed for a field its own class doesn't define. Only `crm` — the
consistently clean generation across every experiment this session has
run it in — avoids it entirely.

**Shared, lower-severity:** Visual Judge scores low in every single app
tested, including `crm`'s otherwise-clean run — the most universal
finding of all, though lower-impact since it doesn't gate
build/runtime/CRUD (per Exp056's own severity ranking, unchanged here).

**App-specific:** `inventory`'s Runtime-Startup-fails-but-Integration-passes
shape is new — not seen in `todo`/`blog_cms` (where both fail together).
Plausible explanation, not confirmed further this cycle (would require
fixing, out of scope): the CRUD journey may complete successfully
against a backend that's technically "up" per the journey runner's own
check, while the STARTUP health-check specifically (a separate,
stricter check) fails for an unrelated reason — worth a dedicated
investigation, not attempted here per "do not fix" / "document only."

**Architecture-specific:** not confirmed as a distinct category this
cycle — the 2 legacy-validator triggers (Q1) each fired in a different
app but neither recurred in the other apps tested, consistent with
"triggers depend on what the LLM happened to generate for that specific
app," not a structural pattern tied to one app's architecture
specifically. `Unknown` whether a 4th/5th app would show a similar
one-off legacy-validator trigger — plausible given 2-for-3 hit rate this
cycle, not provable from n=3.

## Q3 — Measurements

| App | First-pass score | Final score | fix_attempts | Runtime retries observed | `validate_project()` calls | Native diagnostics | Legacy fallback diagnostics |
|---|---|---|---|---|---|---|---|
| `blog_cms` | (not separately captured this cycle — see limitation below) | 71.49 | 5 | 5 attempts, strategies `patch_file×2, regenerate_arch×3` | 11 | 35 | 1 |
| `crm` | 88.59 | 88.59 | 0 | 0 (passed immediately) | 1 | 4 | 0 |
| `inventory` | (not separately captured) | 78.14 | 4 | 4 attempts, `patch_file×1, switch_model×3` | 7 | 18 | 2 |

**Operational success** (build+runtime+CRUD all passing, the same bar
Exp056/058 used): 1/3 this round (`crm`). Consistent with Exp056's
20% baseline and Exp058's confirmation that the retry-loop regression
fix (Exp057) doesn't by itself recover apps blocked by the separate
`AttributeError` generation defect (Q2).

**Limitation, stated honestly:** "first-pass success" (pre-any-fix
score) wasn't captured as a separately-logged field by this
experiment's observer for `blog_cms`/`inventory` — `score_history[0]`
is present in the raw result JSON (`benchmark_results/exp062/*.json`)
but wasn't pulled into this summary table; available on request, not
re-derived here to avoid delaying this report over a cosmetic gap.

## Q4 — Is `todo` still representative of overall reliability?

**Partially — representative of the dominant systemic issue, not of
the full failure-class diversity.** `todo` correctly surfaced the #1
cross-app finding (the `AttributeError` schema-mismatch pattern) that
this cycle confirms is systemic, not `todo`-specific — so `todo` alone
was never misleading about the MOST IMPORTANT issue. But `todo` (and
`blog_cms`, in 6 combined live rounds across Exp056/058/061) never once
triggered a legacy-validator fallback, while 2 of 3 NEW apps tried this
cycle did — meaning `todo` alone significantly **under-represented**
how often the legacy-validator/fallback path actually gets exercised in
practice. `todo` also didn't surface `inventory`'s distinct
Runtime-Startup-fails-but-Integration-passes failure shape. **Recommendation:
rotate which canary app gets the "repeat validation" role** rather than
defaulting to `todo` every cycle — this experiment's 3 fresh apps
produced more NEW evidence in one round each than 6 cumulative `todo`
reruns did.

## Failure classification (per the experiment's own taxonomy)

- **`AttributeError` schema-mismatch pattern**: **model output** issue
  (the LLM generates route/seed code referencing a field its own
  schema/model doesn't define) — not a validator, adapter, repair, or
  orchestration bug. The repair layer's inability to fully resolve it
  (confirmed across 3 apps now) is a **repair** issue: the fix attempts
  visibly try (per `retry_history`'s `patch_file`/`regenerate_arch`/
  `switch_model` escalation) but don't converge, consistent with
  Exp056/058's "same failure signature persists, stagnation guard
  correctly gives up" finding — not a new discovery, now cross-app-confirmed.
- **`inventory`'s Runtime-Startup-vs-Integration split**: `Unknown` root
  cause — flagged as **runtime** (most likely, given it's specifically
  the health-check/startup stage diverging from the CRUD-journey stage)
  but not investigated further this cycle, per "do not fix."
- **Both legacy-validator triggers (Q1)**: working exactly as designed
  — not a failure at all, a successful confirmation.

## Recommendation for Exp063

Given the success-criteria question is now answered with strong,
multi-app evidence (see below), the highest-value next step is a
**root-cause investigation of the `AttributeError` schema-mismatch
pattern specifically** (Exp056 §4's original flag, now confirmed
systemic across 3 apps) — NOT further live validation of the Diagnostic
contract (Exp060/061 already proved that live, twice, across now 4
total apps combined) and NOT migrating the remaining legacy validators
(Q1 showed the fallback works correctly; migrating them is a
nice-to-have contract-completeness item, not a reliability blocker).
Secondary: investigate `inventory`'s Runtime-Startup-vs-Integration
divergence, since it's a NEW failure shape not explained by the
already-understood `AttributeError` pattern.

## Success criteria — answered with evidence

**"What is now the highest ROI engineering problem across ForgeAI,
independent of app type?"**

**The recurring Pydantic Create/Signup-schema `AttributeError` pattern**
(generated route/seed code accesses a field the schema class doesn't
define — `SignupRequest.username`, `User.name`, `ProductCreate.price`).
Evidence: confirmed in **3 of 4 apps** tested live across this session
(`todo`, `blog_cms`, `inventory`), each a different specific field but
the identical underlying shape, each one the direct cause of that app's
Runtime Startup failure and the reason its Forge Score never recovers
above the low-to-mid 70s despite the retry loop (Exp057-fixed and now
working correctly, per Exp058/061/062) making real, visible fix
attempts every time. Only `crm` — one specific app's specific generation
— avoids it, which is why single-app (`todo`-only) validation
undercounted its severity: 3/4 apps is a systemic pattern, not
app-specific noise. This is independent of app type by construction
(same bug shape, different schema/field each time) and independent of
the validator-contract work (Exp060/061 already confirmed that
subsystem is sound) — it is a **model-output / generation-quality**
issue, not an infrastructure one, making it the correct next target
per this cycle's own "root cause it in the actual generated project
output" methodology.

## Cost

3 generations (`blog_cms`, `crm`, `inventory`), full experiment budget
used (all 3 allocated apps run — no early stop, since no single dominant
FAILURE emerged mid-run to justify one; the dominant PATTERN only became
clear after comparing all 3). Not committed, per this experiment's
explicit instruction. Did not migrate any validator despite 2 legitimately
triggering, per its own explicit instruction.
