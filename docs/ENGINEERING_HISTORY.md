# ForgeAI Engineering History (Experiment 069, Part 3)

2026-07-12. Every one of the 68 numbered entries in `experiments.md`
read in full (not sampled, not title-only) and compiled into a single
timeline table. Source: `experiments.md` at the repo root, 4792 lines.

**Data-quality anomaly found, not resolved**: Experiment 059's own
text contains the finding *"Exp064's new semantic-consistency guard
does NOT cover repairs made through regenerate_arch/regenerate_module
— a real gap found one cycle after shipping it"* — but Exp064 (which
ships that guard) appears **after** Exp059 in the file's numbering and
reading order. Experiment 065 contains a near-identical finding,
phrased almost identically. Either Exp059's text was edited/appended
after Exp064 shipped (a later cross-reference inserted into an earlier
entry), or this is a genuine duplication/transcription artifact in the
source file. Flagged here per "Unknown means Unknown" rather than
silently resolved one way or the other.

| # | Date | Title | Problem | Root Cause | Fix | Verification | Regr. Tests | Status | Subsystem(s) |
|---|---|---|---|---|---|---|---|---|---|
| 001 | 07-06 | m0-quick-wins baseline | N/A — establish baseline | N/A | N/A | 3-app canary | No | Baseline set | Infra/measurement |
| 002 | 07-06 | m1-contract (INCONCLUSIVE) | Does warn-only ContractConformanceValidator regress anything? | Provider exhaustion (Groq near-limit, Gemini 503s), not the validator | None (investigation) | Canary vs Exp001 | No | INCONCLUSIVE, superseded by 005 | Validation/Provider |
| 003 | 07-06 | generation_log telemetry bug | V15 telemetry blind since 06-28 | `pipeline.py:334` `getattr` on a method not property, swallowed by bare `except` | Call `ctx.all_diagnostics()` | Local repro | No | Shipped 862d393 | Observatory |
| 004 | 07-06 | Silent-exception audit | Hidden failures like Exp003's | 71 `except` blocks reviewed; 10 real silent gaps found | Structured logging added; `_ProjectSnapshot.revert()` now reports incomplete | `ast.parse` + live import of 5 modules | No | Shipped 4287344 | Repair |
| 005 | 07-06 | m1-contract-gemini clean run | Re-test 002 without quota confound | Same M1 code; found confidence-engine bug (2nd instance of Exp003's bug class) | N/A + confidence-engine fix | Live canary + local repro | No | Inconclusive on AppContract; confidence fix shipped cbb46fb | Provider/Confidence |
| 006 | 07-06 | Filename-sanitization fix | Querystring-in-filename crash | `endpoint_validator.py:207` didn't strip querystring before deriving filename | Fixed + 3 other call sites | Isolated old-vs-new repro | No | Shipped 4af31b4 | Generation |
| 007 | 07-06 | m1-post-filename-fix re-canary | Did filename fix restore todo? | N/A | N/A | Canary vs Exp001 | No | Confirmed recovered | Generation |
| 008 | 07-06 | Controlled AppContract A/B | Does the validator reduce contract-coherence failures? | Score gap NOT from contract stage — 2 preflight config-patcher blind spots | None (root-caused only) | Canary A/B | No | INCONCLUSIVE on AppContract | Validation/Repair |
| 009 | 07-06 | Preflight config-patcher fix | Fix Exp008's 2 blind spots | Instance-only patch + exact-case-only key match | Patch class-level, both cases | Canary + 3 unit tests | Yes | KEEP, confirmed | Repair |
| 010 | 07-06 | JourneyCRUDFailure 422-coercion fix | Reduce cascade failures | N/A | Coercion table for 422 mismatches | Canary | No | KEEP; found new Enum-as-BaseModel bug | Runtime |
| 011 | 07-06 | BaseModel-Query-param fix | AssertionError on Query param typed as BaseModel | Enum-shaped field emitted as empty BaseModel | Loosen annotation | Canary | No | KEEP, confirmed | Repair |
| 012 | 07-06 | NOT NULL model/schema gap fix | IntegrityError on insert | Uncoordinated model-gen vs schema-gen waves | `_fix_model_schema_notnull_gap` | Canary + root-cause trace | No | KEEP but incomplete (found same day) | Repair/Generation |
| 013 | 07-06 | NOT NULL gap requiredness refinement | Exp012 incomplete | Presence-only check let Optional falsely cover NOT NULL | Check requiredness, not presence | Canary vs 012 | No | KEEP | Repair |
| 014 | 07-06 | Frontend missing-import scaffolder wiring | Vite "could not resolve" errors, 8/9 runs | `create_missing_stubs()` existed, never called in V15 | Wired into preflight priority 23 | Canary + corpus grep | No | KEEP | Generation/Repair |
| 015 | 07-06 | Journey-runner status-code fix | Failures showed "?" not real status | `requests.Response.__bool__` returns `.ok`; falsy on 400+ | `is not None` check | Local repro + canary | No | KEEP | Runtime |
| 016 | 07-06 | 422-retry false-positive fix | A retry that itself 500s reported "passed" | `do_create()` only branched on 200/201/422 | New `elif status>=500` | Local repro + canary | No | KEEP | Runtime |
| 017 | 07-06 | Model-driven schema generation | Model/schema field-name drift | Wave3's filename lookup + Wave2.5 shim pollution | New `entity_metadata.py` extractor, default OFF | Canary (both regressions root-caused as unrelated) | No | KEEP, not promoted yet | Generation |
| 018 | 07-06 | Model-driven schema, confirm + promote | Consistency check | N/A | Promote flag to default ON | Canary — first-ever full CRUD pass, both apps | No | PROMOTED to default | Generation |
| 019 | 07-06 | Seed reference data before CRUD | Persistent unseeded-table 400 | Journey runner never called `/seed` | `POST /seed` added after login | Canary + local test | No | KEEP, gated by a separate factor | Runtime |
| 020 | 07-06 | Deterministic seeder (ADR-002) | Seed-stub fallback gates Exp019 | LLM sometimes omits seed_routes.py | Deterministic entity-metadata-driven seed generator | 35 unit tests + targeted live verification | Yes | VALIDATED, ADR-002 | Generation/Repair |
| 021 | 07-06 | RouterExportMismatch deterministic repair | Route file exports wrong name | N/A | `_patch_router_export_mismatch` | 3 fixture tests + canary | Yes | KEEP | Repair |
| 022 | 07-06 | Dict-unpack-constructor patcher fix | Malformed `.model_dump()` calls recur | Regex `\b` boundary left dangling call | Negative lookahead added | 2 fixtures + canary | Yes | KEEP | Repair |
| 023 | 07-06 | ConfigAttributeError patch confirm | Confirm commit 85514e5 | N/A | N/A | Canary, zero occurrences | No | KEEP, FREEZE | Repair |
| 024 | ~07-07 | ADR-001 ext Phase A: relationship extraction | entity_metadata.py can't capture relationships | N/A (feature) | `RelationshipDefinition` + parsing | 35+5 fixture tests | Yes | KEEP | Generation |
| 025 | ~07-07 | ADR-001 ext Phase B: activate dormant validator | Relationship-target check permanently inert | `from_architecture_plan()` never carried relationship data | `enrich_relationships_from_models()` | 40+5 tests | Yes | KEEP | Validation |
| 026 | ~07-07 | Consolidate Render build/start commands | 2 independent hardcodes, sync by coincidence | No shared source of truth | `render_config.py` shared constants | Unit test + full suite | Yes | KEEP | Deployment |
| 027 | ~07-07 | ADR-001 ext Phase D + integrations | Complete relationship-kind derivation | N/A (feature) | `derive_relationship_kinds()`, schema manifest, seeder integration | 18 new, 66 total | Yes | KEEP | Generation/Repair |
| 028 | ~07-07 | Frontend/dependency reliability verification | Are Import/ModuleNotFound errors still active? | Dormant >1 week | None | Telemetry staleness check | No | NO FIX NEEDED | Generation |
| 029 | ~07-07 | MissingEndpoint root cause | blog_cms naming mismatch | Frontend hallucinates endpoints not in architecture | Prompt constraint added | Local render tests only | No | IMPLEMENTED, validation pending | Generation |
| 030 | ~07-07 | Repair-cache + schema edge cases verification | Are these open gaps? | Cache well-designed; error classes are stale pre-freeze data | None | Live data inspection | No | NO FIX NEEDED | Repair |
| 031 | 07-09 | Forge Motion & Theme Kit | Inconsistent visual polish | Design foundation LLM-remembered, not scaffold-provided | `theme_builder.py`, motion tokens, ErrorBoundary | 35/35 render matrix + real vite build | No | Shipped, canary pending | Generation |
| 032 | ~07-10 | Gemini model retirement fallback | gemini-2.5/2.0-flash retired, 404 | Google retired models silently | Ordered candidate list + blacklisting | Live call verification | No | Shipped | Provider |
| 033 | 07-10 | m2 canary: 2 new Gemini-3 idiom fixes | New idioms crash output | Gemini 3-specific patterns not covered | Relationship normalization + import dedup patcher | Force-the-path on real broken files | No | Shipped | Generation/Repair |
| 034 | 07-10 | m3 canary: confirm Exp033 | Do fixes hold up? | N/A | N/A | Canary vs true baseline | No | KEEP, confirmed | Generation/Repair |
| 035 | 07-11 | Design Intelligence v2 + Design Memory | Generic visual identity | N/A (feature) | `app/design/` pipeline, layout axis, similarity-gated memory | 36 asserts + 1 live generation | Yes | Shipped | Generation |
| 036 | ~07-11 | Icon-validity guardrail | "X not exported" build failures | Bad vocab, bad whitelist, no hallucination patcher | Ground-truth export list + rename patcher | 8 asserts + real canary output | Yes | Shipped | Generation/Repair |
| 037 | ~07-11 | V20 Reliability Engine | Blind spots in failure-memory loop | #1 pattern had no prevention rule; classifier only 7 substrings | Prevention rules, unified classifier, stage dashboard | 7 asserts + all suites | Yes | Shipped | Observatory |
| 038 | ~07-11 | Forensic Bundle System (V20.1) | Failure evidence truncated then discarded | Evidence computed then thrown away | Generic bundle writer + redaction + log wiring | 13 asserts across 4 files | Yes | Shipped | Observatory |
| 039 | ~07-11 | Kill Playwright harness's phantom CRUD failures | JourneyCRUDFailure dominant class | Duplicate hardcoded journey impl drifted | Stage 10 reuses Stage 3's journey_result | 5 unit + targeted live verification | Yes | Shipped | Runtime |
| 040 | ~07-11 | Symbol Validation stage | Missing imported names, not just files | Names imported but never defined anywhere | New Stage 2a-symbols, AST resolution | 8 tests + swept 53 real projects | Yes | Shipped | Validation |
| 041 | ~07-11 | Endpoint smoke tests hardcoded port 8001 | Guaranteed connection-refused when port≠8001 | `base_url` never passed through | Port param threaded through | 1 test + live 7%→100% | Yes | Shipped; 2 canaries killed by infra | Runtime |
| 042 | ~07-11 | Python syntax gate + Create/Update field completion | Repair path had no syntax gate | Repair wrote syntactically broken Python unconditionally | `_python_syntax_error()` gate + field-completion patcher | 13 tests + corpus sweep (2 self-caught bugs fixed) | Yes | Shipped | Repair |
| 043 | ~07-11 | Prevention Rate KPI + Role-Aware Validation | No prevention KPI; role-gated routes untestable | Discarded return values; hardcoded role="user" | `prevention_counts` dict + role discovery + elevation retry | 16 tests + all suites + live 6/11→11/11 | Yes | Shipped | Observatory/Runtime/Generation |
| 044 | ~07-11 | Observatory data quality + 3-bug chain | Validate role-aware on 2nd app | Role discovery schema-only; response-schema inheritance gap | Confidence labeling; route-level fallback; inherited-field patcher | 14 tests + all suites + live 6/11→11/11 | Yes | Shipped | Observatory/Runtime/Repair |
| 045 | ~07-11 | Model-column fallback for missing fields | Schema-only corroboration blind spot | Field on model, absent from all schemas | Model-column fallback corroboration | 4 tests + corpus sweep | Yes | Shipped | Repair |
| 046 | ~07-11 | FK ownership drift audit | Synonym patcher missing reverse direction | Real FK vs unrelated always-0 column — silent data-isolation bug | `_patch_ownership_fk_attribute_drift` | 7 tests + corpus sweep, 0 false positives | Yes | Shipped | Repair/Security |
| 047 | ~07-12 | Model-integrity dedup: singular/plural gap | 2/3 sub-mechanisms already fixed | Dedup missed singular/plural class-name variants | Extended for name-variant detection | 7 tests + corpus sweep + full re-run | Yes | Shipped | Repair |
| 048 | 07-11/12 | Regen-strategy cache bypass | todo regression after Exp047 canary | REGENERATE_ARCH hit LLM cache, overwrote already-fixed files | Disable FORGE_LLM_CACHE during regen | 3 tests + canary (confounded) + retroactive log scan | Yes | Shipped + canary-lock same day | Repair |
| 049 | 07-12 | Broken template-literal className collapse | FrontendBuildError #1 class, zero detection | Bare backtick instead of `${` for ternary interpolation | Detect+collapse pattern | 8 tests vs real esbuild; corpus sweep 882 files | Yes | Shipped, not yet live-validated | Repair |
| 050 | 07-12 | Observatory cockpit page | No dashboard UI despite compute functions existing | Functions never wired to any endpoint | `GET /observatory` + `Observatory.jsx` | 7 tests + real Playwright screenshots | Yes | Shipped | Observatory |
| 051 | 07-12 | Reliability Debt Audit of repair pipeline | Audit before debt becomes bugs | 8/114 (7%) repair functions tested; 4 duplicate dispatch mechanisms | None (docs only) | Fork enumeration | No | Docs shipped; process incident logged | Repair/Process |
| 052 | 07-12 | Deterministic Repair Test Coverage Initiative | Low coverage from Exp051 | N/A (coverage build); 4 real bugs found via new tests | Coverage 7%→82%; 4 bugs fixed | Independent re-execution of every fork's tests | Yes | Shipped, 4 bugs fixed | Repair |
| 053 | 07-12 | Repair Pipeline Consolidation | Reduce duplicate infra without behavior change | N/A | Brace-matcher dedup, failure isolation, Stage1 extraction | Tests before/after, 40 new tests | Yes | Shipped; **CAUSED A REGRESSION** (found by 056) | Repair |
| 054 | ~07-12 | Fix FastAPI param-order bracket-tracking bug | Exp053-flagged bug | `_split_params` tracked only `()`, corrupted `Dict[str,int]` sigs | Track `[]` too + compile() gate before write | Direct repro + regression test + full suite | Yes | Shipped | Repair |
| 055 | ~07-12 | Repair Failure Isolation: run_frontend_patches | Exp053-flagged missing isolation | One bad patcher could 500 the whole resync | `FrontendPatchResult` + isolated wrapper | git-stash repro + 13 tests + full suite | Yes | Shipped | Repair |
| 056 | ~07-12 | Post-Hardening Reliability Baseline (measurement only) | Establish baseline after Exp048-055 | **Found real Exp053 regression**: NameError from Stage-1 extraction | None this exp | 5 live generations, 2 independent evidence sources | No | MEASUREMENT ONLY; regression ranked #1 for Exp057 | Repair/Measurement |
| 057 | ~07-12 | Restore Runtime Repair Loop | Fix Exp056's found regression | Exp053's extraction moved a local import out of scope | Widened import statement | 7 tests + git-stash exact replay + full suite | Yes | Shipped, confirmed via exact replay | Repair |
| 058 | ~07-12 | Live Regression Validation (Cerebras) | Validate Exp057's fix live | N/A | N/A | 2 todo-only canary runs | No | Fix confirmed; score did NOT recover (separate defect) | Repair/Measurement |
| 059 | 07-12 | Principal Engineer Reliability & Architecture Review | Offline 10-part deep review | Multiple (135-complexity hotspot, 4 incompatible validator shapes, ~20 redundant os.walk calls, rule-table drift, contradictory docs) | None (docs only) | 5 parallel forks + direct synthesis | No | Docs shipped, no code changed | Architecture |
| 060 | 07-12 | Validator Contract Unification | Unify 4 incompatible result shapes | Naive conversion would crash 15 unhashable-Diagnostic call sites | Additive parallel `diagnostics` field, 15 validators migrated | 23 tests + full suite + real end-to-end | Yes | Shipped | Validation |
| 061 | 07-12 | Live Validator Contract Validation | Validate Exp060 live | Regex-fallback path never exercised live (honest evidence gap) | None (validation only) | Observer-pattern monkeypatch + 2 canary rounds | No | Design confirmed; evidence gap flagged | Validation |
| 062 | 07-12 | Cross-App Reliability Investigation | todo-specific or systemic? | **Confirmed systemic**: Pydantic AttributeError hit 3/4 apps, different field each time | None (investigation) | Observer-pattern script | No | Answered "systemic"; set up Exp063 | Generation/Measurement |
| 063 | 07-12 | Pydantic AttributeError Root Cause Investigation | Why does the pattern recur? | TWO causes: repair-introduced regression (auth case) + first-pass generation defect (inventory case) | None (investigation only) | Direct code + byte-comparison + artifact tracing | No | Investigation only; recommendation → Exp064 | Repair/Generation |
| 064 | 07-12 | Semantic Write Validation | Close Exp063's gap | `write_fix()` had no semantic self-consistency check | `_check_request_field_consistency`, AST-based | 24 tests + replay against real corrupted files | Yes | Shipped, recommended permanent | Repair |
| 065 | 07-12 | Principal Engineer Deep Architecture Audit | Find next year of work pre-commercial-release | Multiple (write_files() zero path validation; subprocess cleanup not in finally; regenerate_arch bypasses Exp064's guard) | None (docs only) | 5 parallel forks | No | Docs shipped, no code changed | Architecture/Security |
| 066 | 07-12 | Write Pipeline Hardening | Close Exp065 Finding #1 | `write_files()` zero traversal check | `safe_path.py` + `atomic_write.py` shared modules | 32 tests + full suite + malicious-path smoke tests | Yes | Shipped | Write pipeline |
| 067 | 07-12 | Complete Write Pipeline Symmetry | Harden 3rd write path Exp066 left untouched | **Corrected premise**: 5 call sites share the pattern, not 1; also corrected Exp066's own wrong assumption | Scoped drive/UNC check + atomic write on `_regenerate_module` only | 21 tests + full suite + self-caught API-call violation fixed | Yes | Shipped | Write pipeline |
| 068 | 07-12 | Runtime Failure Intelligence | Build first Runtime Failure Knowledge Base | None (measurement); JourneyCRUDFailure = 4 root causes (64% missing /auth/register); fix_count≥2 almost never succeeds | None (docs only) | 3 parallel forks + direct compute runs | No | Docs shipped, no code changed | Runtime |

**Full reliability-evolution analysis (what actually moved the needle,
what didn't, ranked verdicts, recurring confound patterns) is in
`docs/RELIABILITY_EVOLUTION.md`, Part 4 of this same experiment.**
