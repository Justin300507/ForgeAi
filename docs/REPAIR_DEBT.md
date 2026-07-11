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

### Investigated and ruled NOT duplicative: relationship-stripping family — WITH ONE CORRECTION

**Fact:** `_patch_strip_relationships`, `_patch_strip_back_populates`,
`_patch_dangling_foreign_keys`, `_patch_model_aliases`,
`_patch_relationship_string_aliases` look duplicative by name (all
touch SQLAlchemy `relationship()`/FK declarations) but are, by
reading their bodies and the ordering comments in `REPAIR_GRAPH.md` §2,
complementary layered defense: `_patch_strip_relationships` removes
whole `relationship()` statements (which incidentally removes most
`back_populates=`/`backref=` usages as a side effect of removing the
containing statement); `_patch_strip_back_populates` is an explicitly
documented defensive backstop ("in case any remain," line 6282) for any
that survive the first pass through some other code path. Not
duplication for these two.

**Correction (verified directly, 2026-07-11, same audit cycle):**
`_patch_relationship_string_aliases` is a different case, not covered by
the "layered defense" explanation above. Its own docstring: *"Scan all
model files for relationship('X') where X is not a real registered
class."* Its own search loop requires `relationship(` to be present in
the file. But `_patch_strip_relationships` (`REPAIR_GRAPH.md` §2 step 13)
runs earlier in the exact same `run_deterministic_patches` call and
**unconditionally removes every single `relationship(...)` assignment**
from every model file — confirmed by direct read: `if "relationship(" not
in original: continue` is the only skip condition; every file that has
one gets every one stripped. By the time `_patch_relationship_string_aliases`
runs at step 19, there is no `relationship(` left in any model file for
it to find. This is not "layered defense" — it is a function that is
called every generation, and finds nothing every generation, structurally,
by pipeline design. **Downgrading this one function out of the "ruled not
duplicative" bucket and into a new, separate finding below.**

### New finding — `_patch_relationship_string_aliases` is called but structurally inert

**Fact:** confirmed above. Distinguish this from "dead code" in the usual
sense (a function nothing ever calls) — this function IS called, every
run, from `run_deterministic_patches`. It simply can never do anything,
because an earlier step in the same call already eliminated its only
possible input. The `run_deterministic_patches` telemetry (`counts[]`)
presumably still records a `0` for this key every time, which is at least
honest (not miscounted as a false success) — not independently verified
in this pass whether the counts dict distinguishes "ran, found 0" from
"didn't run."

