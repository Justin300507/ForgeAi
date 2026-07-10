import React, { useState, useEffect } from "react";
import { Link, useNavigate } from "react-router-dom";
import { Zap, Download, Globe, Rocket, Github, Cloud, Train, Check, Loader2 } from "lucide-react";
import { jobsAPI, credentialsAPI } from "../api";
import NavBar from "../components/NavBar";
import { IDEA_DRAFT_KEY } from "../lib/cinematic";

const EXAMPLES = [
  "A habit tracker with streaks, badges, dark mode, and weekly reports",
  "An expense tracker with categories, budgets, and spending charts",
  "A CRM with contacts, deals, and activity timeline",
];

// Cerebras is intentionally absent: the account returns 402 Payment Required
// and the backend benches it, so offering it here is a dead-end choice.
const MODELS = [
  { id:"auto",   label:"Auto",   sub:"Best available" },
  { id:"gemini", label:"Gemini", sub:"Google AI" },
  { id:"groq",   label:"Groq",   sub:"Ultra fast" },
];

const DEPLOYMENTS = [
  { id:"none",       Icon:Download, label:"Download Only", sub:"Generate & download the code", requires:[] },
  { id:"cloudflare", Icon:Globe,    label:"Frontend Only", sub:"Deploy to Cloudflare Pages",   requires:["github","cloudflare"] },
  { id:"both",       Icon:Rocket,   label:"Full Stack",    sub:"GitHub + Cloudflare + Railway", requires:["github","cloudflare","railway"] },
];

const SERVICE_META = {
  github:     { label:"GitHub",     Icon:Github },
  cloudflare: { label:"Cloudflare", Icon:Cloud },
  railway:    { label:"Railway",    Icon:Train },
};

