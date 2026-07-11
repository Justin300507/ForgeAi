"""
ForgeAI Observatory (V20.2) — the engineering cockpit.

Zero-cost: reads existing telemetry (generation_log.jsonl,
canary_history.json) via app/memory/reliability_metrics.py's
compute_observatory(), renders one self-contained HTML page. No new
subsystem — every number here already exists somewhere in the reliability
dashboard or generation_log.jsonl; this just puts them in one view meant
to be opened, not read top-to-bottom.

Usage:
    python scripts/observatory.py [--out path/to/observatory.html]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_BACKEND_ROOT = Path(__file__).resolve().parent.parent
FAILURE_DIR = _BACKEND_ROOT / "failure_memory"
sys.path.insert(0, str(_BACKEND_ROOT))


def _load_jsonl(path: Path) -> list[dict]:
    rows = []
    if not path.exists():
        return rows
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except Exception:
                pass
    return rows


_CATEGORY_ORDER = [
    "Import validation", "Symbol validation", "Schema validator",
    "Entity validator", "Syntax validator", "Pydantic patcher",
    "Auth patcher", "Frontend patcher", "Other",
]


def _stage_color(rate: float | None) -> str:
    if rate is None:
        return "var(--text-faint)"
    if rate >= 70:
        return "var(--good)"
    if rate >= 40:
        return "var(--warn)"
    return "var(--bad)"


def _canary_dot_class(health: str) -> str:
    return {"Healthy": "good", "Degraded": "warn", "Unhealthy": "bad"}.get(health, "unknown")


def _render_prevention_section(obs: dict) -> str:
    by_cat = obs["prevention_by_category"]
    total = obs["prevention_total"]
    if not by_cat:
        return f'''\
  <div class="section-head">
    <h2 class="heading">Deterministic Prevention</h2>
    <span class="total mono">0 caught before runtime, last {obs["window"]} runs</span>
  </div>
  <div class="prevention-empty">
    No <code>prevention_counts</code> recorded yet in this window &mdash; the
    field shipped in Experiment 043 and back-fills nothing for generations
    that ran before it existed. This section activates on the next real
    generation; expect Import Validation and Schema Validator to lead,
    based on the corpus sweeps behind Experiments 040 and 042.
  </div>'''

    ordered = sorted(by_cat.items(), key=lambda kv: (
        _CATEGORY_ORDER.index(kv[0]) if kv[0] in _CATEGORY_ORDER else len(_CATEGORY_ORDER), -kv[1]))
    max_count = max(by_cat.values())
    rows = []
    for label, count in ordered:
        width = round(100 * count / max_count) if max_count else 0
        rows.append(f'''\
    <div class="prevention-row">
      <div class="prevention-label">{label}</div>
      <div class="prevention-track"><div class="prevention-fill" style="width: {width}%;"></div></div>
      <div class="prevention-count mono">{count}</div>
    </div>''')
    return f'''\
  <div class="section-head">
    <h2 class="heading">Deterministic Prevention</h2>
    <span class="total mono">{total} caught before runtime, last {obs["window"]} runs</span>
  </div>
  <div class="prevention-list">
{chr(10).join(rows)}
  </div>'''


def _render_failure_shift(obs: dict) -> str:
    now, historically = obs["top_failure_now"], obs["top_failure_historically"]
    if now is None:
        return '<div class="failure-shift"><span class="same">No failures recorded</span></div>'
    if historically is None or now == historically:
        return f'<div class="failure-shift"><span class="same">{now}</span></div>'
    return f'''<div class="failure-shift">
      <span class="was">{historically}</span>
      <span class="now">
        <svg viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="19 12 12 19 5 12"/><line x1="12" y1="19" x2="12" y2="5"/></svg>
        {now}
      </span>
    </div>'''


_CONFIDENCE_COLOR = {"High": "var(--good)", "Medium": "var(--warn)", "Low": "var(--text-faint)"}


def _render_timeline_section(timeline: list[dict]) -> str:
    if not timeline:
        return '''  <div class="section-head"><h2 class="heading">Reliability Timeline</h2></div>
  <div class="prevention-empty">No labeled canary runs recorded yet.</div>'''
    max_score = max(p["avg_score"] for p in timeline) or 1
    rows = []
    for i, p in enumerate(timeline):
        width = round(100 * p["avg_score"] / max_score)
        connector = '<div class="timeline-connector"></div>' if i > 0 else ""
        rows.append(f'''{connector}    <div class="timeline-row">
      <div class="timeline-label">
        <span class="timeline-name">{p["label"]}</span>
        <span class="timeline-date mono">{p["timestamp"]}</span>
      </div>
      <div class="timeline-bar-track"><div class="timeline-bar-fill" style="width: {width}%;"></div></div>
      <div class="timeline-score mono">{p["avg_score"]}</div>
    </div>''')
    return f'''  <div class="section-head">
    <h2 class="heading">Reliability Timeline</h2>
    <span class="total mono">{len(timeline)} canary milestones, avg forge score</span>
  </div>
  <div class="timeline-list">
{chr(10).join(rows)}
  </div>'''


def _render_attribution_section(attribution: list[dict]) -> str:
    if not attribution:
        return '''  <div class="section-head"><h2 class="heading">Experiment Attribution</h2></div>
  <div class="prevention-empty">Need at least two labeled canary runs to compute a before/after.</div>'''
    cards = []
    for a in attribution:
        sign = "+" if a["delta"] >= 0 else ""
        delta_color = "var(--good)" if a["direction"] == "improved" else (
            "var(--bad)" if a["direction"] == "regressed" else "var(--text-faint)")
        conf_color = _CONFIDENCE_COLOR[a["confidence"]]
        cards.append(f'''    <div class="attribution-card">
      <div class="attribution-head">
        <span class="attribution-label">{a["label"]}</span>
        <span class="attribution-date mono">{a["timestamp"]}</span>
      </div>
      <div class="attribution-grid">
        <div><div class="eyebrow">Before</div><div class="mono attribution-num">{a["before"]}</div></div>
        <div><div class="eyebrow">After</div><div class="mono attribution-num">{a["after"]}</div></div>
        <div><div class="eyebrow">Change</div><div class="mono attribution-num" style="color: {delta_color};">{sign}{a["delta"]}</div></div>
        <div><div class="eyebrow">Confidence</div><div class="attribution-num" style="color: {conf_color};">{a["confidence"]}</div></div>
        <div><div class="eyebrow">Evidence</div><div class="mono attribution-num">{a["evidence_n"]} apps</div></div>
      </div>
    </div>''')
    return f'''  <div class="section-head">
    <h2 class="heading">Experiment Attribution</h2>
    <span class="total mono">last {len(attribution)} labeled canary transitions</span>
  </div>
  <div class="attribution-list">
{chr(10).join(cards)}
  </div>
  <p class="attribution-note">
    Confidence reflects evidence size (canary app count), not delta
    magnitude &mdash; a big swing can mean a real fix or a provider-quota
    confound just as easily; this project's own experiment log documents
    both. A 3-app canary is inherently Low confidence on its own; treat
    single entries as directional, not conclusive.
  </p>'''


def render_html(obs: dict, rel: dict, gen_count: int, canary_count: int,
                 timeline: list[dict], attribution: list[dict]) -> str:
    trend = obs["first_try_trend"]
    if trend is None:
        trend_html = '<span class="trend-pill" style="color: var(--text-faint); background: transparent; border-color: var(--border);">no prior window</span>'
    else:
        direction = "up" if trend >= 0 else "down"
        arrow_points = "5 12 12 5 19 12" if trend >= 0 else "5 12 12 19 19 12"
        line_y = "19 5" if trend >= 0 else "5 19"
        y1, y2 = line_y.split()
        sign = "+" if trend >= 0 else ""
        trend_html = f'''<span class="trend-pill {direction}">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><line x1="12" y1="{y1}" x2="12" y2="{y2}"/><polyline points="{arrow_points}"/></svg>
      {sign}{trend} pts vs. prior window
    </span>'''

    first_try = obs["first_try_success_rate"]
    first_try_display = f"{first_try}" if first_try is not None else "&mdash;"
    confidence = obs["first_try_confidence"]
    confidence_color = _CONFIDENCE_COLOR[confidence]

    stages = rel["stage_rates"]
    stage_items = []
    for label, key in (("Build", "build"), ("Runtime", "runtime"), ("CRUD", "crud"),
                        ("Browser UX", "browser")):
        rate = stages.get(key)
        pct = rate if rate is not None else 0
        stage_items.append(f'''    <div class="stage-item">
      <div class="eyebrow">{label}</div>
      <div class="stage-bar-track"><div class="stage-bar-fill" style="width: {pct}%; background: {_stage_color(rate)};"></div></div>
      <div class="stage-value mono">{f"{rate}%" if rate is not None else "&mdash;"}</div>
    </div>''')
    deploy_rate = obs["deploy_rate"]
    stage_items.append(f'''    <div class="stage-item">
      <div class="eyebrow">Deploy</div>
      <div class="stage-bar-track"><div class="stage-bar-fill" style="width: {deploy_rate or 2}%; background: {_stage_color(deploy_rate)};"></div></div>
      <div class="stage-value mono">{f"{deploy_rate}%" if deploy_rate is not None else "&mdash;"}</div>
    </div>''')

    dot_class = _canary_dot_class(obs["canary_health"])
    dot_color = {"good": "var(--good)", "warn": "var(--warn)", "bad": "var(--bad)"}.get(
        dot_class, "var(--text-faint)")

    regression_class = "accent-good" if obs["regression_alerts"] == 0 else "accent-warn"

    return f'''<title>ForgeAI Observatory</title>
<style>
  :root {{
    --bg: #0A0D13;
    --panel: #12161F;
    --border: #232B3A;
    --border-soft: #1A2029;
    --text: #E8ECF3;
    --text-muted: #8892A6;
    --text-faint: #565F72;
    --accent: #E3963E;
    --good: #4CAF7D;
    --warn: #D4A72C;
    --bad: #D9534F;
  }}
  * {{ box-sizing: border-box; }}
  html, body {{ margin: 0; padding: 0; background: var(--bg); color: var(--text);
    font-family: "Segoe UI", "SF Pro Text", system-ui, -apple-system, sans-serif;
    -webkit-font-smoothing: antialiased; }}
  body {{ max-width: 920px; margin: 0 auto; padding: 40px 24px 80px; }}
  @media (prefers-reduced-motion: reduce) {{ * {{ animation: none !important; transition: none !important; }} }}
  .heading {{ font-family: "Bahnschrift", "SF Compact Display", "Segoe UI Semibold", sans-serif;
    font-weight: 600; letter-spacing: 0.01em; text-wrap: balance; }}
  .mono {{ font-family: "Cascadia Code", "SF Mono", Consolas, "JetBrains Mono", monospace;
    font-variant-numeric: tabular-nums; }}
  .eyebrow {{ font-family: "Bahnschrift", "Segoe UI Semibold", sans-serif; font-size: 0.72rem;
    font-weight: 600; letter-spacing: 0.14em; text-transform: uppercase; color: var(--text-faint); }}
  .observatory-header {{ display: flex; align-items: baseline; justify-content: space-between;
    gap: 16px; flex-wrap: wrap; padding-bottom: 20px; border-bottom: 1px solid var(--border); }}
  .observatory-header h1 {{ font-size: 1.5rem; margin: 0; color: var(--text); }}
  .observatory-header h1 span {{ color: var(--accent); }}
  .header-meta {{ display: flex; align-items: center; gap: 10px; font-size: 0.82rem; color: var(--text-muted); }}
  .status-dot {{ width: 8px; height: 8px; border-radius: 50%; display: inline-block; flex-shrink: 0; }}
  .north-star {{ padding: 40px 0 36px; border-bottom: 1px solid var(--border); }}
  .north-star-value-row {{ display: flex; align-items: baseline; gap: 20px; flex-wrap: wrap; }}
  .north-star-value {{ font-size: 5.2rem; line-height: 1; color: var(--accent); font-weight: 500; }}
  .north-star-value .unit {{ font-size: 2.4rem; opacity: 0.75; }}
  .trend-pill {{ display: inline-flex; align-items: center; gap: 5px; padding: 5px 12px 5px 10px;
    border-radius: 999px; font-size: 0.92rem; font-weight: 600; border: 1px solid transparent; }}
  .trend-pill.up {{ color: var(--good); background: rgba(76,175,125,0.14); border-color: rgba(76,175,125,0.3); }}
  .trend-pill.down {{ color: var(--bad); background: rgba(217,83,79,0.14); border-color: rgba(217,83,79,0.3); }}
  .trend-pill svg {{ width: 13px; height: 13px; }}
  .north-star-caption {{ margin-top: 10px; font-size: 0.9rem; color: var(--text-muted); max-width: 60ch; }}
  .stage-rail {{ display: flex; gap: 28px; margin-top: 28px; flex-wrap: wrap; }}
  .stage-item {{ min-width: 96px; }}
  .stage-item .eyebrow {{ margin-bottom: 6px; }}
  .stage-bar-track {{ height: 4px; background: var(--border-soft); border-radius: 2px; overflow: hidden; margin-bottom: 6px; }}
  .stage-bar-fill {{ height: 100%; border-radius: 2px; }}
  .stage-item .stage-value {{ font-size: 0.92rem; color: var(--text); }}
  .gauge-row {{ display: grid; grid-template-columns: repeat(3, 1fr); gap: 1px;
    background: var(--border); border: 1px solid var(--border); }}
  .gauge {{ background: var(--panel); padding: 24px 22px; display: flex; flex-direction: column; gap: 10px; }}
  .gauge-value {{ font-size: 1.7rem; color: var(--text); }}
  .gauge-value.accent-good {{ color: var(--good); }}
  .gauge-value.accent-warn {{ color: var(--warn); }}
  .failure-shift {{ display: flex; flex-direction: column; gap: 5px; font-size: 0.95rem; }}
  .failure-shift .was {{ color: var(--text-faint); text-decoration: line-through; font-size: 0.85rem; }}
  .failure-shift .now {{ color: var(--warn); font-weight: 600; display: flex; align-items: center; gap: 6px; }}
  .failure-shift .same {{ color: var(--text); font-weight: 600; }}
  .gauge-sub {{ font-size: 0.8rem; color: var(--text-faint); margin-top: auto; }}
  .section {{ padding: 36px 0 8px; }}
  .section-head {{ display: flex; align-items: baseline; justify-content: space-between; margin-bottom: 22px; }}
  .section-head h2 {{ font-size: 1.05rem; margin: 0; color: var(--text); }}
  .section-head .total {{ font-size: 0.85rem; color: var(--text-muted); }}
  .prevention-empty {{ padding: 28px; border: 1px dashed var(--border); color: var(--text-muted);
    font-size: 0.9rem; line-height: 1.6; }}
  .prevention-empty code {{ font-family: "Cascadia Code", monospace; color: var(--text-muted); }}
  .prevention-list {{ display: flex; flex-direction: column; gap: 14px; }}
  .prevention-row {{ display: grid; grid-template-columns: 150px 1fr 56px; align-items: center; gap: 14px; }}
  .prevention-label {{ font-size: 0.88rem; color: var(--text); }}
  .prevention-track {{ height: 8px; background: var(--border-soft); border-radius: 1px; overflow: hidden; }}
  .prevention-fill {{ height: 100%; background: var(--accent); border-radius: 1px; }}
  .prevention-count {{ text-align: right; font-size: 0.92rem; color: var(--text); }}
  .observatory-footer {{ margin-top: 40px; padding-top: 20px; border-top: 1px solid var(--border);
    font-size: 0.78rem; color: var(--text-faint); line-height: 1.7; }}
  .observatory-footer code {{ font-family: "Cascadia Code", "SF Mono", Consolas, monospace; color: var(--text-muted); }}
  .confidence-pill {{ font-size: 0.78rem; font-weight: 600; padding: 5px 10px; border: 1px solid; border-radius: 999px; }}
  .timeline-list {{ display: flex; flex-direction: column; }}
  .timeline-row {{ display: grid; grid-template-columns: 220px 1fr 52px; align-items: center; gap: 16px; padding: 9px 0; }}
  .timeline-connector {{ width: 1px; height: 10px; background: var(--border); margin-left: 4px; }}
  .timeline-label {{ display: flex; flex-direction: column; gap: 2px; }}
  .timeline-name {{ font-size: 0.86rem; color: var(--text); }}
  .timeline-date {{ font-size: 0.72rem; color: var(--text-faint); }}
  .timeline-bar-track {{ height: 6px; background: var(--border-soft); border-radius: 1px; overflow: hidden; }}
  .timeline-bar-fill {{ height: 100%; background: var(--accent); opacity: 0.85; border-radius: 1px; }}
  .timeline-score {{ text-align: right; font-size: 0.9rem; color: var(--text); }}
  .attribution-list {{ display: flex; flex-direction: column; gap: 1px; background: var(--border);
    border: 1px solid var(--border); }}
  .attribution-card {{ background: var(--panel); padding: 16px 20px; display: flex; flex-direction: column; gap: 12px; }}
  .attribution-head {{ display: flex; align-items: baseline; justify-content: space-between; }}
  .attribution-label {{ font-size: 0.92rem; color: var(--text); font-weight: 600; }}
  .attribution-date {{ font-size: 0.76rem; color: var(--text-faint); }}
  .attribution-grid {{ display: grid; grid-template-columns: repeat(5, 1fr); gap: 12px; }}
  .attribution-num {{ font-size: 1rem; margin-top: 3px; }}
  .attribution-note {{ font-size: 0.78rem; color: var(--text-faint); line-height: 1.6; margin-top: 14px; max-width: 68ch; }}
</style>

<div class="observatory-header">
  <h1 class="heading">Forge<span>AI</span> Observatory</h1>
  <div class="header-meta">
    <span class="status-dot" style="background: {dot_color};" aria-hidden="true"></span>
    <span>Canary <strong style="color: {dot_color};">{obs["canary_health"]}</strong></span>
    <span style="color: var(--border);">&middot;</span>
    <span>last run <span class="mono">{obs["canary_label"]}</span></span>
  </div>
</div>

<section class="north-star">
  <div class="eyebrow">First-Try Success &mdash; North Star</div>
  <div class="north-star-value-row">
    <div class="north-star-value mono">{first_try_display}<span class="unit">%</span></div>
    {trend_html}
    <span class="confidence-pill" style="color: {confidence_color}; border-color: {confidence_color};">
      Confidence: {confidence}
    </span>
  </div>
  <p class="north-star-caption">
    Fraction of the last {obs["window"]} generations that reached deploy-ready
    with zero fix iterations. Computed from {gen_count} logged generations and
    {canary_count} canary runs. Confidence is evidence size (generations in
    the window), not a significance test &mdash; {obs["window"]} data points
    is {confidence.lower()} evidence by this project's own variance history.
  </p>
  <div class="stage-rail">
{chr(10).join(stage_items)}
  </div>
</section>

<section class="gauge-row">
  <div class="gauge">
    <div class="eyebrow">Top Failure</div>
    {_render_failure_shift(obs)}
    <div class="gauge-sub">All-time leader vs. last {obs["window"]} runs</div>
  </div>
  <div class="gauge">
    <div class="eyebrow">Repair Loops &middot; Average</div>
    <div class="gauge-value mono">{obs["avg_fix_iterations"] if obs["avg_fix_iterations"] is not None else "&mdash;"}</div>
    <div class="gauge-sub">Fix attempts per generation, last {obs["window"]} runs</div>
  </div>
  <div class="gauge">
    <div class="eyebrow">Regression Alerts</div>
    <div class="gauge-value mono {regression_class}">{obs["regression_alerts"]}</div>
    <div class="gauge-sub">Fix attempts that made things worse, last {obs["window"]} runs</div>
  </div>
</section>

<section class="section">
{_render_prevention_section(obs)}
</section>

<section class="section">
{_render_timeline_section(timeline)}
</section>

<section class="section">
{_render_attribution_section(attribution)}
</section>

<div class="observatory-footer">
  Generated from <code>generation_log.jsonl</code> ({gen_count} entries) and
  <code>canary_history.json</code> ({canary_count} runs) via
  <code>app/memory/reliability_metrics.py::compute_observatory()</code>.
  Internal only &mdash; re-run <code>python scripts/observatory.py</code> after
  any generation cycle to refresh.
</div>
'''


def main():
    parser = argparse.ArgumentParser(description="Generate the ForgeAI Observatory cockpit page.")
    parser.add_argument("--out", default=str(_BACKEND_ROOT / "observatory_report.html"))
    args = parser.parse_args()

    from app.memory.reliability_metrics import (
        compute_observatory, compute_reliability_metrics,
        compute_reliability_timeline, compute_experiment_attribution,
    )

    gen_entries = _load_jsonl(FAILURE_DIR / "generation_log.jsonl")
    canary_path = _BACKEND_ROOT / "benchmark_results" / "canary_history.json"
    canary_runs = []
    if canary_path.exists():
        try:
            canary_runs = json.loads(canary_path.read_text(encoding="utf-8")).get("runs", [])
        except Exception:
            pass

    obs = compute_observatory(gen_entries, canary_runs)
    rel = compute_reliability_metrics(gen_entries, canary_runs)
    timeline = compute_reliability_timeline(canary_runs)
    attribution = compute_experiment_attribution(canary_runs)
    html = render_html(obs, rel, len(gen_entries), len(canary_runs), timeline, attribution)

    out_path = Path(args.out)
    out_path.write_text(html, encoding="utf-8")
    print(f"Observatory written to {out_path}")
    print(f"First-try success: {obs['first_try_success_rate']}%  |  "
          f"Canary: {obs['canary_health']}  |  "
          f"Prevention total: {obs['prevention_total']}  |  "
          f"Timeline points: {len(timeline)}  |  Attribution entries: {len(attribution)}")


if __name__ == "__main__":
    main()
