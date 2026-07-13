# Experiment 101 — ForgeBench Regression Investigation

2026-07-13. Investigation only, $0, zero Cerebras calls, zero code
changes. Uses ForgeBench v1.0's own telemetry (27 new failure bundles,
`generation_log.jsonl` entries, `forgebench_v1_results.json`) and the
still-on-disk generated projects — no new generation, per this
experiment's "prefer replay over new generation" constraint.

## 1-2. Collection and deduplication (Tasks 1, 2)

7 apps carried the two target failure classes: 5 `JourneyCRUDFailure`
(`forge_blog_cms`, `inventory_manager`, `library_management_system`,
`event_manager_platform`, `donation_tracker`) and 2
`UserIdNotInjectedError` (`personal_expense_tracker`,
`university_course_management`). The 27 raw bundle files deduplicate to
these **7 unique incidents** — every app's multiple bundles are retries
of the identical symptom within one generation run (same status
code/body repeated, confirmed by direct inspection), matching this
series' established retry-inflation pattern (Exp084, Exp094).

## 3-4. Per-incident comparison against the Exp091-096 corpus, and which explanation applies (Tasks 3, 4)

**`forge_blog_cms` — Option C (new structural variant).** Bundles show
`PUT /posts/6 → 405` and (mid-run) `POST /posts → 405`. Checked the
saved architecture (`metadata.json`): it correctly declares
`PUT /posts/{id}` (and `DELETE`). Checked the actual final generated
code (`post_routes.py`): **zero update/PUT/PATCH/DELETE route exists
for posts at all** — only `GET /posts`, `GET /posts/{id}`,
`POST /posts`. This is not the PUT-vs-PATCH method mismatch Exp095
fixed (architecture and would-be code agree the verb should be PUT) —
backend generation simply never implemented the endpoint. This is the
same shape Exp094 already flagged and explicitly deferred
(`volunteer_management_system`'s "missing update endpoint entirely"
sub-case) — now confirmed as a **second, independent occurrence**.

**`inventory_manager` — Option E.** Bundles show
`POST /products → 404 "Category not found"`. Checked the code: full
CRUD exists (`GET`, `GET/{id}`, `POST`, `PUT`, `DELETE` all present and
correct). The failure is the journey runner's generic Create payload
referencing a `category_id` that was never seeded — the same "seed
pipeline reliability" gap Exp097-100 already identified and explicitly
left open (moderate frequency, not yet cleared this project's own ROI
bar). Not the ownership-assignment or method-mismatch classes.

**`library_management_system` — Option E (most likely).** Bundle:
`POST /books → 403 "Insufficient permissions"`. Code confirms the gate
(`if getattr(current_user, "role", None) != "librarian"`). Checked
`app/schemas/auth.py`: **no discoverable role field at all** — this
app's registration schema doesn't expose a role to self-select, meaning
"librarian" is plausibly an intentionally non-self-registerable admin
role (a reasonable real-world business rule), not a generation defect.
The generic journey test has no path to become a librarian by design,
which may be entirely correct application behavior.

**`event_manager_platform` — Option A (existing repair failed to
activate — confirmed precisely, not assumed).** Bundle:
`POST /events → 403 "Organizer role required"`. Checked
`app/schemas/auth.py`: `role: str = Field(min_length=1,
pattern="^(Organizer|Attendee)$")` — a **discoverable** role vocabulary
exists. The role-aware-retry mechanism (V20.1.5, built specifically for
this shape, confirmed live in Exp043) *should* have elevated and
retried. Root-caused why it didn't: read
`_discover_role_vocabulary_from_schema`'s regex (`_ROLE_FIELD_RE`,
`deterministic_patcher.py:2020`) — it requires a **quoted string
default** as `Field(...)`'s first argument
(`Field("default", pattern=...)`), but this schema has no default at
all (`Field(min_length=1, pattern=...)`, a `str`-typed *required*
field) — the regex doesn't match. Checked the fallback,
`_discover_role_vocabulary_from_routes`'s `_ROLE_EQ_RE`
(`\.role\s*(?:!=|==)\s*["\'](\w+)["\']`) against the actual gate code
(`event_routes.py:56`): `getattr(current_user, "role", None) !=
"Organizer"` — **not a literal `.role` attribute access**, a
`getattr()` call instead (a common defensive-coding idiom). Neither
discovery path matches this app's actual code shape, so role discovery
silently returns `None` and the elevation retry never fires.

**`donation_tracker` — Option D/E (journey-runner limitation, not a
generation defect).** Bundle: `POST /campaigns → 400 "end_date must be
after start_date"`. The generic Create payload (dummy dates) violates a
domain business rule the app is correctly enforcing. The app isn't
buggy; the generic journey-runner payload doesn't know to satisfy this
constraint.

