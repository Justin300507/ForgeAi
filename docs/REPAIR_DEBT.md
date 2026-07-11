# ForgeAI Repair Pipeline — Reliability Debt Report

Experiment 051 (Reliability Debt Audit), 2026-07-11. Read-only
investigation, $0, no generation, no LLM calls, no prompt changes. Ranked
by engineering risk (highest first). Every finding cites file:line or is
explicitly marked as a recommendation rather than a fact.

**Rule followed throughout:** facts are things the code demonstrably does;
recommendations are judgment calls about what to do about them. They are
kept in visually separate blocks below — a **Fact** line never contains an
opinion, a **Recommendation** line never claims to be proven.

---

## Risk 1 (Critical) — Test coverage: 8 of 114 repair functions have any test at all

**Fact:** across the two largest repair surfaces —
`deterministic_patcher.py` (66 functions matching `^def _?patch_`) and the
combined set of `preflight.py` + `database_patcher.py` +
`deployed_fixer.py` + `deployment_fix_service.py` + `file_writer_service.py`
+ `frontend_service.py` + `project_service.py` + `runtime_fix_service.py`
+ `json_cleaner.py` (47 functions matching `^def _?(patch|fix)_`) — a
per-function grep of `backend/tests/` for the function's own name found
**8 functions with any test reference, out of 114 (7.1%)**:

| Function | Test file |
|---|---|
| `_patch_broken_template_literal_classname` | `tests/reliability/test_broken_template_literal_classname.py` |
| `_patch_forward_role_to_duplicate_registrars` | `tests/reliability/test_role_forward_patcher.py` |
| `_patch_invalid_lucide_icons` | `tests/design_intelligence/test_icon_validity.py` |
| `_patch_missing_create_update_fields` | `tests/reliability/test_missing_create_update_fields.py` (+2 others) |
| `_patch_ownership_fk_attribute_drift` | `tests/reliability/test_ownership_fk_drift.py` |
| `_patch_response_schema_inherited_required_fields` | `tests/reliability/test_response_schema_inheritance.py` |
| `_patch_response_schemas_optional` | `tests/reliability/test_response_schema_inheritance.py` |
| `_patch_router_names` | `tests/reliability/test_prevention_rate.py` (indirect — tests the *category* rollup, not the patcher's own logic) |

All 8 of these were added in the last ~2 weeks of reliability-focused
experiments (047-049 and a handful before). Every patcher that predates
this cycle — including load-bearing ones like `_patch_strip_relationships`,
`_patch_auth_routes`, `_patch_database_py` (all 17 `preflight.py` fixes),
`_patch_deduplicate_models`/`_patch_deduplicate_schemas`,
`_patch_pagination_component` — has **zero** direct test coverage.

**Why Critical:** this session's own three experiments (048, 049, and the
retroactive corpus check in 048) each found real bugs specifically because
someone *manually* re-ran a corpus check after the fact. A regex patcher
with zero tests can regress silently for weeks — exactly the
Experiment-047 lesson ("the report's 7.5% figure was measuring stale
pre-fix output") in reverse: without tests, nobody would know a patcher
broke until corpus telemetry happens to surface it days or weeks later.

**Recommendation** *(not a fact — a judgment call)*: do not attempt to
backfill 105 tests in one pass. Prioritize by blast radius: the
`preflight.py` registry (17 functions, fails soft per-fix so a broken one
is silently invisible — see Risk 3) and the relationship/FK family in
`deterministic_patcher.py` (silent data-isolation or startup-crash risk if
wrong) are the highest-value targets for a future test-writing cycle, not
the frontend cosmetic patchers.

---

## Risk 2 (High) — `repair_project()` duplicates the main generation flow's 3-stage repair pattern

**Fact:** `services/v6_orchestrator.py` calls `run_deterministic_patches`
from 6 distinct locations (see `REPAIR_GRAPH.md` §6). Three of them (lines
257, 639, 795) live in the main generation flow; the other three (lines
1008, 1161, 1188) live inside `repair_project()`, a separate top-level
function for repair-only mode (skip generation). The stage semantics are
identical pairwise:

| Stage | Main flow | `repair_project()` |
|---|---|---|
| Initial pass | line 257 | line 1008 |
| After arch-repair injection (`skip_protected_injections=True`) | line 639 | line 1161 |
| After each LLM runtime fix | line 795 | line 1188 |

**Why High, not Critical:** this is duplicated *structure*, not duplicated
*bugs* — each site currently does the right thing independently. The risk
is drift: a future fix to one flow's ordering/comment (e.g. tightening
what runs after an arch-repair injection) has no structural forcing
function to also apply it to the other flow. `run_frontend_patches`
already suffered exactly this class of drift once (see `REPAIR_GRAPH.md`
§3's docstring quote — two frontend patchers were invisible to the
standalone resync path until that particular drift was fixed by routing
both call sites through one function).

