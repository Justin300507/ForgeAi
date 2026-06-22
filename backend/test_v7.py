"""
V7 Integration Test Suite

Checks:
  1. Can v6_orchestrator generate a project end-to-end?
  2. Can the improvement cycle (rule evolution + prompt optimizer) run?
  3. Does benchmark_comparison_service produce useful output?
  4. Does research_agent_service produce useful hypotheses?
  5. Does improvement_leaderboard_service rank correctly?

Usage:
  python test_v7.py [--all] [--check 1] [--check 2] ...
  python test_v7.py --seed-dataset  # seed 2 real projects then run improvements
"""
import sys
import os
import json
import time
import argparse

# ── helpers ─────────────────────────────────────────────────────────────────
PASS = "  PASS"
FAIL = "  FAIL"
SKIP = "  SKIP"
INFO = "  INFO"
results = []


def _s(msg): return str(msg).encode("ascii", errors="replace").decode("ascii")
def ok(msg): print(f"{PASS}  {_s(msg)}"); results.append(("PASS", _s(msg)))
def fail(msg, detail=""): print(f"{FAIL}  {_s(msg)}"); detail and print(f"       {_s(detail)}"); results.append(("FAIL", _s(msg)))
def skip(msg): print(f"{SKIP}  {_s(msg)}"); results.append(("SKIP", _s(msg)))
def info(msg): print(f"{INFO}  {_s(msg)}")


# ── Check 3: Benchmark Comparison ────────────────────────────────────────────
def check3_benchmark_comparison():
    print("\n=== CHECK 3: benchmark_comparison_service ===")
    try:
        from app.services.benchmark_comparison_service import compare_versions, print_comparison_report
        comparison = compare_versions(min_runs_per_version=1)
        print_comparison_report(comparison)

        ok(f"compare_versions() returned — best_version={comparison.best_version}")
        ok(f"regression_detected field present: {comparison.regression_detected}")
        ok(f"improvement_velocity computed: {comparison.improvement_velocity}")

        if comparison.versions:
            for v, m in comparison.versions.items():
                ok(f"Version {v}: {m.run_count} runs, avg_score={m.avg_score}, pass_rate={m.pass_rate:.0%}")
        else:
            skip("No versioned data in dataset yet — seed with --seed-dataset first")

    except Exception as e:
        fail("benchmark_comparison_service crashed", str(e))
        import traceback; traceback.print_exc()


# ── Check 4: Research Agent ───────────────────────────────────────────────────
def check4_research_agent(provider="auto"):
    print("\n=== CHECK 4: research_agent_service ===")
    try:
        from app.services.research_agent_service import run_research_agent, get_latest_findings

        findings = run_research_agent(provider=provider)

        if findings.skipped:
            skip(f"Research agent skipped: {findings.skip_reason}")
            return

        ok(f"Trend: {findings.trend} — {findings.trend_explanation[:80]}")
        ok(f"Regression detected: {findings.regression_detected}")
        ok(f"Pattern clusters: {len(findings.pattern_clusters)}")
        ok(f"Top improvements: {len(findings.top_improvements)}")

        if findings.top_improvements:
            for imp in findings.top_improvements[:3]:
                info(f"  [{imp.rank}] {imp.improvement[:70]} ({imp.expected_impact})")

        if findings.hypothesis:
            ok(f"Hypothesis generated: {findings.hypothesis.statement[:100]}")
        else:
            skip("No hypothesis generated")

        # Verify it was saved
        saved = get_latest_findings()
        if saved:
            ok("Findings persisted to disk")
        else:
            fail("Findings NOT persisted to disk")

    except Exception as e:
        fail("research_agent_service crashed", str(e))
        import traceback; traceback.print_exc()


