"""
Benchmark reporter — prints live progress and generates the final report.

Output formats:
  1. Console: live table during the run + final ForgeBench-20 dashboard
  2. JSONL: one result per line (written after each app, crash-safe)
  3. JSON: full report after all apps complete
  4. Markdown: human-readable report card
  5. history.json: version trend appended after each run
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from app.benchmark.metrics import BenchmarkResult, BenchmarkReport, compute_report


def _bar(rate: float, width: int = 20) -> str:
    filled = int(rate * width)
    return "#" * filled + "." * (width - filled)


def _pct(rate: float) -> str:
    return f"{rate * 100:.1f}%"


class BenchmarkReporter:
    """
    Accumulates BenchmarkResult objects as the run progresses.
    Writes incrementally to JSONL. Prints live progress.
    """

    def __init__(self, output_dir: Path, run_id: str, version: str, provider: str, label: str):
        self.output_dir = output_dir
        self.run_id     = run_id
        self.version    = version
        self.provider   = provider
        self.label      = label
        self.started_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        self.results:   list[BenchmarkResult] = []
        output_dir.mkdir(parents=True, exist_ok=True)
        self._jsonl_path  = output_dir / "results.jsonl"
        self._report_path = output_dir / "report.json"
        self._md_path     = output_dir / "report.md"

    def record(self, result: BenchmarkResult):
        """Call after each app completes. Appends to JSONL immediately (crash-safe)."""
        result.run_id = self.run_id
        self.results.append(result)
        with self._jsonl_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(result.to_dict()) + "\n")
        self._print_live_row(result)

    def _print_live_row(self, r: BenchmarkResult):
        print(r.one_line())

    def finalize(self) -> BenchmarkReport:
        """Generate and save the final report, then update history."""
        report = compute_report(
            self.results,
            run_id     = self.run_id,
            version    = self.version,
            provider   = self.provider,
            label      = self.label,
            started_at = self.started_at,
        )
        self._report_path.write_text(
            json.dumps(report.__dict__, indent=2, default=str),
            encoding="utf-8",
        )
        self._md_path.write_text(self._build_markdown(report), encoding="utf-8")
        self._print_dashboard(report)

        # Append to version history and print trend
        try:
            from app.benchmark.history import record_run, print_trend
            record_run(report)
            print_trend()
        except Exception:
            pass

        return report

    # ── Dashboard ─────────────────────────────────────────────────────────────

    def _print_dashboard(self, r: BenchmarkReport):
        is_golden = any(res.get("difficulty") == "golden" for res in r.results)
        suite_name = "ForgeBench-20" if is_golden else f"ForgeAI {r.version} Benchmark"

        w = 60
        print(f"\n{'═' * w}")
        print(f"  {suite_name}  —  {r.version}  ({r.label or r.run_id[:16]})")
        print(f"{'═' * w}")
        print(f"  Apps: {r.completed}/{r.total_prompts}  Crashed: {r.crashed}")

        # Headline: weighted score if we have weights, else plain avg
        if r.weighted_score != r.avg_forge_score and r.weighted_score > 0:
            print(f"\n  ★ Weighted Score   {r.weighted_score:>6.1f} / 100  "
                  f"(weighted pass: {_pct(r.weighted_pass_rate)})")
            print(f"    Avg Forge Score  {r.avg_forge_score:>6.1f} / 100")
        else:
            print(f"\n  ★ Avg Forge Score  {r.avg_forge_score:>6.1f} / 100")

        print(f"")
        metrics = [
            ("Compile  Success ", r.compile_success_rate),
            ("Runtime  Success ", r.runtime_success_rate),
            ("CRUD     Tests   ", r.crud_success_rate),
            ("Browser  Success ", r.browser_success_rate),
            ("Deploy   Success ", r.deployment_success_rate),
            ("Endpoint Pass Rt ", r.avg_endpoint_pass_rate),
        ]
        for lbl, rate in metrics:
            bar = _bar(rate, 24)
            print(f"  {lbl:<18} {_pct(rate):>6}  [{bar}]")

        print(f"")
        print(f"  Avg Time       {r.avg_generation_s:>6.0f}s")
        print(f"  Total Cost     ${r.total_cost_usd:.3f}")
        print(f"  Cost / Success ${r.cost_per_success:.4f}  ← $/successful app")
        print(f"  Avg Fixes      {r.avg_fix_count:>6.1f}")

        if r.by_difficulty:
            print(f"")
            print(f"  {'Tier':<14} {'n':>3} {'Compile':>8} {'Runtime':>8} {'Score':>6}")
            print(f"  {'-' * 44}")
            for d in r.by_difficulty:
                print(f"  {d['difficulty']:<14} {d['total']:>3} "
                      f"{_pct(d['compile_rate']):>8} "
                      f"{_pct(d['runtime_rate']):>8} "
                      f"{d['avg_score']:>6.1f}")

        print(f"{'═' * w}")
        print(f"  Report: {self._report_path}")

    # ── Markdown ──────────────────────────────────────────────────────────────

    def _build_markdown(self, r: BenchmarkReport) -> str:
        is_golden = any(res.get("difficulty") == "golden" for res in r.results)
        suite = "ForgeBench-20" if is_golden else f"ForgeAI {r.version} Benchmark"

        lines = [
            f"# {suite} — {r.version}",
            f"",
            f"**Run:** `{r.run_id}`  ",
            f"**Label:** {r.label or '(none)'}  ",
            f"**Provider:** {r.provider}  ",
            f"**Completed:** {r.completed_at}",
            f"",
            f"## Headline",
            f"",
        ]
        if r.weighted_score != r.avg_forge_score and r.weighted_score > 0:
            lines += [
                f"| Metric | Value |",
                f"|--------|-------|",
                f"| **Weighted Score** (ForgeBench-20) | **{r.weighted_score:.1f} / 100** |",
                f"| Weighted Pass Rate | {_pct(r.weighted_pass_rate)} |",
                f"| Avg Forge Score | {r.avg_forge_score:.1f} / 100 |",
                f"| Successful Apps | {r.completed - r.crashed} / {r.total_prompts} |",
                f"| Total Cost | ${r.total_cost_usd:.3f} |",
                f"| Cost / Successful App | ${r.cost_per_success:.4f} |",
            ]
        else:
            lines += [
                f"| Metric | Value |",
                f"|--------|-------|",
                f"| Avg Forge Score | {r.avg_forge_score:.1f} / 100 |",
                f"| Apps | {r.completed}/{r.total_prompts} |",
            ]

        lines += [
            f"",
            f"## Success Rates",
            f"",
            f"| Stage | Rate | Bar |",
            f"|-------|------|-----|",
            f"| Compile  | {_pct(r.compile_success_rate)}  | `{_bar(r.compile_success_rate)}` |",
            f"| Runtime  | {_pct(r.runtime_success_rate)}  | `{_bar(r.runtime_success_rate)}` |",
            f"| CRUD     | {_pct(r.crud_success_rate)}     | `{_bar(r.crud_success_rate)}` |",
            f"| Browser  | {_pct(r.browser_success_rate)}  | `{_bar(r.browser_success_rate)}` |",
            f"| Deploy   | {_pct(r.deployment_success_rate)}| `{_bar(r.deployment_success_rate)}` |",
            f"| Endpoint | {_pct(r.avg_endpoint_pass_rate)} | `{_bar(r.avg_endpoint_pass_rate)}` |",
            f"",
            f"## Per-App Results",
            f"",
            f"| # | App | W | Score | CRUD | Score | Time | Cost |",
            f"|---|-----|---|-------|------|-------|------|------|",
        ]
        for i, res in enumerate(r.results, 1):
            icons = (
                ("C" if res.get("compile_success") else "x") +
                ("R" if res.get("runtime_success") else "x") +
                ("U" if res.get("crud_success") else "x") +
                ("B" if res.get("browser_success") else "x")
            )
            w = res.get("weight", 1.0)
            lines.append(
                f"| {i} | {res.get('name','?'):<22} | {w:.0f} | `{icons}` "
                f"| {res.get('forge_score',0):.0f} "
                f"| {res.get('generation_time_s',0):.0f}s "
                f"| ${res.get('estimated_cost_usd',0):.3f} |"
            )

        if r.by_difficulty:
            lines += [
                f"",
                f"## By Tier",
                f"",
                f"| Tier | n | Compile | Runtime | Score | Cost |",
                f"|------|---|---------|---------|-------|------|",
            ]
            for d in r.by_difficulty:
                lines.append(
                    f"| {d['difficulty']:<12} | {d['total']} "
                    f"| {_pct(d['compile_rate'])} "
                    f"| {_pct(d['runtime_rate'])} "
                    f"| {d['avg_score']} "
                    f"| ${d['avg_cost_usd']:.4f} |"
                )

        lines += [
            f"",
            f"---",
            f"*Generated by ForgeAI {r.version} — ForgeBench-20*",
        ]
        return "\n".join(lines)


def load_completed_ids(jsonl_path: Path) -> set[str]:
    """Return prompt files already completed in a previous run (for --resume)."""
    if not jsonl_path.exists():
        return set()
    completed = set()
    for line in jsonl_path.read_text(encoding="utf-8").splitlines():
        try:
            d = json.loads(line)
            completed.add(d.get("prompt_file", ""))
        except Exception:
            pass
    return completed
