import React, { useState, useEffect } from "react";
import { Link, useNavigate } from "react-router-dom";
import { jobsAPI, credentialsAPI } from "../api";

const EXAMPLES = [
  "A habit tracker with streaks, badges, dark mode, and weekly reports",
  "An expense tracker with categories, budgets, and spending charts",
  "A CRM with contacts, deals, and activity timeline",
];

const MODELS = [
  { id:"auto",      label:"Auto",     sub:"Best available" },
  { id:"cerebras",  label:"Cerebras", sub:"Fast & free" },
  { id:"gemini",    label:"Gemini",   sub:"Google AI" },
  { id:"groq",      label:"Groq",     sub:"Ultra fast" },
];

const DEPLOYMENTS = [
  { id:"none",       emoji:"📁", label:"Download Only",  sub:"Generate & download the code", requires:[] },
  { id:"cloudflare", emoji:"🌐", label:"Frontend Only",  sub:"Deploy to Cloudflare Pages",   requires:["github","cloudflare"] },
  { id:"both",       emoji:"🚀", label:"Full Stack",     sub:"GitHub + Cloudflare + Railway", requires:["github","cloudflare","railway"] },
];

const SERVICE_META = {
  github:     { label:"GitHub",     icon:"🐙" },
  cloudflare: { label:"Cloudflare", icon:"☁️" },
  railway:    { label:"Railway",    icon:"🚂" },
};

function NavBar() {
  return (
    <nav className="flex items-center justify-between px-8 py-4 border-b border-white/5 sticky top-0 z-10"
      style={{background:"rgba(9,9,18,0.8)",backdropFilter:"blur(12px)"}}>
      <Link to="/" className="flex items-center gap-2">
        <span className="text-xl">⚡</span>
        <span className="font-bold text-lg" style={{background:"linear-gradient(135deg,#a78bfa,#818cf8)",WebkitBackgroundClip:"text",WebkitTextFillColor:"transparent"}}>ForgeAI</span>
      </Link>
      <div className="flex items-center gap-4">
        <Link to="/dashboard" className="text-sm text-gray-400 hover:text-white transition-colors">Dashboard</Link>
        <Link to="/settings" className="text-sm text-gray-400 hover:text-white transition-colors">Deploy Settings</Link>
        <Link to="/new" className="text-sm font-semibold text-white px-4 py-2 rounded-lg"
          style={{background:"#7c3aed"}}>+ New App</Link>
        <button onClick={() => { localStorage.removeItem("token"); window.location.href = "/"; }}
          className="text-sm text-gray-400 hover:text-white transition-colors">Logout</button>
      </div>
    </nav>
  );
}