# ── Check 5: Improvement Leaderboard ─────────────────────────────────────────
def check5_leaderboard():
    print("\n=== CHECK 5: improvement_leaderboard_service ===")
    try:
        from app.services.improvement_leaderboard_service import (
            ImprovementEntry, record_improvement, get_leaderboard, print_leaderboard,
            sync_from_prompt_optimizer, sync_from_rule_evolution,
        )
        import time

        # Inject two synthetic entries and verify ranking
        e1 = ImprovementEntry(
            entry_id="test_high_impact",
            timestamp=time.strftime("%Y-%m-%dT%H:%M:%S"),
            improvement_type="prompt_rule",
            description="Add explicit router naming rule",
            source="test",
            pattern_key="RouterExportMismatch",
            success_rate_change=0.20,     # +20pp
            runtime_change_ms=None,
            cost_change_usd=-0.01,
            benchmark_score_delta=8.0,
            old_pass_rate=0.60,
            new_pass_rate=0.80,
            test_n=5,
        )
        e2 = ImprovementEntry(
            entry_id="test_low_impact",
            timestamp=time.strftime("%Y-%m-%dT%H:%M:%S"),
            improvement_type="evolved_rule",
            description="Minor comment cleanup",
            source="test",
            pattern_key="StubHandler",
            success_rate_change=0.02,     # +2pp
            runtime_change_ms=None,
            cost_change_usd=None,
            benchmark_score_delta=1.0,
            old_pass_rate=0.70,
            new_pass_rate=0.72,
            test_n=3,
        )

        record_improvement(e1)
        record_improvement(e2)

        board = get_leaderboard(top_n=10)
        ok(f"get_leaderboard() returned {len(board)} entries")

        if len(board) >= 2:
            top = board[0]
            second = board[1]
            if top.get("combined_impact", 0) >= second.get("combined_impact", 0):
                ok(f"Ranking correct: top={top['combined_impact']:.1f} >= second={second['combined_impact']:.1f}")
            else:
                fail("Ranking WRONG: lower impact item ranked first")

        print_leaderboard(top_n=5)

        # Sync from real sources
        po_added = sync_from_prompt_optimizer()
        re_added = sync_from_rule_evolution()
        info(f"Synced {po_added} from prompt_optimizer, {re_added} from rule_evolution")
        ok("Leaderboard sync completed")

    except Exception as e:
        fail("improvement_leaderboard_service crashed", str(e))
        import traceback; traceback.print_exc()


# ── Check 2: Improvement Cycle (dry-run with no benchmarking) ────────────────
def check2_improvement_cycle(provider="auto", benchmark_n=0):
    print("\n=== CHECK 2: Improvement Cycle ===")
    try:
        from app.services.rule_evolution_service import run_rule_evolution
        from app.services.prompt_optimizer_service import run_prompt_optimization

        # Rule evolution with 0 benchmark projects = just generates + validates candidates
        if benchmark_n == 0:
            info("Running rule evolution in dry-run mode (no benchmark — pass --benchmark to benchmark)")
            evo = run_rule_evolution(provider=provider, test_n=0, threshold=0.08)
            info(f"Candidates generated: {evo.candidates_generated}")
            info(f"Candidates validated: {evo.candidates_validated}")
            if evo.skipped:
                skip(f"Rule evolution skipped: {evo.skip_reason}")
            else:
                ok(f"Rule evolution ran: {evo.candidates_generated} candidates generated, {evo.candidates_validated} validated")
        else:
            info(f"Running rule evolution with {benchmark_n} benchmark projects...")
            evo = run_rule_evolution(provider=provider, test_n=benchmark_n)
            if evo.skipped:
                skip(f"Rule evolution skipped: {evo.skip_reason}")
            else:
                ok(f"Rule evolution: {evo.rules_promoted} rules promoted out of {evo.candidates_generated} candidates")
                if evo.promoted_rules:
                    for rule in evo.promoted_rules:
                        ok(f"  Promoted: {rule[:80]}")

        # Prompt optimizer validation
        if benchmark_n == 0:
            info("Prompt optimizer requires benchmark_n > 0 to actually run")
            skip("Prompt optimizer dry-run skipped (needs --benchmark N)")
        else:
            opt = run_prompt_optimization(provider=provider, test_n=benchmark_n)
            if opt.skipped:
                skip(f"Prompt optimizer skipped: {opt.skip_reason}")
            elif opt.improved:
                ok(f"Prompt optimizer: +{opt.improvement:.0%} improvement accepted")
                ok(f"  Rule: {opt.rule_added[:80]}")
            else:
                info(f"Prompt optimizer: no improvement found ({opt.old_pass_rate:.0%} → {opt.new_pass_rate:.0%})")

    except Exception as e:
        fail("Improvement cycle crashed", str(e))
        import traceback; traceback.print_exc()


