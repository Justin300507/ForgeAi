import React, { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import NavBar from "../components/NavBar";
import StatCard from "../components/StatCard";
import StatusBadge from "../components/StatusBadge";
import ForgeMeter from "../components/ForgeMeter";
import { observatoryAPI, jobsAPI } from "../api";
import { STAGES, detectStage } from "../lib/pipelineStages";
import {
  ShieldCheck, AlertTriangle, TrendingUp, TrendingDown, Minus,
  Activity, FlaskConical, Rocket, Flame,
} from "lucide-react";

const HEALTH_COLOR = {
  Healthy: "#58c983",
  Degraded: "#e8b34b",
  Unhealthy: "#e6675c",
  Unknown: "#6b7280",
};

// Observatory is a watch floor, but when the forge is actually burning
// it should say so: any running/pending jobs surface here with the same
// ForgeMeter charge column the run view uses, linking into the run.
function LiveForge() {
  const [live, setLive] = useState([]);

  useEffect(() => {
    let mounted = true;
    const poll = async () => {
      try {
        const r = await jobsAPI.list();
        const active = (r.data.jobs || []).filter(
          (j) => j.status === "running" || j.status === "pending"
        );
        const detailed = await Promise.all(
          active.slice(0, 4).map(async (j) => {
            try {
              const d = await jobsAPI.get(j.id);
              const idx = Math.max(0, STAGES.findIndex((s) => s.id === detectStage(d.data.logs || [])));
              return { ...j, progress: idx / (STAGES.length - 1) };
            } catch {
              return { ...j, progress: 0 };
            }
          })
        );
        if (mounted) setLive(detailed);
      } catch {
        // Watch floor stays calm on a failed poll; next tick retries.
      }
    };
    poll();
    const t = setInterval(poll, 5000);
    return () => { mounted = false; clearInterval(t); };
  }, []);

  if (!live.length) return null;
  return (
    <div className="anim-fade-up glass-panel rounded-2xl px-5 py-4 mb-8">
      <h2 className="hero-serif text-xl text-white mb-3 flex items-center gap-2">
        <Flame size={18} className="text-[#ffb47a]" aria-hidden="true" /> Live forge
      </h2>
      <div className="grid sm:grid-cols-2 gap-3">
        {live.map((j) => (
          <Link
            key={j.id}
            to={`/projects/${j.id}`}
            className="hover-lift flex items-center gap-4 rounded-xl border border-white/5 bg-white/[0.02] px-4 py-3"
          >
            <ForgeMeter progress={j.progress} status="running" className="h-16 shrink-0" />
            <div className="flex-1 min-w-0">
              <p className="text-sm text-gray-200 line-clamp-1">{j.idea}</p>
              <div className="mt-1.5 flex">
                <StatusBadge status={j.status} live />
              </div>
            </div>
          </Link>
        ))}
      </div>
    </div>
  );
}

function TrendChart({ points }) {
  const [hover, setHover] = useState(null);
  if (!points.length) {
    return <p className="text-sm text-gray-600 py-10 text-center">No canary runs recorded yet.</p>;
  }

  const W = 760, H = 200, PAD_X = 12, PAD_Y = 16;
  const n = points.length;
  const xAt = (i) => PAD_X + (i * (W - 2 * PAD_X)) / Math.max(1, n - 1);
  const yAt = (v) => H - PAD_Y - (Math.max(0, Math.min(100, v)) / 100) * (H - 2 * PAD_Y);

  const path = points.map((p, i) => `${i === 0 ? "M" : "L"} ${xAt(i).toFixed(1)} ${yAt(p.avg_score).toFixed(1)}`).join(" ");
  const areaPath = `${path} L ${xAt(n - 1).toFixed(1)} ${H - PAD_Y} L ${xAt(0).toFixed(1)} ${H - PAD_Y} Z`;

  return (
    <div className="relative">
      <svg viewBox={`0 0 ${W} ${H}`} className="w-full h-[200px]" role="img" aria-label="Canary average Forge Score over time">
        {/* recessive gridlines at 0/50/100 */}
        {[0, 50, 100].map((v) => (
          <line key={v} x1={PAD_X} x2={W - PAD_X} y1={yAt(v)} y2={yAt(v)} stroke="rgba(255,255,255,0.06)" strokeWidth="1" />
        ))}
        <defs>
          <linearGradient id="obs-trend-fill" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="#8fa7c4" stopOpacity="0.22" />
            <stop offset="100%" stopColor="#8fa7c4" stopOpacity="0" />
          </linearGradient>
        </defs>
        <path d={areaPath} fill="url(#obs-trend-fill)" stroke="none" />
        <path d={path} fill="none" stroke="#8fa7c4" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
        {points.map((p, i) => (
          <circle
            key={i}
            cx={xAt(i)} cy={yAt(p.avg_score)}
            r={hover === i ? 5 : 3}
            fill={hover === i ? "#cfdcec" : "#8fa7c4"}
            style={{ transition: "r 120ms ease-out" }}
          />
        ))}
        {/* wide invisible hit targets, bigger than the marks */}
        {points.map((p, i) => (
          <rect
            key={`hit-${i}`}
            x={xAt(i) - (W / n) / 2} y={0} width={W / n} height={H}
            fill="transparent"
            onMouseEnter={() => setHover(i)}
            onMouseLeave={() => setHover((h) => (h === i ? null : h))}
            style={{ cursor: "pointer" }}
          />
        ))}
      </svg>
      {hover != null && (
        <div
          className="absolute glass-panel rounded-lg px-3 py-2 text-xs pointer-events-none"
          style={{
            left: `${(xAt(hover) / W) * 100}%`,
            top: 0,
            transform: `translateX(${hover < n / 2 ? "8px" : "-108%"})`,
          }}
        >
          <p className="text-white font-medium">{points[hover].label}</p>
          <p className="text-gray-500">{points[hover].timestamp} · n={points[hover].n}</p>
          <p className="text-[#aec1da] mt-0.5">avg score {points[hover].avg_score}</p>
        </div>
      )}
    </div>
  );
}

function PreventionBars({ byCategory }) {
  const entries = Object.entries(byCategory || {}).sort((a, b) => b[1] - a[1]);
  const max = Math.max(1, ...entries.map(([, v]) => v));
  if (!entries.length) return <p className="text-sm text-gray-600">No deterministic preventions recorded yet.</p>;
  return (
    <div className="space-y-2.5">
      {entries.map(([name, count]) => (
        <div key={name}>
          <div className="flex items-center justify-between text-xs mb-1">
            <span className="text-gray-400">{name}</span>
            <span className="text-gray-600 tabular-nums">{count}</span>
          </div>
          <div className="h-1.5 rounded-full bg-white/5 overflow-hidden">
            <div
              className="h-full rounded-full"
              style={{ width: `${(count / max) * 100}%`, background: "linear-gradient(90deg, #8fa7c4, #ffb47a)" }}
            />
          </div>
        </div>
      ))}
    </div>
  );
}

function AttributionRow({ row, delay }) {
  const color = row.direction === "improved" ? "#58c983" : row.direction === "regressed" ? "#e6675c" : "#6b7280";
  const Icon = row.direction === "improved" ? TrendingUp : row.direction === "regressed" ? TrendingDown : Minus;
  return (
    <div className="anim-fade-up flex items-center gap-3 glass-panel rounded-xl px-4 py-3" style={{ "--d": `${delay}ms` }}>
      <Icon size={16} style={{ color }} aria-hidden="true" />
      <div className="flex-1 min-w-0">
        <p className="text-sm text-gray-200 truncate">{row.label}</p>
        <p className="text-xs text-gray-600">{row.timestamp} · confidence {row.confidence.toLowerCase()} (n={row.evidence_n})</p>
      </div>
      <div className="text-right shrink-0">
        <p className="text-xs text-gray-600 tabular-nums">{row.before} → {row.after}</p>
        <p className="text-sm font-medium tabular-nums" style={{ color }}>
          {row.delta > 0 ? "+" : ""}{row.delta}
        </p>
      </div>
    </div>
  );
}

function ExperimentCard({ exp, delay }) {
  return (
    <div className="anim-fade-up glass-panel rounded-xl px-4 py-3.5" style={{ "--d": `${delay}ms` }}>
      <div className="flex items-center gap-2 mb-1">
        <span className="text-xs font-mono text-[#aec1da]">#{exp.number}</span>
        {exp.cost_free && (
          <span className="text-[10px] px-1.5 py-0.5 rounded-full border border-emerald-500/30 text-emerald-400">
            $0
          </span>
        )}
      </div>
      <p className="text-sm text-white leading-snug mb-1">{exp.title}</p>
      <p className="text-xs text-gray-500 leading-relaxed line-clamp-3">{exp.summary}</p>
    </div>
  );
}

export default function Observatory() {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    observatoryAPI.get()
      .then((r) => setData(r.data))
      .catch((e) => setError(e.response?.data?.detail || "Failed to load Observatory data"));
  }, []);

  return (
    <div className="app-shell">
      <NavBar />
      <div className="max-w-5xl mx-auto px-6 py-12">
        <div className="anim-fade-up mb-8">
          <h1 className="hero-serif text-4xl sm:text-5xl text-white leading-tight flex items-center gap-3">
            <Activity size={34} className="text-[#ffb47a]" aria-hidden="true" />
            Observatory
          </h1>
          <p className="text-gray-500 text-sm mt-2">
            Reliability telemetry — canary trend, failure taxonomy, deterministic prevention, and recent experiments.
            Read-only, $0, no generation calls.
          </p>
        </div>

        {error && (
          <div className="glass-panel rounded-xl px-4 py-3 mb-8 flex items-center gap-2" style={{ borderColor: "rgba(250,204,21,0.25)" }}>
            <AlertTriangle size={15} className="text-amber-400 shrink-0" aria-hidden="true" />
            <p className="text-sm text-amber-200/90">{error}</p>
          </div>
        )}

        {!data && !error && (
          <div className="space-y-2" aria-label="Loading Observatory data">
            {[0, 1, 2].map((i) => <div key={i} className="skeleton h-20 rounded-2xl" />)}
          </div>
        )}

        <LiveForge />

        {data && (
          <>
            {/* Cockpit stat row */}
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 mb-8">
              <StatCard
                label="First-Try Success"
                value={data.cockpit.first_try_success_rate != null ? `${data.cockpit.first_try_success_rate}%` : "—"}
                trend={data.cockpit.first_try_trend}
                sub={`confidence: ${data.cockpit.first_try_confidence?.toLowerCase()}`}
                delay={0}
                accent="#9db8ff"
              />
              <StatCard
                label="Generation Success"
                value={data.cockpit.generation_success_rate != null ? `${data.cockpit.generation_success_rate}%` : "—"}
                sub={`last ${data.cockpit.window} generations`}
                delay={40}
                accent="#7ee0b0"
              />
              <StatCard
                label="Avg Fix Iterations"
                value={data.cockpit.avg_fix_iterations ?? "—"}
                sub="per generation"
                delay={80}
                accent="#ffb457"
              />
              <div className="anim-fade-up glass-panel rounded-2xl px-5 py-4" style={{ "--d": "120ms" }}>
                <p className="forge-mono text-[10px] text-gray-500 uppercase">Canary Health</p>
                <div className="flex items-center gap-2.5 mt-1.5">
                  {/* Mission-control heartbeat: the icon well breathes in the
                      current health color (shared badge-glow-pulse keyframe,
                      static under prefers-reduced-motion). */}
                  <span
                    className="badge-live w-8 h-8 rounded-full liquid-glass flex items-center justify-center shrink-0"
                    style={{ "--badge-glow": `${HEALTH_COLOR[data.cockpit.canary_health] || HEALTH_COLOR.Unknown}55` }}
                  >
                    {data.cockpit.canary_health === "Healthy"
                      ? <ShieldCheck size={16} style={{ color: HEALTH_COLOR[data.cockpit.canary_health] }} aria-hidden="true" />
                      : <AlertTriangle size={16} style={{ color: HEALTH_COLOR[data.cockpit.canary_health] || HEALTH_COLOR.Unknown }} aria-hidden="true" />}
                  </span>
                  <p className="hero-serif text-2xl text-white leading-none">{data.cockpit.canary_health}</p>
                </div>
                <p className="text-xs text-gray-600 mt-1 truncate">{data.cockpit.canary_label}</p>
              </div>
            </div>

            {/* Failure taxonomy shift */}
            <div className="anim-fade-up glass-panel rounded-2xl px-5 py-4 mb-8 flex flex-wrap items-center gap-x-8 gap-y-2" style={{ "--d": "160ms" }}>
              <div>
                <p className="text-xs text-gray-500 uppercase tracking-wide mb-1">Top failure — historically</p>
                <p className="text-sm text-gray-300">{data.cockpit.top_failure_historically || "—"}</p>
              </div>
              <div>
                <p className="text-xs text-gray-500 uppercase tracking-wide mb-1">Top failure — now</p>
                <p className="text-sm" style={{ color: data.cockpit.top_failure_now === data.cockpit.top_failure_historically ? "#f87171" : "#facc15" }}>
                  {data.cockpit.top_failure_now || "—"}
                </p>
              </div>
              {data.cockpit.regression_alerts > 0 && (
                <div className="flex items-center gap-1.5 text-xs text-red-400 ml-auto">
                  <AlertTriangle size={13} aria-hidden="true" />
                  {data.cockpit.regression_alerts} regression{data.cockpit.regression_alerts === 1 ? "" : "s"} in window
                </div>
              )}
            </div>

            {/* Canary trend */}
            <div className="anim-fade-up glass-panel rounded-2xl px-5 py-5 mb-8" style={{ "--d": "200ms" }}>
              <h2 className="hero-serif text-xl text-white mb-1">Canary Score Trend</h2>
              <p className="text-xs text-gray-600 mb-4">Average Forge Score per labeled canary run, chronological.</p>
              <TrendChart points={data.timeline.slice(-20)} />
            </div>

            <div className="grid sm:grid-cols-2 gap-6 mb-8">
              <div className="anim-fade-up glass-panel rounded-2xl px-5 py-5" style={{ "--d": "240ms" }}>
                <h2 className="hero-serif text-xl text-white mb-1">Deterministic Prevention</h2>
                <p className="text-xs text-gray-600 mb-4">{data.prevention.total_preventions} failures prevented before runtime, by category.</p>
                <PreventionBars byCategory={data.prevention.by_category} />
              </div>

              <div className="anim-fade-up glass-panel rounded-2xl px-5 py-5" style={{ "--d": "280ms" }}>
                <h2 className="hero-serif text-xl text-white mb-1 flex items-center gap-2">
                  <Rocket size={18} className="text-[#ffb47a]" aria-hidden="true" /> Experiment Attribution
                </h2>
                <p className="text-xs text-gray-600 mb-4">Before → after score per canary run. A confounded confidence still means "check the log," not "trust the delta."</p>
                <div className="space-y-2 max-h-[280px] overflow-y-auto pr-1">
                  {data.attribution.map((row, i) => (
                    <AttributionRow key={row.label + row.timestamp} row={row} delay={i * 30} />
                  ))}
                </div>
              </div>
            </div>

            {/* Recent experiments */}
            <div className="anim-fade-up mb-4" style={{ "--d": "320ms" }}>
              <h2 className="hero-serif text-2xl text-white flex items-center gap-2">
                <FlaskConical size={20} className="text-[#ffb47a]" aria-hidden="true" /> Recent Experiments
              </h2>
            </div>
            <div className="grid sm:grid-cols-2 gap-3">
              {data.recent_experiments.map((exp, i) => (
                <ExperimentCard key={exp.number} exp={exp} delay={i * 30} />
              ))}
            </div>
          </>
        )}
      </div>
    </div>
  );
}
