# Experiment 102 — Extend Role Vocabulary Discovery

2026-07-13. Offline implementation, $0, zero Cerebras calls. Extends
Exp101's identified fix point (`_discover_role_vocabulary_from_schema`/
`_discover_role_vocabulary_from_routes`, `app/services/deterministic_patcher.py`)
rather than a new patcher, per this experiment's own constraint.

## 1. Code diff (Tasks 1, 2)

**Task 1 — required-field regex**: `_ROLE_FIELD_RE`'s leading quoted
default (`Field("diner", ...)`) is now optional. Confirmed live shape
(`event_manager_platform`): `role: str = Field(min_length=1,
pattern="^(Organizer|Attendee)$")` — a required field, no default at
all. When the default group is absent,
`_discover_role_vocabulary_from_schema` now falls back to `"user"` as
the synthesized default (added to the allowed set too) — reusing
exactly the same safe-fallback convention
`_discover_role_vocabulary_from_routes` already established for its own
no-anchor case, rather than inventing a new heuristic (e.g. guessing
"first alternative in the pattern" would have been unsafe: nothing
guarantees the first-listed role is the lower-privilege one — confirmed
inconsistent across the corpus, e.g. `"diner|staff"` lists the
lower-privilege role first while `"Organizer|Attendee"` lists the
higher-privilege one first).

**Task 2 — getattr() detection**: new shared fragment
`_ROLE_ACCESS_FRAGMENT` matches either literal `.role` access OR
`getattr(<obj>, "role", <default>)` calls; both `_ROLE_EQ_RE` and
`_ROLE_IN_RE` now use it (extending the `in`/`not in` sibling shape too,
not just the `==`/`!=` case Exp101 specifically found, since both serve
the identical "route-level role gate" detection purpose and leaving
one half-fixed would be an inconsistent state).

## 2. Replay (Task 3)

**`event_manager_platform`**: `_discover_role_vocabulary()` now returns
`('user', ['Organizer', 'Attendee', 'user'])` — previously `None`.

**Full corpus** (73 currently-on-disk projects, git-stash A/B compared
against the exact pre-Exp102 commit): pre-Exp102 code finds a role
vocabulary in **6** projects; post-Exp102 finds **9** — the same 6 plus
3 new:

| Project | Discovery mechanism |
|---|---|
| `event_manager_platform` | Task 1 (required-field regex) |
| `forge_blog_cms` | Task 2 (`getattr(user, "role", None) not in {"admin", "author"}` in `tag_routes.py`) |
| `forgeai_booking_platform` | Task 2 (`getattr(user, "role", None) != "customer"`/`"provider"` in `booking_routes.py`) |

## 3. Verify previous detections unchanged (Task 4)

All 6 pre-existing discoveries (`dine_reserve`, `forge_learn`,
`library_management_system`, `real_estate_marketplace`,
`taggable_blog_platform`, `volunteer_management_system`) return
**byte-for-byte identical tuples** on old vs. new code — confirmed via
git-stash A/B replay against the same corpus snapshot, not assumed.
Zero regressions.

## 4. Regression tests (Task 5)

Added 6 tests to the existing `test_role_aware_auth_template.py` (no
new test file, matching this experiment's "reuse the existing
mechanism" spirit): required-field-with-no-default discovery, the
synthesized default validating correctly in
`_build_auth_routes_template`'s output, `getattr()`-based `==`/`!=`
gate discovery, `getattr()`-based `in`/`not in` gate discovery, a
guard that plain `.role` access is completely unaffected by the new
alternation, and a guard that the existing "≥2 distinct roles"
safety threshold still applies through the `getattr()` path (a single
repeated role string, even via `getattr()`, still returns `None`).
All 19 tests in the file (13 existing + 6 new) pass.

## 5. Full regression suite (Task 6)

51/54 — same 3 pre-existing, unrelated failures this series has
repeatedly confirmed (`test_exp066_write_pipeline_hardening.py` —
stale fixture directory, `test_exp070_security_phase0.py` — missing
`jose` package, `test_semantic_write_validation.py` — 2 unrelated
write-corruption-replay subtests). Zero new regressions.

## 6. Estimated ForgeBench improvement

Directly fixes the one confirmed Option A incident from Exp101
(`event_manager_platform`) plus, per the full-corpus replay, 2
additional real, previously-undiscoverable role-gated apps in the
current corpus (`forge_blog_cms`, `forgeai_booking_platform`) that
would have hit the identical false `JourneyCRUDFailure` if benchmarked.
Converts a confirmed "existing repair failed to activate" gap into
correct behavior, reusing 100% of the already-proven V20.1.5
role-aware-retry mechanism — no new infrastructure, no new patcher.

## 7. Recommendation for ForgeBench v1.1

**Run a smaller confirmation benchmark before the full 100-app v1.1**,
not a direct jump to scale — consistent with Exp101's own
recommendation. Specifically: re-run (or re-generate)
`event_manager_platform` (and ideally `forge_blog_cms`/
`forgeai_booking_platform`-shaped ideas) to confirm live that the
role-aware-retry now successfully elevates and passes Create, before
committing to a 100-app run. This experiment's evidence is strong
(precise root cause, clean replay, zero regressions) but has not yet
been confirmed against a live generation + journey run, only against
already-generated, static code — the natural next step (Exp103) per
this project's own "offline first, then live-validate" discipline.

**Deliverables**: this doc, `experiments.md` entry, code diff in
`backend/app/services/deterministic_patcher.py`, extended test file
`backend/tests/reliability/test_role_aware_auth_template.py`.
**Cost: $0, zero Cerebras calls.**