**Recommendation**: if `repair_project()` is actively maintained (not
legacy), consider whether it can delegate to the same 3-stage helper the
main flow uses, parameterized by "skip generation," rather than
re-implementing the sequence. Not attempted in this pass — out of scope
("no speculative refactors").

---

## Risk 3 (High) — `run_deterministic_patches`'s per-attempt repair pass is narrower than its initial pass

**Fact:** `core/pipeline.py:459-467` (the live V15 pipeline's *initial*
call) runs `run_deterministic_patches` **and** 5 more `database_patcher.py`
functions: `patch_database_py`, `patch_model_field_mismatches`,
`patch_add_missing_model_columns`, `patch_add_missing_schema_fields`,
`patch_missing_required_constructor_kwargs`,
`patch_filter_dict_unpack_constructor_kwargs`.

`repair/orchestrator.py:1075-1077` (the `FixOrchestrator`'s per-fix-attempt
cleanup pass, run after **every** LLM-driven fix group during the repair
loop) runs `run_deterministic_patches` and **only** `patch_database_py` —
none of the other 5.

**Why High:** an LLM-driven fix applied mid-repair-loop can introduce (or
fail to notice) exactly the class of bug those 5 functions exist to catch
— a model-field mismatch, a missing model column, a missing schema field,
a missing required constructor kwarg. Those bugs are caught at generation
time but not re-checked after every subsequent LLM fix, for the entire
rest of the repair loop. **[inferred]** — not confirmed against a live
failure this pass (would require a generation run, out of scope for a
$0 audit), but the mechanism is directly analogous to Experiment 048's
confirmed finding (a fix-loop stage silently not re-validating something
an earlier stage guaranteed).

**Recommendation**: worth a $0 static check next cycle — read what
`patch_model_field_mismatches` etc. actually detect, and whether an LLM
fix group plausibly reintroduces that shape of bug. If so, add those 5
calls to the orchestrator's per-attempt pass. Not done in this pass
(would be a behavior change, and the rules for this audit are read-only
unless "tiny, obviously correct").

---

## Risk 4 (Medium) — Four coexisting dispatch mechanisms for the same conceptual thing

**Fact** (full detail in `REPAIR_GRAPH.md` §1): ForgeAI has four different
ways a "deterministic repair" gets registered and invoked:
1. Hardcoded sequential call list (`run_deterministic_patches`,
   `run_frontend_patches`) — no enforced ordering, ordering is comments +
   trust.
2. Priority-number registry (`preflight.PreflightRegistry`) — explicit
   ordering, per-fix exception isolation.
3. Error-type dict registry (`deployment_fix_service._DETERMINISTIC_FIXES`)
   — dispatch key is a string, not order-sensitive by design.
4. Inline if/elif (`deployed_fixer.fix_deployed_app`) — dispatch key is a
   string, not order-sensitive by design.

**Why Medium:** none of these is wrong in isolation, and 2-4 are
reasonable patterns for their specific use case. The risk is for a future
contributor: the existence of a clean, already-proven registry pattern
(mechanism 2, with per-fix failure isolation) sitting right next to a
62-function hardcoded list with zero failure isolation (mechanism 1,
`REPAIR_GRAPH.md` §5) makes it easy to add function #67 to the wrong
place, or assume mechanism 1 has the same safety properties mechanism 2
does. It does not: a single unguarded exception inside the mechanism-1
loop stops every subsequent patcher in that call.