export default function NewProject() {
  const nav = useNavigate();
  const [idea, setIdea] = useState(() => sessionStorage.getItem(IDEA_DRAFT_KEY) || "");
  const [model, setModel] = useState("auto");
  const [deploy, setDeploy] = useState("none");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [connStatus, setConnStatus] = useState(null);

  useEffect(() => {
    // The landing-page draft has served its purpose once it lands here.
    sessionStorage.removeItem(IDEA_DRAFT_KEY);
    credentialsAPI.status().then(r => setConnStatus(r.data)).catch(() => {});
  }, []);

  const selected = DEPLOYMENTS.find(d => d.id === deploy);
  const requiredServices = selected?.requires ?? [];
  const allConnected = requiredServices.every(s => connStatus?.[s]?.connected === true);
  const canSubmit = !loading && !!idea.trim() && (requiredServices.length === 0 || allConnected);

  const submit = async (e) => {
    e.preventDefault();
    if (!idea.trim()) return;
    setError(""); setLoading(true);
    try {
      const res = await jobsAPI.create(idea.trim(), model, deploy, "web");
      nav(`/projects/${res.data.job_id}`);
    } catch (err) {
      setError(err.response?.data?.detail || "Failed to start generation");
      setLoading(false);
    }
  };

  return (
    <div className="app-shell">
      <NavBar />
      <div className="max-w-6xl mx-auto px-6 py-10">
        <h1 className="anim-fade-up hero-serif text-4xl text-white mb-1">Forge a new app</h1>
        <p className="anim-fade-up text-gray-500 text-sm mb-8" style={{ "--d": "60ms" }}>
          Describe your idea — AI builds the full stack and deploys it
        </p>

        <div className="grid grid-cols-1 lg:grid-cols-5 gap-6">
          {/* Left — form */}
          <form onSubmit={submit} className="lg:col-span-3 space-y-6">
            {error && (
              <div role="alert" className="text-red-400 text-sm bg-red-500/10 border border-red-500/20 rounded-xl px-4 py-3">{error}</div>
            )}

            <div className="anim-fade-up" style={{ "--d": "100ms" }}>
              <label htmlFor="idea" className="block text-sm font-medium text-gray-300 mb-2">What do you want to build?</label>
              <textarea id="idea" value={idea} onChange={e => setIdea(e.target.value)} required rows={5}
                placeholder="A habit tracker with streaks, badges, dark mode, and weekly reports..."
                className="w-full glass-panel rounded-xl px-4 py-3.5 text-sm text-white placeholder:text-gray-600 focus:outline-none resize-none focus:border-violet-500/50 bg-transparent" />
              <div className="flex flex-wrap gap-2 mt-2">
                {EXAMPLES.map((ex, i) => (
                  <button key={i} type="button" onClick={() => setIdea(ex)}
                    className="text-xs text-gray-500 hover:text-gray-200 glass-panel hover:border-white/15 rounded-full px-3 py-1.5 transition-colors">
                    {ex.slice(0, 32)}…
                  </button>
                ))}
              </div>
            </div>

            {/* Model radio cards */}
            <fieldset className="anim-fade-up" style={{ "--d": "160ms" }}>
              <legend className="block text-sm font-medium text-gray-300 mb-3">Model</legend>
              <div className="grid grid-cols-2 sm:grid-cols-3 gap-3">
                {MODELS.map(m => (
                  <button key={m.id} type="button" onClick={() => setModel(m.id)}
                    aria-pressed={model === m.id}
                    className="flex items-center gap-3 rounded-xl px-4 py-3.5 border text-left transition-all glass-panel"
                    style={{
                      background: model === m.id ? "rgba(124,58,237,0.15)" : undefined,
                      borderColor: model === m.id ? "var(--brand)" : undefined,
                    }}>
                    <div className="w-4 h-4 rounded-full border-2 flex items-center justify-center shrink-0"
                      style={{borderColor: model === m.id ? "var(--brand)" : "#444"}}>
                      {model === m.id && <div className="w-2 h-2 rounded-full" style={{background:"var(--brand)"}} />}
                    </div>
                    <div>
                      <div className="text-sm font-semibold text-white">{m.label}</div>
                      <div className="text-xs text-gray-500">{m.sub}</div>
                    </div>
                  </button>
                ))}
              </div>
            </fieldset>

            {/* Deployment radio cards */}
            <fieldset className="anim-fade-up" style={{ "--d": "220ms" }}>
              <legend className="block text-sm font-medium text-gray-300 mb-3">Deployment</legend>
              <div className="space-y-2">
                {DEPLOYMENTS.map(d => (
                  <button key={d.id} type="button" onClick={() => setDeploy(d.id)}
                    aria-pressed={deploy === d.id}
                    className="w-full flex items-center gap-4 rounded-xl px-4 py-3.5 border text-left transition-all glass-panel"
                    style={{
                      background: deploy === d.id ? "rgba(124,58,237,0.15)" : undefined,
                      borderColor: deploy === d.id ? "var(--brand)" : undefined,
                    }}>
                    <div className="w-4 h-4 rounded-full border-2 flex items-center justify-center shrink-0"
                      style={{borderColor: deploy === d.id ? "var(--brand)" : "#444"}}>
                      {deploy === d.id && <div className="w-2 h-2 rounded-full" style={{background:"var(--brand)"}} />}
                    </div>
                    <d.Icon size={18} className="text-violet-300 shrink-0" aria-hidden="true" />
                    <div className="flex-1 min-w-0">
                      <div className="text-sm font-semibold text-white">{d.label}</div>
                      <div className="text-xs text-gray-500">{d.sub}</div>
                    </div>
                    {d.id !== "none" && deploy === d.id && (
                      <span className="text-xs font-medium shrink-0" style={{color: allConnected ? "#34d399" : "#fbbf24"}}>
                        {allConnected ? "Ready" : "Setup needed"}
                      </span>
                    )}
                  </button>
                ))}
              </div>

              {/* Required accounts */}
              {requiredServices.length > 0 && (
                <div className="mt-3 glass-panel rounded-xl p-4 space-y-2.5">
                  <p className="text-xs text-gray-500 uppercase tracking-wide font-mono mb-1">Required accounts</p>
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

            <button type="submit" disabled={!canSubmit}
              className="anim-fade-up w-full flex items-center justify-center gap-2 font-semibold text-white py-3.5 rounded-full text-sm transition-all hover:opacity-90 disabled:opacity-40"
              title={!allConnected && requiredServices.length > 0 ? "Connect all required accounts first" : undefined}
              style={{background:"var(--brand)", "--d": "280ms"}}>
              {loading
                ? (<><Loader2 size={15} className="animate-spin" aria-hidden="true" /> Starting generation…</>)
                : (<><Zap size={15} aria-hidden="true" /> {deploy === "none" ? "Generate App" : "Generate & Deploy"}</>)}
            </button>
          </form>

          {/* Right — preview panel */}
          <div className="lg:col-span-2">
            <div className="anim-fade-up glass-panel rounded-2xl flex flex-col items-center justify-center h-64 lg:h-full min-h-48 text-center px-6"
              style={{ "--d": "200ms" }}>
              <div className="w-14 h-14 rounded-full liquid-glass flex items-center justify-center mb-5">
                <Zap size={22} className="text-violet-300" aria-hidden="true" />
              </div>
              <p className="text-gray-500 text-sm leading-relaxed">
                Describe your app and click Generate.<br />
                The full pipeline runs in 3–5 minutes.
              </p>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
