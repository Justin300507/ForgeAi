# Runtime Failure History &amp; Timelines (Experiment 068, Part 5)

2026-07-12. Offline, read-only. Built from `backend/benchmark_results/canary_history.json`'s
32 labeled runs (2026-07-06 through 2026-07-12), cross-referenced against
`experiments.md`'s ~67 numbered entries, plus `app/memory/reliability_metrics.py`'s
own `compute_experiment_attribution()` run live against the real data (not
reimplemented). Every delta below carries the confidence label this
project's own methodology already assigns — **every single pairwise
canary comparison in this dataset is "Low confidence"** (evidence size
n=1-3 app-results per comparison), which the project's own code
(`confidence_from_evidence()`) computes automatically, not something
this experiment is downgrading after the fact. Read every delta below
with that in mind: it shows correlation the project itself has already
flagged as not statistically confirmed, not proof of causation.

## Full chronological canary timeline

| # | Label | Date (2026-07) | todo (build/runtime/crud/browser, score) | blog_cms | crm | inventory |
|---|---|---|---|---|---|---|
|0|m0-quick-wins|06 01:30|T/F/–/–, 76.9|F/F/–/–, 33.0|T/F/–/–, 76.9|–|
|1|m1-contract|06 05:02|F/F/–/–, 25.3|F/F/–/–, 31.5|None/None/–/–, 0.0|–|
|2|m1-contract-gemini|06 05:58|F/F/–/–, 25.5|F/F/–/–, 66.1|T/F/–/–, 65.8|–|
|3|m1-post-filename-fix|06 06:30|T/F/–/–, 76.4|F/T/–/–, 94.1|T/F/–/–, 72.6|–|
|4|m1-contract-OFF-control|06 06:50|T/F/–/–, 76.9|F/F/–/–, 45.0|T/F/–/–, 47.1|–|
|5|m1-config-patcher-fix|06 07:13|T/F/–/–, 76.4|F/F/–/–, 34.3|T/F/–/–, 66.6|–|
|6|m1-journey-crud-fix|06 08:31|T/T/–/–, 99.3|T/F/–/–, 67.5|T/F/–/–, 44.4|–|
|7|m1-querybasemodel-fix|06 09:08|T/T/–/–, 99.3|F/T/–/–, 93.3|T/F/–/–, 65.8|–|
|8|m1-notnullgap-fix|06 09:35|T/F/–/–, 76.4|F/F/–/–, 34.3|T/F/–/–, 72.6|–|
|9|m1-notnullgap-requiredness|06 10:03|T/F/–/–, 67.4|F/T/–/–, 86.2|T/F/–/–, 74.3|–|
|10|m1-signuppage-fix|06 10:39|T/F/–/–, 73.9|T/T/–/–, 87.4|T/F/–/–, 66.9|–|
|11|m1-status-code-fix|06 11:35|T/F/–/–, 73.9|T/F/–/–, 68.3|None/None/–/–, 0.0|–|
|12|m1-retry500-fix|06 11:54|T/F/–/–, 73.9|T/T/–/–, 84.8|T/F/–/–, 66.2|–|
|13|m1-model-driven-schema|06 12:28|T/F/–/–, 73.9|T/F/–/–, 61.5|T/F/–/–, 39.4|–|
|14|m1-model-driven-schema-confirm|06 12:52|T/F/–/–, 73.9|T/T/–/–, 90.3|T/T/–/–, 91.4|–|
|15|m1-seed-before-crud|06 13:49|T/F/–/–, 73.9|T/T/–/–, 90.3|T/T/–/–, 91.6|–|
|16|adr002-deterministic-seeder|06 20:33|T/F/**F**/T, 76.0|T/T/**F**/T, 87.3|T/T/**F**/T, 88.6|–|
|17|adr002-deterministic-seeder-confirm|07 01:37|T/F/F/T, 76.0|T/T/F/T, 83.3|T/T/F/T, 87.5|–|
|18|router-export-mismatch-fix|07 11:51|T/T/F/T, 91.2|T/T/**T**/T, 86.7|T/F/F/T, 72.3|–|
|19|dictunpack-modeldump-fix|07 12:16|T/T/F/T, 91.2|T/T/F/T, 85.9|T/F/–/–, 36.4|–|
|20|pydantic-config-patch-confirm|07 12:47|T/T/F/T, 91.2|T/T/F/T, 85.9|T/T/F/T, 87.8|–|
|21|m2-endpointfix-themekit|10 02:13|**F**/F/–/–, 42.0|T/T/**T**/**F**, 81.1|**F**/T/–/–, 87.3|–|
|22|m3-relationship-dedupe-confirm|10 14:51|T/T/–/–, 90.7|T/T/–/–, 89.0|F/T/–/–, 82.9|–|
|23|post-exp047-model-integrity|11 11:40|**F**/T/–/–, 88.8|T/T/–/–, 91.6|T/T/–/–, 83.6|–|
|24|exp048-regen-cache-bypass|11 12:51|T/T/–/–, 99.7|T/**F**/–/–, 65.9|None/None/–/–, 0.0|–|
|25|exp056-baseline-r1|11 21:12|T/F/F/T, 74.4|T/F/F/T, 72.1|T/T/**T**/T, 89.9|–|
|26|exp056-baseline-r2|11 21:09*|T/F/F/T, 74.4|T/F/F/T, 70.9|–|–|
|27|exp058-validation-r1|11 21:39|T/F/F/T, 70.7|–|–|–|
|28|exp058-validation-r2|11 21:47|T/F/F/T, 71.5|–|–|–|
|29|exp061-validation-r1|11 22:41|T/F/F/T, 74.4|–|–|–|
|30|exp061-validation-r2|11 23:01|T/F/F/T, 73.3|–|–|–|
|31|exp062-cross-app|11 23:24|–|T/F/F/T, 71.5|T/T/T/T, 88.6|T/F/T/T, 78.1|

