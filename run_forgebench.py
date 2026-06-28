"""
run_forgebench.py — Public ForgeBench runner with adapter interface.

Runs any ForgeBench suite against any code-generation backend — ForgeAI,
Bolt, Lovable, v0, or any HTTP-accessible service — and produces a
standardised ForgeBench scorecard.

Usage
-----
  # Run ForgeBench-20 against local ForgeAI:
  python run_forgebench.py --suite golden --adapter forgeai

  # Run ForgeBench-RT against a custom HTTP adapter at localhost:9000:
  python run_forgebench.py --suite forgebench_realtime --adapter http --adapter-url http://localhost:9000/generate

  # Dry-run (list prompts, do not generate):
  python run_forgebench.py --suite forgebench_ai --dry-run

  # Save results to a named output directory:
  python run_forgebench.py --suite golden --adapter forgeai --out results/my_run

  # Limit prompts for a quick smoke test:
  python run_forgebench.py --suite golden --adapter forgeai -n 5

Adapter interface
-----------------
An adapter is any Python class that implements:

    class MyAdapter:
        name: str = "my-system"

        def generate(self, idea: str) -> AdapterResult:
            ...

AdapterResult fields (all optional except compile_success):
    compile_success:    bool         — did the generated code pass static checks?
    runtime_success:    bool         — did the app start and respond to /health?
    crud_success:       bool         — did CRUD smoke tests pass?
    forge_score:        float        — 0–100 quality score (if available)
    generation_time_s:  float        — wall-clock time in seconds
    estimated_cost_usd: float        — LLM cost in USD (if tracked)
    fix_count:          int          — repair iterations applied
    notes:              str          — freeform info (error message, log excerpt)

Built-in adapters
-----------------
  forgeai  — calls the local ForgeAI /project endpoint (default: http://localhost:8000)
  http     — generic HTTP adapter; POST idea to --adapter-url, expects AdapterResult JSON
  null     — always returns compile_success=False (useful for baseline comparison)
  mock     — returns compile_success=True with random scores (useful for testing this script)
"""
from __future__ import annotations

import argparse
import json
import os
import random
import sys
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT / "backend"))

try:
    from app.benchmark.loader import load_prompts, TIER_LABELS, BenchmarkPrompt
except ImportError:
    print("ERROR: Could not import benchmark loader. Make sure you run from the repo root.")
    sys.exit(1)


# ---------------------------------------------------------------------------
# AdapterResult
# ---------------------------------------------------------------------------
@dataclass
class AdapterResult:
    compile_success:    bool  = False
    runtime_success:    bool  = False
    crud_success:       bool  = False
    forge_score:        float = 0.0
    generation_time_s:  float = 0.0
    estimated_cost_usd: float = 0.0
    fix_count:          int   = 0
    notes:              str   = ""

    @staticmethod
    def from_dict(d: dict) -> "AdapterResult":
        return AdapterResult(**{k: v for k, v in d.items() if k in AdapterResult.__dataclass_fields__})