**`personal_expense_tracker` — Option E (confirmed via error-parser
source, not inference).** Final `generation_log.jsonl` tag:
`[UserIdNotInjectedError] sqlalchemy.exc.IntegrityError`. Checked the
current code: `create_expense` **correctly** assigns
`user_id=current_user.id` (Exp091-093's fix is present and working).
Read `error_parser.py:394-399` to understand what actually triggers
this tag: **any NOT-NULL constraint violation on a column whose name
ends in `_id`** — a generic FK-suffix heuristic, not a check specific
to `current_user.id`/ownership. The taxonomy label "UserIdNotInjectedError"
is a misnomer inherited from its original discovery context (a genuine
ownership bug) but the detector itself matches far more broadly. The
real trigger here is very likely an unrelated, unseeded FK reference
(same "seed pipeline reliability" theme, not reproduced further per
this experiment's replay-only scope).

**`university_course_management` — Option E (confirmed via code
inspection).** Same generic tag. Checked `enrollment_routes.py`: the
create handler builds `Enrollment(student_id=payload.student_id,
course_id=payload.course_id)` — **this endpoint doesn't even use an
"assign to current_user" pattern at all**; a registrar enrolls an
explicit student into an explicit course, a legitimate many-to-many
relationship shape, not an ownership-assignment shape Exp091-093 ever
targeted. The NOT-NULL violation is from the journey's dummy
`student_id: 1`/`course_id: 1` referencing rows never seeded — again
the seed-pipeline theme, and again the generic FK-suffix tag
misclassifying it under the ownership bug's name.

## 5. Category tally (Task 5)

| Category | Count | Incidents |
|---|---|---|
| A — existing repair failed to activate | 1 | `event_manager_platform` |
| B — repair activated, later overwritten | 0 | — |
| C — new structural variant outside repair scope | 1 | `forge_blog_cms` |
| D — benchmark methodology limitation | 1 | `donation_tracker` |
| E — different root cause, same taxonomy label | 4 (5 counting library as borderline D/E) | `inventory_manager`, `library_management_system`, `personal_expense_tracker`, `university_course_management` |

## 6. Estimated true remaining prevalence after deduplication (Task 6)

**0/7 (0%) of these incidents are genuine recurrences of the exact bugs
Exp091-093 (ownership-FK assignment) or Exp094-096 (Edit-path PUT-vs-
PATCH) fixed.** Both fixes are confirmed still working correctly
everywhere checked (`personal_expense_tracker`'s and
`inventory_manager`'s ownership assignment is present and correct;
no PUT-vs-PATCH method mismatch was found anywhere in this sample).
**Exp100's core conclusion — that those two specific, previously-
dominant classes are closed — holds.** What ForgeBench v1.0 actually
found is: (a) one real, precisely-scoped gap in a *different* existing
mechanism (role-vocabulary discovery, 1/25 = 4%), (b) one confirmed
recurrence of an already-flagged-but-deferred structural gap
(missing-update-endpoint, 2/50 = 4% across both ForgeBench and Exp094's
corpus), and (c) five incidents that are either already-known separate
issues (seed pipeline reliability, 3 instances) or arguably-correct
application/business-rule behavior the generic journey test isn't
equipped to handle (2 instances) — all mislabeled under overly broad
taxonomy tags that made them *look* like regressions of closed threads
when they are not.

## 7. Earliest divergence

No single shared divergence point exists across all 7 — this is itself
the core finding: **the `JourneyCRUDFailure` and `UserIdNotInjectedError`
taxonomy labels are too coarse-grained to correspond to one root cause
each.** `UserIdNotInjectedError` specifically diverges at
`error_parser.py`'s classification stage (`_FK_COLS_SUFFIXES` matches
any `_id`-suffixed NOT-NULL violation, not specifically
`current_user.id` omission) — this is where telemetry starts
overstating "recurrence." For the one confirmed Option A case, the
divergence is in `deterministic_patcher.py`'s role-discovery regexes,
which predate this experiment and were never updated for
required-field (no-default) or `getattr()`-based role checks.

## 8. Existing mechanism to extend

1. **`_ROLE_FIELD_RE`** (`deterministic_patcher.py:2020`): extend to
   also match `Field(...)` declarations with no positional default
   (required fields), not just `Field("default", pattern=...)`.
2. **`_ROLE_EQ_RE`** (`deterministic_patcher.py:2028`) /
   `_discover_role_vocabulary_from_routes`: extend to also match
   `getattr(current_user, "role", ...) ==/!= "X"`, not just literal
   `.role ==/!=` attribute access.
3. (Observability improvement, not a repair): consider whether
   `error_parser.py`'s `UserIdNotInjectedError` classifier should be
   split into an ownership-specific tag (matching
   `_OWNERSHIP_FK_SYNONYMS`'s known names) versus a generic
   "unseeded/invalid FK reference" tag for everything else — would
   prevent this exact false-recurrence confusion in future ForgeBench
   runs.

## 9. Estimated reliability improvement

Fixing #1/#2 above (both small, precisely-scoped regex extensions,
reusing 100% of the existing role-aware-retry mechanism, zero new
infrastructure) would directly resolve the one confirmed Option A
incident and, by extension, any other app using either the
required-Field or `getattr()`-based role-check idiom — both common,
increasingly-likely LLM output shapes as models drift toward more
defensive code style. Given only 1/25 apps in this sample hit it, the
measured frequency is modest, but the fix is cheap and safe enough
(reusing exactly Exp094/097's "extend the existing mechanism" pattern)
to be worth shipping regardless of ROI debate.

## 10. Recommendation for Exp102

**Implement the two role-discovery regex extensions (#1/#2 in §8)** —
small, safe, high-confidence, directly traceable to a confirmed gap,
consistent with this project's "smallest deterministic fix" discipline.
Do **not** chase the other 6 incidents this cycle: `forge_blog_cms`'s
missing-update-endpoint gap needs more corpus evidence before
justifying a fix (2 confirmed instances total now, still below this
project's established bar); the 3 seed-pipeline-reliability incidents
are already tracked and explicitly deferred (Exp097-100); the
2 business-rule/security-design cases likely aren't bugs at all. After
shipping the role-discovery fix, recommend a **second ForgeBench run
(v1.0b, same 25 apps or a fresh set)** specifically to confirm (a) the
fix resolves `event_manager_platform`-shaped incidents live, and (b)
whether the execution-level hang rate (Exp099/ForgeBench v1.0's other
major finding) has improved with a fresh environment — before
committing to a 100-app v1.1.

**Deliverables**: this doc, `experiments.md` entry.
**Cost: $0, zero Cerebras calls, zero code changes.**