export default function NewProject() {
  const nav = useNavigate();
  const [idea, setIdea] = useState("");
  const [model, setModel] = useState("auto");
  const [deploy, setDeploy] = useState("none");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [connStatus, setConnStatus] = useState(null);

  useEffect(() => {
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
    <div className="min-h-screen" style={{background:"#090912"}}>
      <NavBar />
      <div className="max-w-6xl mx-auto px-6 py-10">
        <h1 className="text-3xl font-bold text-white mb-1">Generate App</h1>
        <p className="text-gray-500 text-sm mb-8">Describe your idea — AI builds the full stack and deploys it</p>

        <div className="grid grid-cols-1 lg:grid-cols-5 gap-6">
          {/* Left — form */}
          <form onSubmit={submit} className="lg:col-span-3 space-y-6">
            {error && <div className="text-red-400 text-sm bg-red-500/10 border border-red-500/20 rounded-xl px-4 py-3">{error}</div>}

            <div>
              <label className="block text-sm font-medium text-gray-300 mb-2">What do you want to build?</label>
              <textarea value={idea} onChange={e => setIdea(e.target.value)} required rows={5}
                placeholder="A habit tracker with streaks, badges, dark mode, and weekly reports..."
                className="w-full rounded-xl px-4 py-3.5 text-sm text-white placeholder:text-gray-600 focus:outline-none resize-none border border-white/10 focus:border-violet-500/50"
                style={{background:"#12121f"}} />
              <div className="flex flex-wrap gap-2 mt-2">
                {EXAMPLES.map((ex, i) => (
                  <button key={i} type="button" onClick={() => setIdea(ex)}
                    className="text-xs text-gray-500 hover:text-gray-200 border border-white/10 rounded-full px-3 py-1 transition-colors"
                    style={{background:"#12121f"}}>
                    {ex.slice(0, 32)}…
                  </button>
                ))}
              </div>
            </div>

            {/* Model radio cards */}
            <div>
              <label className="block text-sm font-medium text-gray-300 mb-3">Model</label>
              <div className="grid grid-cols-2 gap-3">
                {MODELS.map(m => (
                  <button key={m.id} type="button" onClick={() => setModel(m.id)}
                    className="flex items-center gap-3 rounded-xl px-4 py-3.5 border text-left transition-all"
                    style={{
                      background: model === m.id ? "rgba(124,58,237,0.15)" : "#12121f",
                      borderColor: model === m.id ? "#7c3aed" : "rgba(255,255,255,0.08)"
                    }}>
                    <div className="w-4 h-4 rounded-full border-2 flex items-center justify-center shrink-0"
                      style={{borderColor: model === m.id ? "#7c3aed" : "#444"}}>
                      {model === m.id && <div className="w-2 h-2 rounded-full" style={{background:"#7c3aed"}} />}
                    </div>
                    <div>
                      <div className="text-sm font-semibold text-white">{m.label}</div>
                      <div className="text-xs text-gray-500">{m.sub}</div>
                    </div>
                  </button>
                ))}
              </div>
            </div>

            {/* Deployment radio cards */}
            <div>
              <label className="block text-sm font-medium text-gray-300 mb-3">Deployment</label>
              <div className="space-y-2">
                {DEPLOYMENTS.map(d => (
                  <button key={d.id} type="button" onClick={() => setDeploy(d.id)}
                    className="w-full flex items-center gap-4 rounded-xl px-4 py-3.5 border text-left transition-all"
                    style={{
                      background: deploy === d.id ? "rgba(124,58,237,0.15)" : "#12121f",
                      borderColor: deploy === d.id ? "#7c3aed" : "rgba(255,255,255,0.08)"
                    }}>
                    <div className="w-4 h-4 rounded-full border-2 flex items-center justify-center shrink-0"
                      style={{borderColor: deploy === d.id ? "#7c3aed" : "#444"}}>
                      {deploy === d.id && <div className="w-2 h-2 rounded-full" style={{background:"#7c3aed"}} />}
                    </div>
                    <span className="text-lg">{d.emoji}</span>
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
                <div className="mt-3 rounded-xl border border-white/8 p-4 space-y-2.5" style={{background:"#12121f"}}>
                  <p className="text-xs text-gray-500 uppercase tracking-wide font-mono mb-1">Required accounts</p>
                  {requiredServices.map(svc => {
                    const meta = SERVICE_META[svc];
                    const st = connStatus?.[svc];
                    const connected = st?.connected === true;
                    const acctLabel = st?.login || st?.name || st?.email || "Connected";
                    return (
                      <div key={svc} className="flex items-center gap-2.5">
                        <span className="text-base w-5 text-center">{meta.icon}</span>
                        <span className="text-sm text-gray-300 w-20 shrink-0">{meta.label}</span>
                        {connected ? (
                          <span className="text-xs flex items-center gap-1" style={{color:"#34d399"}}>
                            <span>✓</span>
                            <span className="truncate max-w-[140px]">{acctLabel}</span>
                          </span>
                        ) : (
                          <Link to="/settings"
                            className="text-xs underline underline-offset-2"
                            style={{color:"#a78bfa"}}>
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
            </div>

            <button type="submit" disabled={!canSubmit}
              className="w-full font-semibold text-white py-3.5 rounded-xl text-sm transition-all disabled:opacity-40"
              title={!allConnected && requiredServices.length > 0 ? "Connect all required accounts first" : undefined}
              style={{background:"#7c3aed"}}>
              {loading ? "Starting generation…" : deploy === "none" ? "⚡ Generate App" : "⚡ Generate & Deploy"}
            </button>
          </form>

          {/* Right — preview panel */}
          <div className="lg:col-span-2">
            <div className="rounded-2xl border border-white/5 flex flex-col items-center justify-center h-64 lg:h-full min-h-48 text-center px-6"
              style={{background:"#12121f"}}>
              <span className="text-4xl mb-4">⚡</span>
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