**Why Low-Medium, not Critical:** harmless in its current form — it
costs a wasted file-glob-and-regex-scan pass per generation (negligible
runtime cost) and doesn't corrupt anything. It's flagged here because it's
exactly the shape of thing this whole audit exists to surface: a function
that reads as intentional (a documented "fix wrong class name in
relationship string" feature) but is actually inert by construction, and
nothing in the codebase's own tests or telemetry would reveal that without
this kind of direct ordering trace.

**Recommendation**: either (a) delete the function and its call site (it
can never fire — a "tiny, obviously correct cleanup" candidate per this
audit's own rules, but deletion is still a behavior change to a live file
and wasn't made in this read-only pass), or (b) move its call to
*before* `_patch_strip_relationships` in the sequence if the intent was
for it to fix string-aliased relationship targets before the stripping
pass removes them wholesale (this would change behavior — currently
`_patch_strip_relationships`'s own replacement `@property` logic resolves
the target via `_build_model_index`'s FK map, not via the string arg
`_patch_relationship_string_aliases` would have fixed, so it's not obvious
running it first would even change the end result — needs a closer read
of `_build_model_index` before concluding (b) is worth doing, out of
scope for this pass).

### Confirmed duplicative: param-order fixing exists in three separate implementations — UPGRADED to High confidence, diffed

**Fact:** the same bug class — a route handler with `Path`/`Query`/`Depends`
params ordered before a body param, a Python `SyntaxError`
("non-default argument follows default argument") — has three independent
fixer implementations:
- `deterministic_patcher.py::_patch_param_order` (line 1174, dispatch
  mechanism 1) — uses `compile()` itself as the trigger check, then a
  `(`/`)`-only paren-depth-tracking param-list splitter.
- `preflight.py::_fix_param_order` (line 651, priority 26, dispatch
  mechanism 2) — **delegates directly to `_patch_param_order` above; not
  independent, no duplication here.**
- `file_writer_service.py::_fix_fastapi_param_order` (line 247, chained
  inline inside `_normalize_newlines`/`_is_safe_to_write`, a fifth dispatch
  pattern not in `REPAIR_GRAPH.md` §1) — an **independently-coded**
  second implementation, with its own `_split_params` helper that tracks
  `(`/`[`/`)`/`]` (one more bracket type than the version above), invoked
  from a different call path entirely: when the repair loop's fix-writer
  (`fix_writer_service.py`) writes a freshly LLM-generated fix file to
  disk, not when the initial deterministic-patch sweep runs.

**Diffed in this enrichment pass (a separate parallel sub-audit read both
bodies directly):** genuinely two different codebases solving the
identical bug, reachable via two different trigger paths within the same
repair pipeline. **This is the single highest-risk duplication found in
the whole audit** — a future correctness fix applied to one implementation
(e.g. handling a param-order edge case neither currently covers) will not
propagate to the other, and which one fires depends on *how* the broken
file arrived (initial generation vs. mid-loop LLM fix), not on anything
visible to whoever is debugging a param-order failure later.

**Recommendation**: consolidate `file_writer_service.py::_fix_fastapi_param_order`
to call `deterministic_patcher.py::_patch_param_order`'s underlying logic
instead of maintaining a second implementation — the same pattern
`preflight.py::_fix_param_order` already correctly uses. Not attempted in
this pass (behavior change, out of scope for a read-only audit), but this
is the clearest "should actually get fixed" item in the whole report,
ahead of the test-coverage gap in terms of concrete near-term bug risk
(Risk 1 is about the unknown; this one is a known, already-diffed
inconsistency).

### Confirmed duplicative: smart-quote normalization exists in at least two places

**Fact:** `deterministic_patcher.py::_patch_smart_quotes` (line 1015) and
`file_writer_service.py::_fix_smart_quotes` (line 178) both normalize
Unicode smart quotes/dashes to ASCII, reachable via the same two
different-call-path pattern as the param-order duplication above (initial
sweep vs. mid-loop fix-write). Not diffed byte-for-byte for exact overlap
in this pass, but same structural risk shape as the param-order finding.

### Confirmed duplicative: a string-aware "matching closing brace" utility is implemented three times

**Fact (diffed in this enrichment pass — bodies read directly, not
name-matched):** the identical ~15-line algorithm — walk forward from an
opening `{`, track nesting depth, track whether inside a string with
escape handling, return the span of the matching `}` — is implemented
three separate times:
- `app/utils/json_cleaner.py::_find_matching_close_brace` (line 267)
- `app/utils/json_cleaner.py::try_repair_truncated` (line 63) — an inline,
  unnamed duplicate of the *same* algorithm, inside the *same file* as the
  named version above.
- `app/services/validator_service.py::_extract_object_literal` (line 665)
  — same algorithm again, in a different file, for a different purpose
  (extracting a JS object literal from generated frontend source to
  validate an auth POST body shape, vs. the two `json_cleaner.py` uses
  which repair the LLM's own raw JSON response text).

None of the three call a shared helper; variable names differ
(`in_string`/`escape_next` vs `in_str`) but control flow is line-for-line
equivalent.

**Why this is the cheapest real fix in the report:** unlike the
param-order and smart-quote duplications (which involve genuinely
different call-site contexts and would need care to consolidate safely),
this is one small, self-contained, side-effect-free utility function
reimplemented three times for no structural reason — two of the three
copies are in the *same file*. Extracting one shared
`find_matching_brace(text, open_pos, quote_chars=...)` into `app/utils/`
would remove all three duplicate implementations with, by inspection, no
behavior change. **Not made in this pass** (still a code change, and this
audit's rule is read-only unless "tiny AND obviously correct" — three
call-site rewrites across two files is judged small enough to flag
prominently but not small enough to make unilaterally without the user
deciding whether to spend the cycle on it).

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
| 2 | Param-order fixing implemented independently twice (3 call sites, 2 codebases), diffed and confirmed genuinely redundant, not sequential | High | **Confirmed (both bodies read and diffed)** |
| 3 | `repair_project()` duplicates the main flow's 3-stage pattern | High | Confirmed (structural) |
| 4 | Per-attempt repair pass skips 5 of 6 `database_patcher.py` functions the initial pass runs | High | Confirmed call-site gap; impact inferred |
| 5 | `_patch_relationship_string_aliases` is called every run but structurally cannot ever find anything (its target is unconditionally eliminated by an earlier step in the same call) | Low-Medium | **Confirmed (both function bodies read directly)** |
| 6 | A string-aware brace-matching utility is reimplemented 3 times (2 in the same file) | Low (cheapest fix in the report) | **Confirmed (all 3 bodies read and diffed)** |
| 7 | 4 coexisting dispatch mechanisms, one with no failure isolation | Medium | Confirmed |
| 8 | Docstring says 7 call sites, actually 8 | Medium | Confirmed |
| 9 | `patch_ensure_auth_pages` runs twice per generation | Medium | Confirmed call, impact inferred (likely idempotent) |
| 10 | Priority-registry execution order diverges from source order | Low | Confirmed, no active bug found |
| — | JSX/template-literal fixer overlap | (tracked separately, see Exp049 memory) | Confirmed overlap; interaction risk unconfirmed |
| — | Smart-quote normalization implemented twice, same call-path pattern as param-order | Medium (unranked, not byte-diffed) | Confirmed function existence + overlap by name/purpose |
| — | No confirmed *unreachable* dead code in the audited surface (distinct from Rank 5's "reachable but inert") | N/A (reassuring non-finding) | Confirmed after accounting for 3 indirect-dispatch patterns |

*Ranks 2, 5, and 6 were added/upgraded in this same audit cycle's
enrichment pass, after three additional dedicated sub-audits (full
line-by-line re-read of `deterministic_patcher.py`; independent
duplication sweep with body-level diffing) landed after the first
synthesis pass was already written and committed. See each finding's own
section above for what changed and why.*

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
