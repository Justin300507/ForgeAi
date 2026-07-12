# Security Review (Experiment 065, Part 9)

2026-07-12. Offline, static analysis only — no dynamic testing, no live
exploitation attempts. ForgeAI's core function is generating and
executing LLM-produced code, and managing deployment credentials, so
this review treats LLM output as semi-trusted (not fully attacker-controlled,
but not fully trusted either — a manipulated or adversarial provider
response is a real, if narrow, threat model for this kind of system).

## 1. Unsafe subprocesses — clean, plus one architectural note

**Zero `shell=True` and zero `os.system()` usage anywhere in `app/`** —
confirmed via exhaustive grep. All 24 `subprocess.run`/`Popen` call
sites pass argument lists, not shell strings — no shell-metacharacter
injection is possible by construction.

**Architectural note, not a code bug**: `app/runtime/backend_runner.py:203`
directly executes LLM-generated application code natively
(`subprocess.Popen([sys.executable, "-m", "uvicorn", "app.main:app", ...])`),
unsandboxed, with the same OS privileges as the ForgeAI backend process
itself. This is the **default** verification path — Docker-based
validation (`app/runtime/docker_validator.py`) exists but gracefully
skips if Docker isn't installed (per its own docstring), i.e. it's not
guaranteed to be the active path. This is inherent to the product's
design (verify generated code by running it), not a fixable line of
code — but it is the single most important structural risk to flag
explicitly before a commercial release: **there is no sandbox/container
isolation on the default path between arbitrary LLM-generated Python
and the host.**

## 2. Unsafe path joins — HIGH severity, confirmed real finding

**`app/services/file_writer_service.py:552-573` (`write_files`, the
initial-generation file-writer) has zero path-traversal validation.**
It takes `path = file["path"]` directly from the LLM's own generated
file list and does `full_path = os.path.join(base_dir, path)` — no
`..`-prefix check, no `os.path.isabs()` check. Confirmed by direct read:
no such guard exists anywhere in this function or its callees.
`os.makedirs(os.path.dirname(full_path), exist_ok=True)` (lines 575-578)
would even create the necessary parent directories outside the intended
sandbox.

**This is asymmetric with a known-good sibling**:
`app/services/fix_writer_service.py::write_fix` (the repair-time
writer, hardened across Exp060/064's own work this cycle) *does* have
this exact guard — `norm.startswith("..")` / `os.path.isabs(norm)` →
`"write_fix: blocked suspicious path"`. **The repair path was hardened;
the initial-generation path — which processes the LLM's first, least-scrutinized
output — was not.**

**Severity: HIGH.** Attacker-controllability: LLM output is
semi-trusted — a manipulated/compromised provider response, or an LLM
hallucinating an adversarial path in response to unusual prompt
content, could plausibly return `"path": "../../../../some/sensitive/file"`.
Blast radius: arbitrary file write outside the intended
`generated_projects/<name>/` sandbox, with the backend process's own OS
privileges. No mitigating control exists on this path today. **This is
the single highest-value security finding in this review** — a
well-scoped, low-risk fix (literally the same guard `write_fix` already
has, applied to a second function) with a clear, confirmed vulnerability
behind it.

## 3. Unsafe temp files — Unknown, not completed this cycle

Not exhaustively checked — a targeted `tempfile.`/`mkdtemp` sweep for
predictable-name / non-`mkstemp` patterns was not completed within this
review's scope. Flagged as incomplete, not asserted clean.

## 4. Unsafe shell calls — clean

Same evidence as #1 — zero shell interpretation anywhere; LLM output
never reaches a shell interpreter.

## 5. Unsafe eval — clean

**Zero `eval(`/`exec(` calls anywhere in `app/`** — confirmed via
exhaustive grep.

## 6. Unsafe YAML — not applicable

**Zero `yaml.load(` calls found**, safe or unsafe. No YAML
deserialization anywhere in the reviewed code.

## 7. Unsafe pickle — not applicable

**Zero `pickle.load`/`pickle.loads` calls found.**

## 8. Unsafe archive extraction — not applicable, confirmed by absence

`app/services/zip_service.py` uses `zipfile.ZipFile` in **write mode
only** (creating `.zip` exports of generated projects). **Zero
`extractall()` calls found anywhere in `app/`** — there is no
extraction code path at all, so zip-slip does not apply.

## 9. Unsafe uploads — not applicable to ForgeAI itself

No file-upload endpoint exists in ForgeAI's own API. The only
`UploadFile`/`File(` match (`app/knowledge/component_db.py:39`) is a
string-literal entry in a pattern-knowledge-base used to help *generate*
upload features in **user-generated apps**, not ForgeAI's own backend.

## 10. Unsafe HTTP — clean

**Zero `verify=False` occurrences** across all 14 files making
`requests`/`httpx` calls. (Timeout coverage for these same 14 call
sites is Part 3/Reliability's territory — see `docs/RELIABILITY_REVIEW.md`,
not duplicated here.)

## 11. Unsafe secrets — clean

**Zero hardcoded-looking API keys/tokens/passwords found** via pattern
grep across `app/`. All credential access goes through `os.getenv(...)`.

## 12. Unsafe logging — Low severity, needs a targeted follow-up

Provider-failure paths (`app/providers/ai_provider.py`, 9 sites, e.g.
lines 86/98/110/176/187/199/208/220/229) print the raw SDK exception
(`print(f"Gemini failed: {e}")`) to stdout/server logs. Most SDK
exceptions don't echo request headers/API keys by default, but this
wasn't verified across every provider SDK version. **Unknown** whether
this output ever reaches an external-facing channel — the
`EventBus`/`log_fn` WebSocket streaming path forwards specific
structured events (`STAGE_START`, `SCORE_UPDATE`, etc.), not a raw-stdout
mirror, based on what was read, but this wasn't traced exhaustively.
Flagged as needing a targeted follow-up, not confirmed exploitable.

---

## Risk-ranked summary

| Finding | Severity | Location | Exploitability confidence |
|---|---|---|---|
| `write_files` has no path-traversal guard (unlike `write_fix`) | **HIGH** | `app/services/file_writer_service.py:552-573` | High — confirmed by direct code read, asymmetry with a known-good sibling implementation |
| Native, unsandboxed execution of LLM-generated code is the default verification path | Informational/Architectural | `app/runtime/backend_runner.py:203` | N/A — inherent to product design, a pre-commercial-release decision point, not a bug |
| Provider-failure logging could theoretically echo sensitive SDK response content | Low | `app/providers/ai_provider.py` (9 sites) | Unknown — not traced to an external-facing sink |
| Unsafe subprocesses / shell calls / eval / YAML / pickle / archive extraction / uploads / `verify=False` / hardcoded secrets | None found | (exhaustive greps) | Clean, confirmed by absence, not sampling |
| Unsafe temp files | Unknown | not checked this cycle | — |

**Top recommendation from this Part**: fix Finding #1
(`write_files`'s missing path-traversal guard) — it is the exact same
fix already proven correct and shipped in `write_fix`, applied to a
second, higher-exposure function (the LLM's first, least-scrutinized
output). Low implementation risk, confirmed real gap, directly
analogous to work already done this cycle.