# ---------------------------------------------------------------------------
# Built-in adapters
# ---------------------------------------------------------------------------
class ForgeAIAdapter:
    """Calls the local ForgeAI /project endpoint."""
    name = "forgeai"

    def __init__(self, base_url: str = "http://localhost:8000"):
        self.base_url = base_url.rstrip("/")

    def generate(self, idea: str) -> AdapterResult:
        import urllib.request
        import urllib.error

        payload = json.dumps({"idea": idea}).encode()
        req = urllib.request.Request(
            f"{self.base_url}/project",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        t0 = time.time()
        try:
            with urllib.request.urlopen(req, timeout=300) as resp:
                body = json.loads(resp.read())
        except urllib.error.HTTPError as e:
            body = json.loads(e.read())
        elapsed = time.time() - t0

        return AdapterResult(
            compile_success    = body.get("static_passed", body.get("compile_success", False)),
            runtime_success    = body.get("runtime_passed", body.get("runtime_success", False)),
            crud_success       = body.get("crud_success", False),
            forge_score        = float(body.get("forge_score", 0)),
            generation_time_s  = elapsed,
            estimated_cost_usd = float(body.get("estimated_cost_usd", 0)),
            fix_count          = int(body.get("fix_count", 0)),
            notes              = body.get("error", ""),
        )


class HttpAdapter:
    """Generic HTTP adapter: POST {idea} → AdapterResult JSON."""
    name = "http"

    def __init__(self, url: str):
        self.url = url

    def generate(self, idea: str) -> AdapterResult:
        import urllib.request

        payload = json.dumps({"idea": idea}).encode()
        req = urllib.request.Request(
            self.url,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        t0 = time.time()
        with urllib.request.urlopen(req, timeout=300) as resp:
            body = json.loads(resp.read())
        elapsed = time.time() - t0
        result = AdapterResult.from_dict(body)
        if result.generation_time_s == 0:
            result.generation_time_s = elapsed
        return result


class NullAdapter:
    """Always fails — baseline for zero-skill comparison."""
    name = "null"

    def generate(self, idea: str) -> AdapterResult:
        return AdapterResult(compile_success=False, runtime_success=False, notes="null adapter")


class MockAdapter:
    """Random pass/fail — useful for testing this runner without calling any LLM."""
    name = "mock"

    def generate(self, idea: str) -> AdapterResult:
        time.sleep(random.uniform(0.05, 0.15))
        ok = random.random() > 0.35
        return AdapterResult(
            compile_success    = ok,
            runtime_success    = ok and random.random() > 0.2,
            crud_success       = ok and random.random() > 0.3,
            forge_score        = round(random.uniform(55, 98) if ok else random.uniform(10, 55), 1),
            generation_time_s  = round(random.uniform(8, 45), 1),
            estimated_cost_usd = round(random.uniform(0.005, 0.08), 4),
            fix_count          = random.randint(0, 3),
        )


def make_adapter(name: str, adapter_url: str = "", base_url: str = "http://localhost:8000"):
    if name == "forgeai":
        return ForgeAIAdapter(base_url)
    if name == "http":
        if not adapter_url:
            print("ERROR: --adapter http requires --adapter-url")
            sys.exit(1)
        return HttpAdapter(adapter_url)
    if name == "null":
        return NullAdapter()
    if name == "mock":
        return MockAdapter()
    print(f"ERROR: Unknown adapter '{name}'. Choices: forgeai, http, null, mock")
    sys.exit(1)


# ---------------------------------------------------------------------------
# Run result
# ---------------------------------------------------------------------------
@dataclass
class PromptResult:
    name:           str
    difficulty:     str
    weight:         float
    idea:           str
    adapter_result: AdapterResult
    crashed:        bool = False
    crash_error:    str  = ""

    def passed(self) -> bool:
        return self.adapter_result.compile_success and self.adapter_result.runtime_success

    def score(self) -> float:
        return self.adapter_result.forge_score


# ---------------------------------------------------------------------------
# Scorecard
# ---------------------------------------------------------------------------
@dataclass
class ForgeBenchScorecard:
    run_id:         str
    suite:          str
    suite_label:    str
    adapter_name:   str
    adapter_url:    str
    started_at:     str
    completed_at:   str = ""

    total:          int   = 0
    completed:      int   = 0
    crashed:        int   = 0

    compile_rate:   float = 0.0
    runtime_rate:   float = 0.0
    crud_rate:      float = 0.0
    avg_score:      float = 0.0
    weighted_score: float = 0.0
    weighted_pass_rate: float = 0.0

    total_cost_usd:     float = 0.0
    cost_per_success:   float = 0.0
    avg_time_s:         float = 0.0

    results: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def compute_scorecard(
    results: list[PromptResult],
    run_id: str,
    suite: str,
    adapter_name: str,
    adapter_url: str,
    started_at: str,
) -> ForgeBenchScorecard:
    done   = [r for r in results if not r.crashed]
    n      = len(done)
    suite_label = TIER_LABELS.get(suite, suite.upper())

    def rate(flag: str) -> float:
        if not n: return 0.0
        return sum(1 for r in done if getattr(r.adapter_result, flag)) / n

    total_w = sum(r.weight for r in done) or 1.0
    w_score = sum(r.score() * r.weight for r in done) / total_w
    w_pass  = sum(r.weight for r in done if r.passed()) / total_w

    total_cost  = sum(r.adapter_result.estimated_cost_usd for r in results)
    successful  = sum(1 for r in done if r.passed())
    cost_per_ok = round(total_cost / successful, 4) if successful else 0.0

    return ForgeBenchScorecard(
        run_id          = run_id,
        suite           = suite,
        suite_label     = suite_label,
        adapter_name    = adapter_name,
        adapter_url     = adapter_url,
        started_at      = started_at,
        completed_at    = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        total           = len(results),
        completed       = n,
        crashed         = len(results) - n,
        compile_rate    = round(rate("compile_success"), 3),
        runtime_rate    = round(rate("runtime_success"), 3),
        crud_rate       = round(rate("crud_success"), 3),
        avg_score       = round(sum(r.score() for r in done) / n if n else 0, 1),
        weighted_score  = round(w_score, 1),
        weighted_pass_rate = round(w_pass, 3),
        total_cost_usd  = round(total_cost, 4),
        cost_per_success= cost_per_ok,
        avg_time_s      = round(sum(r.adapter_result.generation_time_s for r in done) / n if n else 0, 1),
        results         = [
            {
                "name":             r.name,
                "difficulty":       r.difficulty,
                "weight":           r.weight,
                "compile_success":  r.adapter_result.compile_success,
                "runtime_success":  r.adapter_result.runtime_success,
                "crud_success":     r.adapter_result.crud_success,
                "forge_score":      r.adapter_result.forge_score,
                "generation_time_s":r.adapter_result.generation_time_s,
                "cost_usd":         r.adapter_result.estimated_cost_usd,
                "fix_count":        r.adapter_result.fix_count,
                "notes":            r.adapter_result.notes,
                "crashed":          r.crashed,
                "crash_error":      r.crash_error,
            }
            for r in results
        ],
    )


# ---------------------------------------------------------------------------
# Reporter
# ---------------------------------------------------------------------------
def print_scorecard(sc: ForgeBenchScorecard) -> None:
    sep = "─" * 66
    print(f"\n{'═'*66}")
    print(f"  ForgeBench Scorecard — {sc.suite_label}")
    print(f"  Adapter  : {sc.adapter_name}  {sc.adapter_url or ''}".rstrip())
    print(f"  Run ID   : {sc.run_id}")
    print(f"  Suite    : {sc.suite}  ({sc.total} prompts)")
    print(f"{'═'*66}")

    print(f"\n  ★  Weighted Score    {sc.weighted_score:6.1f} / 100")
    print(f"     Weighted Pass Rate {sc.weighted_pass_rate*100:5.1f}%")
    print(f"\n  {sep}")
    print(f"     Compile Rate       {sc.compile_rate*100:5.1f}%")
    print(f"     Runtime Rate       {sc.runtime_rate*100:5.1f}%")
    print(f"     CRUD Rate          {sc.crud_rate*100:5.1f}%")
    print(f"     Avg Forge Score    {sc.avg_score:6.1f}")
    print(f"  {sep}")
    print(f"     Avg Generation     {sc.avg_time_s:5.0f}s")
    print(f"     Total Cost         ${sc.total_cost_usd:.4f}")
    print(f"     Cost / Success     ${sc.cost_per_success:.4f}")
    print(f"  {sep}")
    print(f"     Completed {sc.completed}/{sc.total}  Crashed {sc.crashed}")
    print()

    # Per-prompt table
    max_name = max((len(r["name"]) for r in sc.results), default=10)
    col = max(max_name, 10)
    header = f"  {'NAME':<{col}}  W   C R U  SCORE   TIME    COST"
    print(f"  {sep}")
    print(header)
    print(f"  {sep}")
    for r in sc.results:
        if r["crashed"]:
            print(f"  {r['name']:<{col}}  {r['weight']:.0f}  CRASH  {r['crash_error'][:30]}")
            continue
        c = "C" if r["compile_success"] else "x"
        rt= "R" if r["runtime_success"] else "x"
        u = "U" if r["crud_success"]    else "x"
        print(
            f"  {r['name']:<{col}}  {r['weight']:.0f}  {c} {rt} {u}  "
            f"{r['forge_score']:5.1f}  {r['generation_time_s']:5.0f}s  ${r['cost_usd']:.4f}"
        )
    print(f"  {sep}\n")


def save_scorecard(sc: ForgeBenchScorecard, out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    sc_path = out_dir / "scorecard.json"
    sc_path.write_text(json.dumps(sc.to_dict(), indent=2), encoding="utf-8")
    print(f"  Saved scorecard → {sc_path}")

    # Append to leaderboard history
    history_path = out_dir.parent / "forgebench_history.jsonl"
    entry = {
        "run_id":          sc.run_id,
        "suite":           sc.suite,
        "adapter":         sc.adapter_name,
        "date":            sc.completed_at,
        "weighted_score":  sc.weighted_score,
        "runtime_rate":    sc.runtime_rate,
        "cost_per_success":sc.cost_per_success,
        "avg_time_s":      sc.avg_time_s,
    }
    with open(history_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")
    print(f"  Appended to leaderboard → {history_path}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> None:
    p = argparse.ArgumentParser(
        description="ForgeBench public benchmark runner",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--suite",        default="golden",
                   choices=["golden", "forgebench_realtime", "forgebench_ai",
                             "forgebench_enterprise", "beginner", "intermediate",
                             "advanced", "enterprise", "hard"],
                   help="Benchmark suite to run (default: golden)")
    p.add_argument("--adapter",      default="forgeai",
                   choices=["forgeai", "http", "null", "mock"],
                   help="Generation backend adapter (default: forgeai)")
    p.add_argument("--adapter-url",  default="",
                   help="URL for http adapter or ForgeAI base URL override")
    p.add_argument("--base-url",     default="http://localhost:8000",
                   help="ForgeAI base URL (default: http://localhost:8000)")
    p.add_argument("-n",             type=int, default=None,
                   help="Limit number of prompts")
    p.add_argument("--offset",       type=int, default=0,
                   help="Skip first N prompts")
    p.add_argument("--out",          default="",
                   help="Output directory for scorecard.json (default: benchmark_results/forgebench/<run_id>)")
    p.add_argument("--dry-run",      action="store_true",
                   help="List prompts and exit without generating")
    p.add_argument("--run-id",       default="",
                   help="Custom run ID (default: auto-generated)")
    args = p.parse_args()

    prompts = load_prompts(difficulty=args.suite, n=args.n, offset=args.offset)
    if not prompts:
        print(f"No prompts found for suite '{args.suite}'.")
        sys.exit(1)

    suite_label = TIER_LABELS.get(args.suite, args.suite.upper())
    total_w = sum(p.weight for p in prompts)

    print(f"\n  ForgeBench — {suite_label}")
    print(f"  {len(prompts)} prompts  total weight {total_w:.0f}  adapter={args.adapter}")

    if args.dry_run:
        print()
        for pr in prompts:
            print(f"  w={pr.weight:.0f}  {pr.name:<35}  {pr.idea[:60]}...")
        print(f"\n  (dry-run) {len(prompts)} prompts listed. No generation performed.")
        return

    adapter = make_adapter(
        args.adapter,
        adapter_url=args.adapter_url,
        base_url=args.adapter_url or args.base_url,
    )

    run_id     = args.run_id or f"fb-{args.suite[:6]}-{uuid.uuid4().hex[:8]}"
    started_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    results: list[PromptResult] = []

    print(f"  Run ID: {run_id}\n")

    for i, prompt in enumerate(prompts, 1):
        prefix = f"  [{i:2d}/{len(prompts)}] w={prompt.weight:.0f}  {prompt.name:<35}"
        print(prefix, end="", flush=True)
        t0 = time.time()
        try:
            ar = adapter.generate(prompt.idea)
            elapsed = time.time() - t0
            if ar.generation_time_s == 0:
                ar.generation_time_s = elapsed

            status = ("✓" if ar.compile_success and ar.runtime_success
                      else "C" if ar.compile_success else "✗")
            print(f"  {status}  score={ar.forge_score:4.1f}  {elapsed:4.0f}s  ${ar.estimated_cost_usd:.4f}")
            results.append(PromptResult(
                name=prompt.name, difficulty=prompt.difficulty, weight=prompt.weight,
                idea=prompt.idea, adapter_result=ar,
            ))
        except Exception as e:
            elapsed = time.time() - t0
            print(f"  CRASH  {elapsed:.0f}s  {e}")
            results.append(PromptResult(
                name=prompt.name, difficulty=prompt.difficulty, weight=prompt.weight,
                idea=prompt.idea, adapter_result=AdapterResult(),
                crashed=True, crash_error=str(e),
            ))

    sc = compute_scorecard(results, run_id, args.suite, adapter.name,
                           args.adapter_url or args.base_url, started_at)
    print_scorecard(sc)

    out_dir = (Path(args.out) if args.out
               else ROOT / "benchmark_results" / "forgebench" / run_id)
    save_scorecard(sc, out_dir)


if __name__ == "__main__":
    main()