*Run 26's stored timestamp (21:09) is earlier than run 25's (21:12) despite appearing later in the array — a data-quality note, not analyzed further.

## Stage-level trends

- **build_ok**: trended toward consistently `True`. In runs 0-13, blog_cms fails build 8/14 times; from run 14 onward blog_cms is `True` every single time. todo and crm are mostly `True` throughout, with isolated regressions (runs 21, 23 for todo; run 22 for crm) and two zero-score/`None` runs each (the provider-exhaustion signature, see below).
- **runtime_ok**: improved markedly but never reached full consistency. todo flips repeatedly even in late runs (`True` at 18-24, back to `False` at 25-30). blog_cms stabilizes `True` from run 6 onward with occasional lapses (24, 25, 26). crm is the most consistently `True` from run 14 onward.
- **crud_ok**: first measured at run 16 (`adr002-deterministic-seeder`, 2026-07-06T20:33 — **Experiment 020**). Before that it was never measured (`null` in every run 0-15). Once measured, it is `False` far more often than `True` — `True` only at runs 18(blog_cms), 21(blog_cms), 25(crm), 31(crm, inventory). **`todo`'s `crud_ok` has never once been `True` in this entire dataset** — every measured run from 16 through 31, zero exceptions. `crm` has the best record (`True` in 2 of its ~9 measured runs).

## Canary label → Experiment number mapping, with before/after deltas