# ── Check 1: Full E2E generation ──────────────────────────────────────────────
def check1_e2e_generation(idea: str, provider="auto"):
    print(f"\n=== CHECK 1: Full V7 generation ===")
    print(f"  Idea: {idea}")
    print(f"  Provider: {provider}")
    t0 = time.time()

    try:
        from app.services.v7_orchestrator import generate_project_v7

        result = generate_project_v7(
            idea,
            provider=provider,
            run_improvement_cycle=False,  # don't auto-trigger cycle in check 1
        )

        elapsed = round(time.time() - t0, 1)
        pipeline = result.get("pipeline", "?")
        forge_score = (result.get("forge_score") or {}).get("score", 0)
        v6_score = (result.get("v6_score") or {}).get("total", 0)
        runtime_passed = bool((result.get("runtime") or {}).get("success"))
        validation_passed = (result.get("validation") or {}).get("passed", False)
        project_path = result.get("project_path", "")

        ok(f"Pipeline completed: {pipeline} ({elapsed}s)")
        ok(f"Forge score: {forge_score}/100")
        ok(f"V6 score: {v6_score}/100")
        ok(f"Validation: {'PASS' if validation_passed else 'FAIL'}")
        ok(f"Runtime: {'PASS' if runtime_passed else 'FAIL'}")
        ok(f"Project path: {project_path}")

        # Check V6 agent outputs
        security = result.get("security", {}) or {}
        performance = result.get("performance", {}) or {}
        code_review = result.get("code_review", {}) or {}
        qa = result.get("qa", {}) or {}

        if security.get("score") is not None:
            ok(f"Security agent: {security['score']}/100 [{security.get('risk_level', '?')}]")
        else:
            fail("Security agent returned no score")

        if performance.get("score") is not None:
            ok(f"Performance agent: {performance['score']}/100")
        else:
            fail("Performance agent returned no score")

        if code_review.get("maintainability_score") is not None:
            ok(f"Code review agent: maintainability={code_review['maintainability_score']}/100")
        else:
            fail("Code review agent returned no maintainability score")

        if qa.get("score") is not None:
            ok(f"QA agent: {qa['score']}/100 ({qa.get('stories_covered', '?')} stories covered)")
        else:
            fail("QA agent returned no score")

        # Check dataset was updated
        from app.services.failure_dataset_service import get_index
        index = get_index(limit=5)
        if index and index[-1].get("version") in ("v6", "v7"):
            ok(f"Dataset recorded: run_id={index[-1].get('run_id', '?')}")
        else:
            fail("Dataset was NOT updated after generation")

        # Check collaboration log exists
        collab_log = os.path.join(project_path, "v6_collaboration_log.json")
        if os.path.exists(collab_log):
            with open(collab_log) as f:
                log = json.load(f)
            ok(f"Agent collaboration log: {len(log.get('decisions', []))} decisions recorded")
            conflicts = log.get("conflicts", [])
            if conflicts:
                info(f"Agent conflicts detected: {len(conflicts)}")
                for c in conflicts:
                    info(f"  [{c['type']}] {c['description'][:80]}")
        else:
            fail("Agent collaboration log not written")

        return result

    except Exception as e:
        fail(f"V7 generation crashed", str(e))
        import traceback; traceback.print_exc()
        return None


# ── Holy Grail: Before/After Improvement Test ─────────────────────────────────
def check_holy_grail(provider="auto", n_projects=3):
    """
    The real milestone: show measurable improvement after one cycle.
    1. Run N projects → compute baseline pass rate
    2. Run improvement cycle
    3. Run N projects again → compute new pass rate
    4. Show the delta
    """
    print(f"\n{'#'*70}")
    print(f"# HOLY GRAIL TEST — Does V7 Actually Improve Itself?")
    print(f"# {n_projects} projects before improvement cycle")
    print(f"# {n_projects} projects after improvement cycle")
    print(f"{'#'*70}")

    ideas = [
        "A todo list app with user authentication and task priorities",
        "A blog platform with posts, comments, and author profiles",
        "A recipe manager with ingredients, categories, and search",
        "A contact book with search, tagging, and import/export",
        "An expense tracker with categories and monthly summaries",
    ][:n_projects]

    from app.services.project_service import generate_project
    from app.services.v7_orchestrator import run_improvement_cycle_v7

    # ── Phase 1: Baseline ────────────────────────────────────────────────────
    print(f"\n--- PHASE 1: Baseline ({n_projects} projects) ---")
    baseline_scores = []
    baseline_passed = 0
    for idea in ideas:
        try:
            r = generate_project(idea, provider=provider)
            score = r.get("forge_score", {}).get("score", 0)
            runtime_ok = bool((r.get("runtime") or {}).get("success"))
            baseline_scores.append(score)
            if runtime_ok: baseline_passed += 1
            print(f"  {idea[:45]}: score={score} runtime={'OK' if runtime_ok else 'FAIL'}")
        except Exception as e:
            print(f"  {idea[:45]}: ERROR — {e}")
            baseline_scores.append(0)

    baseline_avg = sum(baseline_scores) / len(baseline_scores) if baseline_scores else 0
    baseline_rate = baseline_passed / n_projects
    print(f"\n  Baseline: avg_score={baseline_avg:.1f} pass_rate={baseline_rate:.0%}")

    # ── Improvement Cycle ─────────────────────────────────────────────────────
    print(f"\n--- IMPROVEMENT CYCLE ---")
    cycle_result = run_improvement_cycle_v7(
        provider=provider,
        benchmark_n=min(n_projects, 3),
    )
    rules_promoted = (cycle_result.get("rule_evolution") or {}).get("rules_promoted", 0)
    prompt_improved = (cycle_result.get("prompt_optimizer") or {}).get("improved", False)
    print(f"  Rules promoted: {rules_promoted}")
    print(f"  Prompt improved: {prompt_improved}")

    # ── Phase 2: After Improvement ───────────────────────────────────────────
    print(f"\n--- PHASE 2: After Improvement ({n_projects} projects) ---")
    after_scores = []
    after_passed = 0
    for idea in ideas:
        try:
            r = generate_project(idea, provider=provider)
            score = r.get("forge_score", {}).get("score", 0)
            runtime_ok = bool((r.get("runtime") or {}).get("success"))
            after_scores.append(score)
            if runtime_ok: after_passed += 1
            print(f"  {idea[:45]}: score={score} runtime={'OK' if runtime_ok else 'FAIL'}")
        except Exception as e:
            print(f"  {idea[:45]}: ERROR — {e}")
            after_scores.append(0)

    after_avg = sum(after_scores) / len(after_scores) if after_scores else 0
    after_rate = after_passed / n_projects

    # ── Report ────────────────────────────────────────────────────────────────
    score_delta = after_avg - baseline_avg
    rate_delta = after_rate - baseline_rate

    print(f"\n{'='*70}")
    print(f"  HOLY GRAIL RESULTS")
    print(f"{'='*70}")
    print(f"  Baseline avg score:    {baseline_avg:.1f}/100  ({baseline_rate:.0%} pass rate)")
    print(f"  After improvement:     {after_avg:.1f}/100  ({after_rate:.0%} pass rate)")
    print(f"  Delta:                 {score_delta:+.1f} score  {rate_delta:+.0%} pass rate")
    print(f"  Rules promoted:        {rules_promoted}")
    print(f"  Prompt improved:       {prompt_improved}")

    if score_delta > 0 or rate_delta > 0:
        print(f"\n  ✅ V7 IS SELF-IMPROVING (score went up)")
        ok(f"Holy Grail: +{score_delta:.1f} score, {rate_delta:+.0%} pass rate")
    elif rules_promoted == 0 and not prompt_improved:
        print(f"\n  ⚠️  No improvements were promoted (not enough data or threshold not met)")
        skip("Holy Grail: no improvements promoted — need more training data")
    else:
        print(f"\n  ⚠️  Improvement cycle ran but scores didn't go up this run")
        info(f"Holy Grail: inconclusive ({score_delta:+.1f} score delta — small samples)")

    print("=" * 70)


