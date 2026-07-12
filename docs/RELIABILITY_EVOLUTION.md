# ForgeAI Reliability Evolution (Experiment 069, Part 4)

2026-07-12. Companion to `docs/ENGINEERING_HISTORY.md` — same source
(all 68 `experiments.md` entries, read in full), different lens: what
actually moved reliability, what didn't, and why. Quotes are verbatim
or close paraphrase of what each entry's own text says, not this
document's characterization of it.

## What actually moved a measured number (in the experiments' own words)

- **Exp007**: todo 25.5→76.4 (recovered), blog_cms 33.0→94.1 (**+61.1**)
- **Exp009**: crm Runtime Startup 0.0→20.0, API Functionality 0.0→100.0 (total boot failure → healthy, 15/15 endpoints)
- **Exp018**: blog_cms 61.5→90.3, **CRUD 11/11 PASS (first ever)**; crm 39.4→91.4, **CRUD 11/11 PASS (first ever)**
- **Exp034**: todo 42.0→90.7 (recovered from m2's total collapse)
- **Exp039/043/044**: three separate live journeys, each 6/11→11/11 steps passing, described as fixed "permanently, for every real end user, not just this test"
- **Exp041**: endpoint smoke-test pass rate 7% (1/14) → 100% (14/14)
- **Exp047**: corpus prevalence 4/53 → 0/53
- **Exp052**: repair test coverage 8/114 (7%) → 93/114 (82%)
- **Exp057/058**: NameError 4/5 runs → 0/2 runs, confirmed via exact git-stash replay

## What explicitly did NOT produce a confirmed benefit (verbatim/close paraphrase)

- **Exp002**: *"This run answers nothing about AppContract... Do not treat the score drops as evidence against M1."*
- **Exp005**: *"Inconclusive on the AppContract question specifically — one sample per app isn't enough..."*
- **Exp008**: *"Inconclusive on AppContract... Recommend NOT continuing to expand AppContract based on this evidence."*
- **Exp017**: flagged `"CANARY FAILED"` by the automated script, but the entry's own verdict was *"KEEP (mechanism confirmed, overall run confounded)... Not flipping the default to ON yet."*
- **Exp020**: *"mechanism CONFIRMED correct and safe, aggregate benefit INCONCLUSIVE this run"*; a second canary *"adds no new evidence about the mechanism itself, positive or negative."*
- **Exp028/030**: closed as *"NO FIX NEEDED"* — verified clean, not a measured improvement.
- **Exp041**: two canaries *"killed by the harness before writing a final canary_history.json entry"*; results labeled *"Directional, not conclusive."*
- **Exp048**: its own entry states the canary was *"confounded, not a clean confirmation."*
- **Exp053 — the single most important negative data point in the whole history.** Shipped with all tests green, reported as "Shipped" — but **caused a real, undetected regression** that only Experiment 056 (a dedicated, later, measurement-only cycle) found. This is the clearest case in 68 experiments of a change passing its own test suite and still breaking production behavior.
- **Exp058**: *"Todo score did NOT recover... 2 of 4 stated criteria met... 2 not met."*
- **Exp061**: *"Genuine gap, reported honestly rather than papered over"* — the regex fallback path was never exercised live.

## "Found and fixed a prior experiment's own bug" — every chain found across all 68

This project's own experiments repeatedly catch each other's mistakes,
which is a genuine reliability-engineering strength worth naming
explicitly, not just a list of bugs:

1. **Exp003 → Exp005**: Exp003 fixed a `getattr`-on-method bug in `pipeline.py`; Exp005 found the *exact same bug class* independently in `confidence/engine.py`, same day.
2. **Exp008 → Exp009**: Exp008's own A/B test found 2 unrelated config-patcher bugs blocking its measurement; Exp009 fixed them.
3. **Exp012 → Exp013**: Exp012's fix was found incomplete (presence≠requiredness) by its own analysis; Exp013 closed the gap.
4. **Exp014 → Exp015/Exp016**: investigating what looked like an Exp014 regression (it wasn't one) surfaced two independent journey-runner bugs, which became Exp015 and Exp016.
5. **Exp032 → Exp033 → Exp034**: model retirement (032) exposed new Gemini-3 idioms; 033 fixed them; 034 confirmed the fix held.
6. **Exp039 → Exp040 + Exp041**: fixing the Playwright harness's phantom-failure bug surfaced two more independent bugs as side effects of live-testing the fix.
7. **Exp041 → Exp042**: Exp042's first fix was found by booting Exp041's own canary output and reading what broke next.
8. **Exp043 → Exp044**: Exp044 explicitly continued Exp043 and found 2 more bugs "exposed only by fix #1 [Exp043] actually working" — bugs structurally unreachable before Exp043 shipped.
9. **THE PRIMARY CHAIN — Exp053 → Exp056 → Exp057 → Exp058.** Exp053 (repair consolidation, all tests green) introduced a silent regression. Exp056 (measurement-only baseline) found and root-caused it via live generation, not via any test. Exp057 fixed it with a 5-line diff. Exp058 live-validated the fix. **This is the highest-value, most complete chain in the entire dataset** — a real production regression that passed its own test suite, caught only because someone deliberately measured rather than trusted green tests.
10. **Exp066 → Exp067**: Exp067 both hardened the write path Exp066 left untouched AND found that Exp066's own threat-model assumption (about `_safe_patch_target`'s Windows-drive/UNC gap) was **empirically wrong** — a direct, explicit self-correction of a prior experiment's own stated claim, made by actually testing the claim rather than repeating it.

## Three-bucket ranking, by the experiments' own stated verdicts

- **Clear positive impact** (~42 of 68): 003, 004, 006, 009, 011, 013, 014, 016, 018, 019, 020 (mechanism confirmed via targeted verification), 021, 022, 023, 024-027, 032-047 (the bulk of the reliability-pivot sequence), 049-052, 054, 055, 057, 060, 064, 066, 067.
- **Inconclusive / mixed / evidence gap honestly flagged** (~20 of 68): 002, 005, 007 (partial), 008, 010 (untested that run), 012 (incomplete same-day), 015 (infra-confounded), 017, 028, 029 (implemented, validation pending), 030, 041 (infra-confounded), 048 (quota-confounded), 058, 059/065/068 (documentation-only, no code change to measure), 061, 062 (a successful investigation, not a fix), 063 (investigation only).
- **Explicitly negative / caused a regression**: **053 alone** — not reverted, but caused a real production bug found and fixed one cycle later (056/057). The sole unambiguous case in 68 experiments.

## Recurring confound pattern: infrastructure, not code

Two distinct confound families recur across 5 separate incidents:

- **Provider quota/rate-limit exhaustion**: Exp002 (Groq near-limit + Gemini 503s), Exp013 (Groq daily quota hit mid-run), Exp048 (Gemini prepayment depleted + Groq daily cap) — 3 incidents.
- **Other infrastructure failures**: Exp015 (transient local network outage), Exp041/042 (canary harness killed 2 runs before writing results — investigated via a dedicated $0 heartbeat diagnostic that ruled out a flat wall-clock timeout but left the actual cause unresolved) — 2 incidents.

**Every one of these 5 incidents was correctly identified and excluded
from judging the code change under test**, rather than misattributed
as a regression — a consistent discipline across the whole 68-experiment
history, with one narrow exception: **Exp053's regression was NOT
caught by its own validation** (all tests green) and required a
dedicated, later measurement-only cycle (056) to surface. This is the
one gap in an otherwise consistent "don't blame the code for infra,
but don't let infra hide a real code bug either" discipline.

## What this means for reliability going forward

Cross-referencing this timeline against `docs/RUNTIME_HISTORY.md`'s
(Experiment 068) independently-computed canary numbers: the project's
own North-Star metric (`first_try_success_rate`) sits at 30% as of the
most recent measurement, trending down, DESPITE this extensive,
mostly-positive experiment history above. The two are not
contradictory — most individual experiments demonstrably fixed the
specific, narrow thing they targeted (the "clear positive impact"
bucket above is real and well-evidenced) — but **fixing 42 narrow,
verified bugs has not yet been sufficient to move the aggregate
first-try success rate**, because (per Experiment 068's own findings)
the two largest remaining failure clusters (`MissingEndpoint`,
`JourneyCRUDFailure`) were never the direct target of a dedicated fix
in this entire 68-experiment history — every experiment above that
touches `JourneyCRUDFailure` (010, 015, 016, 019, 039, 043, 044) fixed
a specific *symptom* of it (a coercion bug, a status-code bug, a
harness bug, a role-discovery gap) without ever fixing its *dominant
root cause* (per Exp068's own bundle analysis: a missing `/auth/register`
route, 64% of recent forensic evidence). This is the single most
important synthesis finding of this entire experiment: **narrow,
well-verified fixes and aggregate reliability improvement are not the
same thing, and this project's own history is the clearest possible
demonstration of that distinction.**

## Update (Experiment 072): the pattern reproduced live, one cycle later

Experiment 071 finally targeted `JourneyCRUDFailure`'s actual dominant
root cause directly (not another symptom) — and Experiment 072's live
validation (`docs/EXP072_VALIDATION.md`) confirmed it worked: zero
`/auth/register` 404s across a 4-app canary run, versus 9/14 forensic
bundles before. **The aggregate CRUD pass rate still did not move**
(3 of 4 apps still failed) — not because the fix didn't work, but
because a **different, previously-secondary failure mode**
(`_patch_attr_access_mismatches()`'s scope-confusion bug, corrupting a
correctly-injected template in 2 of 4 apps independently) was sitting
immediately behind it, previously masked by the more dominant
404-before-any-of-this-mattered failure. This is the exact "narrow fix
≠ aggregate improvement" pattern this document's own closing synthesis
predicted, reproduced in real, live data one cycle after being
identified — not a failure of Experiment 071's fix, but direct
confirmation that ForgeAI's reliability problem has more than one
layer, and fixing the outermost one reveals rather than resolves the
next.

## Update (Experiment 074): the layer-peeling pattern held one more cycle — and closed cleanly, once

Experiment 073 fixed the scope-confusion bug Experiment 072 found (AST-scoped
rewrite, replacing the file-wide `re.sub()`). Experiment 074's live
validation (`docs/EXP074_VALIDATION.md`) confirmed the fix itself worked
cleanly: 3 real invocations in a fresh canary, all correctly scoped,
including a direct reproduction of the exact ambiguous-attribute-name
shape (same word, two different objects, one line) that caused the
original corruption. **This is the first layer in this multi-cycle chain
(071→072→073→074) that closed without immediately revealing a new
failure in the SAME mechanism** — the two new issues Experiment 074
surfaced (`blog_cms`'s missing `PUT`/`DELETE /posts/{id}` routes;
`inventory`'s NOT-NULL-on-PUT gap) are both **pre-existing, independent**
failure classes already present in the taxonomy or a documented sibling
of one (`MissingEndpoint`'s non-auth sub-case; `NotNullViolationError`'s
update-path variant), not new symptoms of the attribute-rewrite scoping
problem itself. The "fixing the outermost layer reveals the next" pattern
still held at the aggregate-app level (blog_cms and inventory still
didn't reach a clean first-try pass) — but for the FIRST time in this
chain, the specific mechanism being validated did not itself produce the
next layer's failure. That is meaningful progress on this document's own
closing synthesis question, even though the aggregate metric it warns
against over-reading (`first_try_success_rate`) still needs the two newly
surfaced, independent issues addressed before it can be expected to move.