| Canary label | Experiment # | What changed | Score delta (this run vs. immediately preceding) |
|---|---|---|---|
| m0-quick-wins | 001 | baseline | n/a (first run) |
| m1-contract | 002 (documented INCONCLUSIVE) | ContractConformanceValidator warn-only wiring | todo 76.9→25.3, crm 76.9→**0.0** (build=`None`) — experiments.md itself attributes this to provider exhaustion, and the `build=None`/score-0.0 signature matches that explanation |
| m1-contract-gemini | 005 | same validator, forced Gemini to remove the quota confound | todo 25.3→25.5 (flat — a genuine, non-quota signal); blog_cms 31.5→66.1 |
| m1-post-filename-fix | 007 | querystring-in-filename fix (Exp006, commit 4af31b4) | todo 25.5→**76.4** (recovered) |
| m1-contract-OFF-control | 008 (A/B control arm) | `FORGE_CONTRACT_CHECK=0` | blog_cms 94.1→45.0 — high generation-to-generation variance, not a clean single-variable read |
| m1-config-patcher-fix | 009 | preflight config-patcher fix | blog_cms 45.0→34.3 (no clear improvement visible at this granularity) |
| m1-journey-crud-fix | 010 | JourneyCRUDFailure 422-coercion fix | todo 76.4→99.3, **runtime False→True** |
| m1-querybasemodel-fix | 011 | BaseModel-as-Query-param fix | blog_cms 67.5→93.3 |
| m1-notnullgap-fix | 012 | NOT NULL model/schema gap fix | blog_cms 93.3→34.3 (large drop — plausibly generation variance, not attributable to this fix specifically) |
| m1-notnullgap-requiredness | 013 | requiredness refinement | blog_cms 34.3→86.2 (recovered), todo 76.4→67.4 (regressed) |
| m1-signuppage-fix | 014 | frontend missing-import scaffolder | blog_cms 86.2→87.4, **runtime False→True** |
| m1-status-code-fix | 015 | journey-runner status-code fix | crm 66.9→**0.0** (provider-exhaustion signature again) |
| m1-retry500-fix | 016 | 422-retry false-positive fix | blog_cms 68.3→84.8, **runtime False→True** |
| m1-model-driven-schema | 017 | model-driven schema generation | crm 66.2→39.4 (regressed) |
| m1-model-driven-schema-confirm | 018 | confirming run | crm 39.4→**91.4**, **runtime False→True**; blog_cms 61.5→90.3 |
| m1-seed-before-crud | 019 | seed reference data before CRUD | crm 91.4→91.6 (flat — already fixed by 018) |
| adr002-deterministic-seeder | 020 | deterministic seeder — **crud_ok measurement begins here** | all 3 apps crud_ok=False (new metric's baseline) |
| router-export-mismatch-fix | 021 | deterministic RouterExportMismatch repair | todo **runtime False→True** (91.2), blog_cms **crud False→True** |
| dictunpack-modeldump-fix | 022 | dict-unpack-constructor fix | crm 87.5→36.4 (regressed hard; not explicitly explained in Exp022's own entry) |
| pydantic-config-patch-confirm | 023 | ConfigAttributeError patch confirm | crm 36.4→87.8 (recovered) |
| m2-endpointfix-themekit | 029+031 (dual-attributed in experiments.md) | endpoint fix + theme kit | todo 91.2→**42.0**, build True→False — a 3-day, 5-experiment gap (024-028) with no intermediate canary makes this not cleanly attributable to just 029/031 |
| m3-relationship-dedupe-confirm | 034 | relationship-normalization + import-dedupe | todo 42.0→90.7 (recovered) |
| post-exp047-model-integrity | 047 (confirm) | model-integrity dedup | todo build True→False (mild, score 90.7→88.8) despite runtime staying True |
| exp048-regen-cache-bypass | 048 | regen-strategy cache bypass fix | todo 88.8→99.7; blog_cms 91.6→65.9 (regressed, runtime True→False); crm →**0.0** — experiments.md's own entry title says this run was quota-confounded |
| exp056-baseline-r1/r2 | 056 (measurement-only) | no code change, baseline remeasure | todo 99.7→74.4 (large drop across the unmeasured Exp049-055 gap — not attributable to Exp056 itself) |
| exp058, exp061 validation runs | 058, 061 | live regression validation, validator contract check | todo flat ~70-74 across all 4 runs |
| exp062-cross-app | 062 | cross-app investigation, adds 4th app "inventory" | crm **crud False→True** (88.6); inventory crud=True on its first-ever measurement |

## crud_ok / JourneyCRUDFailure: did it get fixed?

Measurement began at Experiment 020. Across all 16 measured runs since (16-31), `crud_ok` is `True` in only 5 of ~34 app-measurements total (blog_cms at Exp021 and the m2-themekit run; crm at exp056-r1 and exp062; inventory at exp062, its first measurement). **No experiment in this canary history measurably fixed `crud_ok` for the `todo` app — it has never once shown `True`.** This directly corroborates the separately-run `failure_report.py` output (CRUD journey pass rate 18.5% over the last 30 generations) — the canary-level view and the broader generation-log view independently agree on the same conclusion.

## runtime_ok: did it get fixed?

Clear improvement trend from runs 0-8 (mostly `False`) to runs 14+ (mostly `True`), with visible `False→True` flips immediately following Experiments 010, 014, 016, 018, and 021. This reads as a genuine, **cumulative, multi-experiment improvement** — but per this project's own confidence methodology, no single one of these flips is individually "confirmed causation" (each pairwise comparison has n=1-3 evidence and is labeled Low confidence by `compute_experiment_attribution()`). The pattern across five separate experiments each showing the same directional effect is the actual evidence, not any one comparison alone.

## Regressions found in the raw numbers, and whether experiments.md already explains them

| Transition | App | Metric | Before→After | Matches experiments.md's own explanation? |
|---|---|---|---|---|
| run 1 (m1-contract, Exp002) | todo, crm | build/score | 76.9→25.3 / 76.9→0.0 | **Yes** — Exp002 is explicitly labeled "INCONCLUSIVE — provider exhaustion" |
| run 11 (m1-status-code-fix, Exp015) | crm | score | 66.9→0.0 | Not explicitly called out in Exp015's entry; the `build=None` signature matches the same provider-exhaustion pattern as runs 1 and 24 |
| run 19 (dictunpack-modeldump-fix, Exp022) | crm | score | 87.5→36.4 | No explicit regression callout found in Exp022's entry; recovered the very next run (023) |
| run 21 (m2-endpointfix-themekit, Exp029+031) | todo | build/score | True/91.2→False/42.0 | Spans a 3-day, 5-experiment gap (024-028) with no intermediate canary — not cleanly attributable to 029/031 alone from this data |
| run 23 (post-exp047-model-integrity) | todo | build | True→False (mild, 90.7→88.8) | Not flagged as a regression in Exp047's own entry (a $0, no-LLM-call dedup fix — plausibly ordinary generation variance) |
| run 24 (exp048-regen-cache-bypass) | blog_cms, crm | runtime/score | blog_cms True→False (91.6→65.9); crm →0.0 | **Yes** — Exp048's own entry title states the canary was quota-confounded |
| run 25 (exp056-baseline-r1) | todo | score | 99.7→74.4 | Exp056 is explicitly "measurement only" — the actual drop happened somewhere in the unmeasured Exp049-055 gap, not attributable to Exp056 |

**Unknown, not confirmed either way**: whether runs 11, 19, and 23's unexplained regressions reflect a real code regression or ordinary LLM generation variance (this project's own variance report puts single-run forge_score stdev around 24 points) — distinguishing these would require a fixed-seed re-run, which is outside this experiment's evidence-only, no-generation scope.

## Did reliability improve overall across Experiments 048-067 (the "infrastructure hardening" arc this Experiment 068 was commissioned to evaluate)?

Per `compute_observatory()` run live against the current data: **`first_try_success_rate` (the project's own designated North-Star metric) is 30.0% over the last 30 generations, trending -6.7 points versus the window immediately before it.** `generation_success_rate` is 40.0%. The single largest cluster by volume, `MissingEndpoint`, and the largest integration-stage cluster, `JourneyCRUDFailure`, both still show their most recent `last_seen` dates on 2026-07-11 — i.e., during Experiment 062-067's own window, not resolved by the infrastructure-hardening arc. This is direct, current-data support for this Experiment 068's own commissioning premise ("infrastructure hardening has reached diminishing returns") — not an assumption carried over from the prompt, but a number this cycle's own data reproduces independently.