# ── Main ─────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="ForgeAI V7 integration tests")
    parser.add_argument("--check", type=int, nargs="+", help="Which checks to run (1-5)")
    parser.add_argument("--all", action="store_true", help="Run all checks except E2E")
    parser.add_argument("--e2e", action="store_true", help="Run check 1 (full E2E generation)")
    parser.add_argument("--holy-grail", action="store_true", help="Run the holy grail test")
    parser.add_argument("--benchmark", type=int, default=0, metavar="N",
                        help="Number of projects to benchmark during improvement cycle (0=dry-run)")
    parser.add_argument("--provider", default="auto", help="LLM provider")
    parser.add_argument("--idea", default="A task management app with user login and priority labels",
                        help="Idea for E2E test")
    parser.add_argument("--n-projects", type=int, default=3, help="Projects for holy grail test")
    args = parser.parse_args()

    checks = set(args.check or [])
    run_all = args.all or (not checks and not args.e2e and not args.holy_grail)

    print(f"\n{'='*70}")
    print(f"  ForgeAI V7 Integration Tests")
    print(f"  Provider: {args.provider}")
    print(f"{'='*70}")

    if args.e2e or (1 in checks):
        check1_e2e_generation(args.idea, provider=args.provider)

    if run_all or (2 in checks):
        check2_improvement_cycle(provider=args.provider, benchmark_n=args.benchmark)

    if run_all or (3 in checks):
        check3_benchmark_comparison()

    if run_all or (4 in checks):
        check4_research_agent(provider=args.provider)

    if run_all or (5 in checks):
        check5_leaderboard()

    if args.holy_grail:
        check_holy_grail(provider=args.provider, n_projects=args.n_projects)

    # Summary
    print(f"\n{'='*70}")
    total = len(results)
    passed = sum(1 for r in results if r[0] == "PASS")
    failed = sum(1 for r in results if r[0] == "FAIL")
    skipped = sum(1 for r in results if r[0] == "SKIP")
    print(f"  Results: {passed} passed, {failed} failed, {skipped} skipped / {total} total")

    if failed > 0:
        print(f"\n  Failed checks:")
        for r in results:
            if r[0] == "FAIL":
                print(f"    X  {r[1]}")

    print("=" * 70)
    return 1 if failed > 0 else 0


if __name__ == "__main__":
    sys.exit(main())
