import React, { useState, useEffect, useRef } from "react";
import { Link, useNavigate } from "react-router-dom";
import { Zap, Download, Globe, Rocket, Github, Cloud, Train, Triangle, Database, Check, Loader2, Sparkles } from "lucide-react";
import { jobsAPI, credentialsAPI } from "../api";
import NavBar from "../components/NavBar";
import { useVeil } from "../components/Veil";
import { useSceneryBoost } from "../components/SceneryBoost";
import { IDEA_DRAFT_KEY } from "../lib/cinematic";
import ForgeMeter from "../components/ForgeMeter";
import { STAGES } from "../lib/pipelineStages";
import { STAGE_COLORS_APP } from "../lib/forgeAssets";

const EXAMPLES = [
  "A habit tracker with streaks, badges, dark mode, and weekly reports",
  "An expense tracker with categories, budgets, and spending charts",
  "A CRM with contacts, deals, and activity timeline",
];

// Cerebras is the primary provider (fresh key, 2026-07-12; router made it
// first-leg 2026-07-14). Gemini is intentionally absent: its prepayment
// credits are depleted (permanent 429), so offering it is a dead-end
// choice -- the backend would just fall back to this same chain anyway.
const MODELS = [
  { id:"auto",     label:"Auto",     sub:"Best available" },
  { id:"cerebras", label:"Cerebras", sub:"Primary — fast", icon:"⚡" },
  { id:"groq",     label:"Groq",     sub:"Ultra fast", icon:"🧠" },
];

const DEPLOYMENTS = [
  { id:"none",       Icon:Download, label:"Download Only", sub:"Generate & download the code", requires:[] },
  { id:"cloudflare", Icon:Globe,    label:"Frontend Only", sub:"Deploy to Cloudflare Pages",   requires:["github","cloudflare"] },
  { id:"both",       Icon:Rocket,   label:"Full Stack",    sub:"GitHub + Cloudflare + Railway", requires:["github","cloudflare","railway"] },
  { id:"vercel",     Icon:Triangle, label:"Vercel",        sub:"Frontend + backend, one project", requires:["vercel","neon"] },
];

// Mirrors backend/app/prompts/style_system.py's STYLES catalog exactly —
// "auto" (empty override) keeps today's deterministic per-idea pick.
const STYLES = [
  { id:"",                  label:"Auto",             sub:"Picked from your idea" },
  { id:"glass",              label:"Glassmorphism",    sub:"Translucent, blurred, layered" },
  { id:"bento",              label:"Bento Grid",       sub:"Apple-style modular cards" },
  { id:"neubrutalist",       label:"Neubrutalist",     sub:"Bold blocks, hard shadows" },
  { id:"soft_clay",          label:"Soft Clay",        sub:"Pastel, rounded, tactile" },
  { id:"minimal_editorial",  label:"Minimal Editorial",sub:"Swiss-grid, huge whitespace" },
];

const INTENSITIES = [
  { id:"subtle",   label:"Subtle",   sub:"Restrained — minimal motion" },
  { id:"moderate", label:"Moderate", sub:"Today's default feel" },
  { id:"heavy",    label:"Heavy",    sub:"Scroll reveals, page transitions" },
];

const SERVICE_META = {
  github:     { label:"GitHub",     Icon:Github },
  cloudflare: { label:"Cloudflare", Icon:Cloud },
  railway:    { label:"Railway",    Icon:Train },
  vercel:     { label:"Vercel",     Icon:Triangle },
  neon:       { label:"Neon",       Icon:Database },
};