**Recommendation**: none proposed here — this observation alone doesn't
justify a migration (out of scope: "no speculative refactors,"
"no architectural rewrites"). Flagging it as context for anyone
considering Phase 9 of the previously-proposed dashboard roadmap ("Plugin
Architecture") — that proposal is solving a real, already-observed
inconsistency, not a hypothetical one.

---

## Risk 5 (Medium) — `run_deterministic_patches`'s own docstring undercounts its call sites

**Fact:** `deterministic_patcher.py:6228` states "every one of its 7 call
sites called it as a bare statement." This audit found **8** call sites
(`REPAIR_GRAPH.md` §6). Minor in isolation, but it's a live signal that
this function's own documentation isn't kept in sync with its callers —
consistent with Risk 2's drift concern above.

---

## Risk 6 (Medium) — `patch_ensure_auth_pages` runs twice per full generation

**Fact:** `patch_ensure_auth_pages` is called directly from
`run_deterministic_patches` (`REPAIR_GRAPH.md` §2 step 29) **and** again
inside `run_frontend_patches` (§3 step 14), which `run_deterministic_patches`
itself calls as its final step. Every full generation pass therefore
invokes this function twice.

**[inferred]**: likely harmless if the function is idempotent (checks
"does the page already exist" before writing), which is the pattern every
other "known-good injection" patcher in this codebase follows
(`_patch_auth_utils`, `_patch_pagination_component`, etc.). Not verified
by reading `patch_ensure_auth_pages`'s own body in this pass — flagged as
a fact (it does run twice) with an explicit inference (probably harmless)
rather than claimed as a confirmed bug.

**Recommendation**: a one-line read of `patch_ensure_auth_pages` to
confirm idempotency would resolve this with certainty; small enough that
a future cycle could fold it into Risk 3's static-check pass.

---

## Risk 7 (Low) — Priority-registry execution order silently diverges from source-file order

**Fact** (`REPAIR_GRAPH.md` §5): in `preflight.py`, `fix_postgres_url`
(source line 176, priority 15) is defined *before*
`fix_config_missing_attrs` (source line 206, priority 14) in the file, but
runs *after* it at execution time, because the registry sorts by priority,
not source position.

**Why Low:** this hasn't caused a bug that this audit found evidence of —
it's a maintainability trap, not an active defect. A future contributor
reading top-to-bottom and reasoning "postgres_url clearly runs before
config_attrs, so I can rely on settings already being patched" would be
wrong, silently.

**Recommendation**: none proposed (cosmetic; a comment near the registry
class noting "execution order is priority, not source position" would be
a tiny, obviously-correct addition — but this audit did not make it,
deferring to the "read-only unless tiny AND obviously correct" rule since
judging what counts as "obviously correct" documentation is itself
debatable).

---

## Non-finding: no confirmed dead repair code

**Fact:** every function initially flagged by a naive "grep for
`function_name(`" dead-code check (21 candidates across the audited files)
was confirmed live once indirect dispatch was accounted for:
- 15 were `preflight.py` registry members (decorator-registered, called
  via `fn(project_path, diagnostics)` — never spelled by name at the call
  site).
- 5 were `deployment_fix_service.py`'s `_DETERMINISTIC_FIXES` dict members
  (decorator-registered, called via `_DETERMINISTIC_FIXES[error_type](...)`).
- 1 (`_fix_path_backslashes`, `json_cleaner.py`) is passed as a bare
  function reference to `re.sub(pattern, _fix_path_backslashes, text)` —
  never invoked with a literal `(` after its name.

**Methodology note, important for future audits of this codebase:** a
naive call-site grep produces false positives whenever a codebase uses
decorators, registries, or passes functions as values. All three patterns
are in active use here. Any future dead-code sweep must check for
`@preflight.register`, `@_deterministic_fix`, and bare-reference passing
before concluding a function is unreachable.

`_patch_router_export_mismatch` and `_patch_forward_role_to_duplicate_registrars`
(the two `deterministic_patcher.py` functions not present in
`run_deterministic_patches`'s own call list) were separately confirmed
live: the former is called directly from `v6_orchestrator.py:441` and
`:1086`; the latter from inside `_patch_auth_routes` itself
(`REPAIR_GRAPH.md` §4).

**This does not mean the codebase has no dead code at all** — this audit
checked the ~114 functions matching a `patch_`/`fix_` naming convention
across 9 files. It did not exhaustively check every helper function in
those files, nor any file outside the repair-pipeline set. Absence of
evidence in a targeted sweep is not proof of absence everywhere.

---

## Duplication findings (Task 2)

### Confirmed duplicative: JSX/template-literal build-break fixers

**Fact:** four independent functions target overlapping JSX-syntax-break
patterns:
- `frontend_service.py::_fix_jsx_brace_errors` (generation-time) — fixes
  `}}}>`→`}}>` (extra closing brace before tag-close).
- `frontend_service.py::_fix_empty_template_expressions` (generation-time)
  — strips empty `${}` interpolations.
- `frontend_service.py::_fix_jsx_truncated_templates` (generation-time) —
  closes unclosed `${...}` and unclosed backtick template literals,
  line-by-line.
- `deterministic_patcher.py::_patch_broken_template_literal_classname`
  (repair-stage, added Experiment 049) — detects and collapses a broken
  template-literal-ternary `className` to a static string.

This was already discovered and documented during Experiment 049 (see
`experiments.md` and the memory note `project_jsx_truncated_templates_risk.md`
from that session): specifically, `_fix_jsx_truncated_templates`'s
"close any line with an odd backtick count" rule may be *converting*
Exp049's bug shape (a dropped `${` before a multi-line ternary) from an
"unclosed" form into a "falsely self-closed" form, rather than fixing it
— unconfirmed, explicitly flagged as a lead needing evidence, not
re-investigated in this pass (would require live generation output to
compare before/after, and this audit is read-only/$0 by the same
constraint that produced the original note).

**Recommendation**: this is already tracked; no new recommendation beyond
pointing at the existing lead. Consolidation not attempted (would be a
behavior change).

### Investigated and ruled NOT duplicative: relationship-stripping family

**Fact:** `_patch_strip_relationships`, `_patch_strip_back_populates`,
`_patch_dangling_foreign_keys`, `_patch_model_aliases`,
`_patch_relationship_string_aliases` look duplicative by name (all
touch SQLAlchemy `relationship()`/FK declarations) but are confirmed, by
reading their bodies and the ordering comments in `REPAIR_GRAPH.md` §2,
to be complementary layered defense: `_patch_strip_relationships` removes
whole `relationship()` statements (which incidentally removes most
`back_populates=`/`backref=` usages as a side effect of removing the
containing statement); `_patch_strip_back_populates` is an explicitly
documented defensive backstop ("in case any remain," line 6282) for any
that survive the first pass through some other code path. Not duplication
— correctly ruled out rather than assumed.

### Confirmed duplicative: param-order fixing exists in three separate implementations

**Fact:** the same bug class — a route handler with `Path`/`Query`/`Depends`
params ordered before a body param, a Python `SyntaxError`
("non-default argument follows default argument") — has three independent
fixer implementations:
- `deterministic_patcher.py::_patch_param_order` (line 1174, dispatch
  mechanism 1)
- `preflight.py::_fix_param_order` (line 651, priority 26, dispatch
  mechanism 2)
- `file_writer_service.py::_fix_fastapi_param_order` (line 247, chained
  inline inside `_normalize_newlines`/`_is_safe_to_write`, a fifth dispatch
  pattern not in `REPAIR_GRAPH.md` §1)

Not resolved in this pass (would require reading and diffing all three
regex implementations to know if they're identical, overlapping, or
handle genuinely different malformed shapes — the same rigor that revealed
the relationship-family functions were NOT duplicative despite similar
names). Flagged as a lead, same treatment as the JSX/template-literal
family.

### Confirmed duplicative: smart-quote normalization exists in at least two places

**Fact:** `deterministic_patcher.py::_patch_smart_quotes` (line 1015) and
`file_writer_service.py::_fix_smart_quotes` (line 178) both normalize
Unicode smart quotes/dashes to ASCII. Not diffed for exact overlap in this
pass.

### Not independently re-verified in this pass

Task request also asked to check the schema/model-field-mismatch family
(`_patch_missing_create_update_fields`, `_patch_add_missing_model_columns`,
`_patch_add_missing_schema_fields`, `_patch_model_field_mismatches`,
`_patch_response_schema_inherited_required_fields`,
`_patch_schema_nullable_required_mismatch`) and the auth family
(`_patch_auth_utils`, `_patch_auth_routes`, `_patch_auth_requirements`,
`patch_ensure_auth_pages`) for overlap. Each has a distinct one-line
purpose documented inline at its call site in `REPAIR_GRAPH.md` §2 (e.g.
"Same fix for fields a *Response class INHERITS" at line 6365-6371 makes
clear `_patch_response_schema_inherited_required_fields` is deliberately
NOT the same check as `_patch_response_schemas_optional` — it exists
*because* the other one has a specific blind spot). Full function-body
comparison for all pairs in these two families was not completed in this
pass — see `REPAIR_INVENTORY.md` for what's confirmed about each
individually; a dedicated duplication pass on these two families is a
reasonable next $0 cycle if this report's Risk 1 (test coverage) isn't
prioritized first.

---

## Summary — risk ranking

| Rank | Finding | Severity | Confirmed or inferred |
|---|---|---|---|
| 1 | 8/114 repair functions have any test coverage | Critical | Confirmed (grep-verified) |
| 2 | `repair_project()` duplicates the main flow's 3-stage pattern | High | Confirmed (structural) |
| 3 | Per-attempt repair pass skips 5 of 6 `database_patcher.py` functions the initial pass runs | High | Confirmed call-site gap; impact inferred |
| 4 | 4 coexisting dispatch mechanisms, one with no failure isolation | Medium | Confirmed |
| 5 | Docstring says 7 call sites, actually 8 | Medium | Confirmed |
| 6 | `patch_ensure_auth_pages` runs twice per generation | Medium | Confirmed call, impact inferred (likely idempotent) |
| 7 | Priority-registry execution order diverges from source order | Low | Confirmed, no active bug found |
| — | JSX/template-literal fixer overlap | (tracked separately, see Exp049 memory) | Confirmed overlap; interaction risk unconfirmed |
| — | Param-order fixing implemented 3 times, smart-quote normalization 2 times | Medium (unranked, needs a diff pass) | Confirmed function existence + overlap by name/purpose; not diffed line-by-line |
| — | No confirmed dead code in the audited surface | N/A (reassuring non-finding) | Confirmed after accounting for 3 indirect-dispatch patterns |

---

## Scope note on Task 5 (precision / false-positive / false-negative / scalability / maintainability per function)

The original audit request asked for all five of these evaluated
per-function, for all 114 functions. That was not completed at that
granularity in this pass — doing so rigorously (the way Risk 1-7 above
were produced, each backed by a specific code citation) would mean reading
every regex/detection condition in all 114 functions individually, which
is a multi-day undertaking on its own, not a single $0 audit cycle.

What this pass actually delivers against Task 5: the highest-precision-risk
items were surfaced through the *other* tasks instead of a blanket
sweep — the JSX/template-literal family's precision problem (already
empirically measured at an 85% false-positive rate before Experiment 049's
three refinement rounds, per that experiment's own commit) is the one
member of this inventory with an actual measured FP rate; everything else
in `REPAIR_INVENTORY.md` has an *inferred*, not measured, precision
characterization (mostly: "reads as narrowly-scoped from its trigger
condition," not independently stress-tested against a corpus the way
Exp049 was). Scalability and maintainability were addressed only in
aggregate, via Risk 1 (test coverage) and Risk 4 (dispatch-mechanism
inconsistency) — both are legitimate maintainability findings, just not
attributed to individual functions.

**Recommendation**: if per-function precision/FP/FN grading is genuinely
wanted, it's a separate, larger piece of work — plausibly one $0 cycle per
~15-20 function cluster (mirroring how this session's actual bug-hunting
work happened: one cluster, deeply, with real corpus validation, not 114
shallow judgment calls in one pass).