export default function NewProject() {
  const nav = useNavigate();
  const veil = useVeil();
  const sceneryBoost = useSceneryBoost();
  const [idea, setIdea] = useState(() => sessionStorage.getItem(IDEA_DRAFT_KEY) || "");
  const [model, setModel] = useState("auto");
  const [deploy, setDeploy] = useState("none");
  const [visualPolish, setVisualPolish] = useState(false);
  const [styleOverride, setStyleOverride] = useState("");
  const [motionIntensity, setMotionIntensity] = useState("moderate");
  const [loading, setLoading] = useState(false);
  const [igniting, setIgniting] = useState(false);
  const [error, setError] = useState("");
  const [connStatus, setConnStatus] = useState(null);
  // Tracks whether the user has picked a deployment card themselves — the
  // async connected-accounts default below must never override a choice.
  const deployTouched = useRef(false);

  useEffect(() => {
    // The landing/dashboard draft has served its purpose once it lands here.
    sessionStorage.removeItem(IDEA_DRAFT_KEY);
    credentialsAPI.status().then(r => {
      setConnStatus(r.data);
      // Full Stack is the default whenever the deploy accounts are ready:
      // every completed forge should end with a live, shareable app link,
      // not just a zip. "Download Only" stays the fallback when accounts
      // aren't connected.
      const ready = ["github", "cloudflare", "railway"]
        .every(s => r.data?.[s]?.connected === true);
      if (ready && !deployTouched.current) setDeploy("both");
    }).catch(() => {});
  }, []);

  const selected = DEPLOYMENTS.find(d => d.id === deploy);
  const requiredServices = selected?.requires ?? [];
  const allConnected = requiredServices.every(s => connStatus?.[s]?.connected === true);
  const canSubmit = !loading && !!idea.trim() && (requiredServices.length === 0 || allConnected);

  const submit = async (e) => {
    e.preventDefault();
    if (!idea.trim()) return;
    setError(""); setLoading(true); setIgniting(true);
    // Fire immediately on click, independent of API latency -- the AI
    // Pipeline card ignites and the Scenery backdrop brightens right away
    // so the click feels instant regardless of network timing.
    sceneryBoost?.boost();
    try {
      const res = await jobsAPI.create(idea.trim(), model, deploy, "web", visualPolish ? {
        styleOverride: styleOverride || null,
        motionIntensity,
        includeLandingPage: true,
      } : {});
      // Same veil sweep as landing/auth: crossing into the live generation
      // workspace is a scene change, not a plain route swap.
      await veil.cover();
      nav(`/projects/${res.data.job_id}`);
      veil.liftSoon();
    } catch (err) {
      veil.lift();
      setError(err.response?.data?.detail || "Failed to start generation");
      setLoading(false);
      setIgniting(false);
    }
  };

  return (
    <div className="app-shell">
      <NavBar />
      <div className="max-w-6xl mx-auto px-6 py-12">
        <h1 className="anim-fade-up hero-serif text-4xl sm:text-5xl text-white mb-2">
          What shall we <span className="italic">forge</span>?
        </h1>
        <p className="anim-fade-up text-gray-500 text-sm mb-10" style={{ "--d": "60ms" }}>
          One sentence in — a living full-stack app out.
        </p>

        <div className="grid grid-cols-1 lg:grid-cols-11 gap-6">
          {/* Left — form */}
          <form onSubmit={submit} className="lg:col-span-6 space-y-8">
            {error && (
              <div role="alert" className="text-red-400 text-sm bg-red-500/10 border border-red-500/20 rounded-xl px-4 py-3">{error}</div>
            )}

            <div className={`space-y-8 forge-recede${igniting ? " igniting" : ""}`}>
              <div className="anim-fade-up" style={{ "--d": "100ms" }}>
                <label htmlFor="idea" className="forge-mono block text-[10px] uppercase text-gray-400 mb-2">The idea</label>
                <div className="glass-panel glow-focus rounded-2xl p-1">
                  <textarea id="idea" value={idea} onChange={e => setIdea(e.target.value)} required rows={5}
                    placeholder="A habit tracker with streaks, badges, dark mode, and weekly reports..."
                    className="w-full bg-transparent rounded-xl px-4 py-3.5 text-sm text-white placeholder:text-gray-600 focus:outline-none resize-none" />
                </div>
                <div className="flex flex-wrap gap-2 mt-3">
                  {EXAMPLES.map((ex, i) => (
                    <button key={i} type="button" onClick={() => setIdea(ex)}
                      className="chip-magnetic text-xs text-gray-500 hover:text-gray-200 glass-panel hover:border-white/15 rounded-full px-3 py-1.5">
                      {ex.slice(0, 32)}…
                    </button>
                  ))}
                </div>
              </div>

              {/* Model segmented control */}
              <fieldset className="anim-fade-up" style={{ "--d": "160ms" }}>
                <legend className="forge-mono block text-[10px] uppercase text-gray-400 mb-3">Model</legend>
                <div className="liquid-glass rounded-full inline-flex p-1 gap-0.5">
                  {MODELS.map(m => (
                    <button key={m.id} type="button" onClick={() => setModel(m.id)}
                      aria-pressed={model === m.id}
                      title={m.sub}
                      className={
                        model === m.id
                          ? "bg-white text-slate-900 text-sm font-medium px-4 py-2 rounded-full transition-colors"
                          : "text-white/60 hover:text-white text-sm px-4 py-2 rounded-full transition-colors"
                      }>
                      {m.icon && <span className="mr-1" aria-hidden="true">{m.icon}</span>}{m.label}
                    </button>
                  ))}
                </div>
              </fieldset>

              {/* Visual polish — landing page + style/motion opt-in */}
              <fieldset className="anim-fade-up" style={{ "--d": "190ms" }}>
                <div className="flex items-center justify-between mb-3">
                  <legend className="forge-mono text-[10px] uppercase text-gray-400">Visual polish</legend>
                  <button type="button" role="switch" aria-checked={visualPolish}
                    onClick={() => setVisualPolish(v => !v)}
                    className={`relative w-10 h-5.5 rounded-full transition-colors shrink-0 ${visualPolish ? "bg-[#ff8a3d]" : "bg-white/10"}`}>
                    <span className={`absolute top-0.5 w-4.5 h-4.5 rounded-full bg-white transition-transform ${visualPolish ? "translate-x-[19px]" : "translate-x-0.5"}`} />
                  </button>
                </div>
                <button type="button" onClick={() => setVisualPolish(v => !v)}
                  className="w-full text-left liquid-glass rounded-2xl px-4 py-3 flex items-center gap-3 mb-3">
                  <span className="w-9 h-9 rounded-full liquid-glass flex items-center justify-center shrink-0">
                    <Sparkles size={15} className="text-[#ffb47a]" aria-hidden="true" />
                  </span>
                  <span className="min-w-0">
                    <span className="block text-sm font-medium text-white">Add a landing page</span>
                    <span className="block text-xs text-gray-500">Plus a chosen visual style and motion level for the whole app</span>
                  </span>
                </button>

                {visualPolish && (
                  <div className="space-y-3 pl-1">
                    <div>
                      <p className="forge-mono text-[10px] uppercase text-gray-500 mb-2">Style</p>
                      <div className="flex flex-wrap gap-2">
                        {STYLES.map(s => (
                          <button key={s.id} type="button" onClick={() => setStyleOverride(s.id)}
                            aria-pressed={styleOverride === s.id} title={s.sub}
                            className={
                              styleOverride === s.id
                                ? "bg-white text-slate-900 text-xs font-medium px-3 py-1.5 rounded-full transition-colors"
                                : "text-white/60 hover:text-white text-xs px-3 py-1.5 rounded-full transition-colors liquid-glass"
                            }>
                            {s.label}
                          </button>
                        ))}
                      </div>
                    </div>
                    <div>
                      <p className="forge-mono text-[10px] uppercase text-gray-500 mb-2">Motion intensity</p>
                      <div className="flex flex-wrap gap-2">
                        {INTENSITIES.map(i => (
                          <button key={i.id} type="button" onClick={() => setMotionIntensity(i.id)}
                            aria-pressed={motionIntensity === i.id} title={i.sub}
                            className={
                              motionIntensity === i.id
                                ? "bg-white text-slate-900 text-xs font-medium px-3 py-1.5 rounded-full transition-colors"
                                : "text-white/60 hover:text-white text-xs px-3 py-1.5 rounded-full transition-colors liquid-glass"
                            }>
                            {i.label}
                          </button>
                        ))}
                      </div>
                    </div>
                  </div>
                )}
              </fieldset>

              {/* Deployment cards */}
              <fieldset className="anim-fade-up" style={{ "--d": "220ms" }}>
                <legend className="forge-mono block text-[10px] uppercase text-gray-400 mb-3">Deployment</legend>
                <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
                  {DEPLOYMENTS.map(d => {
                    const isSelected = deploy === d.id;
                    return (
                      <button key={d.id} type="button"
                        onClick={() => { deployTouched.current = true; setDeploy(d.id); }}
                        aria-pressed={isSelected}
                        className={`deploy-card${isSelected ? " selected" : ""} liquid-glass rounded-2xl px-4 py-5 border text-center flex flex-col items-center gap-2`}
                        style={{
                          background: isSelected ? "rgba(255,138,61,0.12)" : undefined,
                          borderColor: isSelected ? "rgba(255,169,107,0.55)" : "rgba(255,255,255,0.08)",
                        }}>
                        <span className="w-10 h-10 rounded-full liquid-glass flex items-center justify-center">
                          <d.Icon size={17} className={isSelected ? "text-[#ffcf9e]" : "text-gray-400"} aria-hidden="true" />
                        </span>
                        <div className="text-sm font-semibold text-white">{d.label}</div>
                        <div className="text-xs text-gray-500">{d.sub}</div>
                        {d.id !== "none" && isSelected && (
                          <span className="text-xs font-medium" style={{color: allConnected ? "#34d399" : "#fbbf24"}}>
                            {allConnected ? "Ready" : "Setup needed"}
                          </span>
                        )}
                        {isSelected && <Check size={15} className="text-[#ffb47a]" aria-hidden="true" />}
                      </button>
                    );
                  })}
                </div>

                {/* Required accounts */}
                {requiredServices.length > 0 && (
                  <div className="mt-3 glass-panel rounded-xl p-4 space-y-2.5">
                    <p className="text-xs text-gray-500 uppercase tracking-widest mb-1">Required accounts</p>
                    {requiredServices.map(svc => {
                      const meta = SERVICE_META[svc];
                      const st = connStatus?.[svc];
                      const connected = st?.connected === true;
                      const acctLabel = st?.login || st?.name || st?.email || "Connected";
                      return (
                        <div key={svc} className="flex items-center gap-2.5">
                          <meta.Icon size={15} className="text-gray-400 w-5 shrink-0" aria-hidden="true" />
                          <span className="text-sm text-gray-300 w-20 shrink-0">{meta.label}</span>
                          {connected ? (
                            <span className="text-xs flex items-center gap-1" style={{color:"#34d399"}}>
                              <Check size={12} aria-hidden="true" />
                              <span className="truncate max-w-[140px]">{acctLabel}</span>
                            </span>
                          ) : (
                            <Link to="/settings"
                              className="text-xs underline underline-offset-2"
                              style={{color:"var(--brand-soft)"}}>
                              Connect →
                            </Link>
                          )}
                        </div>
                      );
                    })}
                    {!allConnected && (
                      <p className="text-xs pt-1 border-t border-white/5" style={{color:"#fbbf24",opacity:0.8}}>
                        Connect all accounts to enable deployment.
                      </p>
                    )}
                  </div>
                )}
              </fieldset>
            </div>

            <button type="submit" disabled={!canSubmit}
              className={`forge-btn-compress${igniting ? " igniting" : ""} anim-fade-up w-full flex items-center justify-center gap-2 font-medium bg-white text-slate-900 py-3.5 rounded-full text-sm hover:bg-white/90 transition-all disabled:opacity-40`}
              title={!allConnected && requiredServices.length > 0 ? "Connect all required accounts first" : undefined}
              style={{ "--d": "280ms" }}>
              {loading
                ? (<><Loader2 size={15} className="animate-spin" aria-hidden="true" /> Starting generation…</>)
                : (<><Zap size={15} aria-hidden="true" /> {deploy === "none" ? "Forge It" : "Forge & Deploy"}</>)}
            </button>
          </form>

          {/* Right — the AI Pipeline: idles with a slow ambient breathing
              pulse per stage so the card never reads as static text; on
              submit each stage ignites in sequence (see .igniting classes
              below), briefly matching the real live stepper it hands off
              to on the next page (ProjectDetail.jsx). */}
          <div className="lg:col-span-5">
            <div className="anim-fade-up glass-panel rounded-2xl p-6 lg:sticky lg:top-24" style={{ "--d": "200ms" }}>
              <div className="w-12 h-12 rounded-full liquid-glass flex items-center justify-center mb-5">
                <Zap size={20} className="text-[#ffb47a]" aria-hidden="true" />
              </div>
              <h2 className="hero-serif text-xl text-white mb-4">AI Pipeline</h2>
              {/* The ForgeMeter waits beside the stage list as a pilot
                  light; pressing Forge starts it heating, and the live
                  run view it hands off to continues the same column. */}
              <div className="flex gap-5">
                <ForgeMeter
                  progress={igniting ? 0.1 : 0}
                  status={igniting ? "running" : "idle"}
                  className="self-stretch shrink-0"
                />
                <ol className="space-y-3.5 flex-1 min-w-0">
                  {STAGES.map((stage, i) => {
                    const StageIcon = stage.Icon;
                    return (
                      <li key={stage.id} className="flex items-center gap-3 text-sm text-gray-400">
                        {/* Each stage wears its forge-arc color (cool
                            idea-blue heating through copper and ember,
                            resolving emerald). */}
                        <span
                          className={`pipeline-node-dot w-5 h-5 rounded-full flex items-center justify-center shrink-0${igniting ? " igniting" : ""}`}
                          style={{
                            "--node-delay": `${i * 420}ms`,
                            "--ignite-delay": `${i * 65}ms`,
                            color: STAGE_COLORS_APP[i]?.accent || "#ffb47a",
                            background: `${STAGE_COLORS_APP[i]?.accent || "#ffb47a"}26`,
                          }}
                          aria-hidden="true">
                          <StageIcon size={11} />
                        </span>
                        <span>{stage.label}</span>
                        <span className="text-xs text-gray-600 ml-auto">{igniting ? "…" : "Waiting…"}</span>
                      </li>
                    );
                  })}
                </ol>
              </div>
              <p className="text-xs text-gray-600 mt-5 pt-4 border-t border-white/5">
                The full pipeline typically runs 3–5 minutes.
              </p>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
