import React, { useEffect, useRef, useState, useCallback } from "react";
import { useParams, Link } from "react-router-dom";
import { jobsAPI } from "../api";
import NavBar from "../components/NavBar";

const STAGES = [
  { id:"plan",     label:"Planning",     keywords:["PRODUCT MANAGER"] },
  { id:"arch",     label:"Architecture", keywords:["ARCHITECT","TECH LEAD"] },
  { id:"backend",  label:"Backend",      keywords:["BACKEND TEAM","Wave 1","Wave 4"] },
  { id:"frontend", label:"Frontend",     keywords:["FRONTEND TEAM","START FRONTEND"] },
  { id:"validate", label:"Validation",   keywords:["VALIDATION LOOP","Fix attempt","PATCHER"] },
  { id:"runtime",  label:"Runtime",      keywords:["RUNTIME","uvicorn","smoke test"] },
  { id:"deploy",   label:"Deploy",       keywords:["Cloudflare","Render","GitHub","DEPLOY"] },
  { id:"done",     label:"Complete",     keywords:["FINAL STATUS","V6 SCORE","Forge Score"] },
];

function detectStage(logs) {
  for (let i = logs.length - 1; i >= 0; i--) {
    for (let s = STAGES.length - 1; s >= 0; s--) {
      if (STAGES[s].keywords.some(k => logs[i].includes(k))) return STAGES[s].id;
    }
  }
  return null;
}

function PipelineBar({ logs, status }) {
  const active = detectStage(logs);
  const activeIdx = STAGES.findIndex(s => s.id === active);
  return (
    <div className="flex items-center gap-1 overflow-x-auto py-1">
      {STAGES.map((stage, i) => {
        const past = activeIdx > i;
        const curr = activeIdx === i;
        const err  = status === "error" && curr;
        const fin  = status === "done" && stage.id === "done";
        return (
          <React.Fragment key={stage.id}>
            <div className="flex flex-col items-center shrink-0 min-w-0">
              <div className="w-6 h-6 rounded-full flex items-center justify-center text-xs font-bold border-2 transition-all"
                style={{
                  borderColor: err ? "#ef4444" : (fin||past) ? "#4ade80" : curr ? "#a78bfa" : "#2a2a3d",
                  background:  err ? "rgba(239,68,68,0.15)" : (fin||past) ? "rgba(74,222,128,0.15)" : curr ? "rgba(167,139,250,0.15)" : "#12121f",
                  color:       err ? "#f87171" : (fin||past) ? "#4ade80" : curr ? "#a78bfa" : "#444"
                }}>
                {(fin || past) ? "✓" : curr && !err ? "…" : err ? "✕" : i+1}
              </div>
              <span className="text-[9px] mt-1 whitespace-nowrap font-medium"
                style={{color: err ? "#f87171" : (fin||past) ? "#4ade80" : curr ? "#a78bfa" : "#444"}}>
                {stage.label}
              </span>
            </div>
            {i < STAGES.length - 1 && (
              <div className="h-px flex-1 min-w-[8px] transition-all" style={{background: past ? "#4ade8050" : "#2a2a3d"}} />
            )}
          </React.Fragment>
        );
      })}
    </div>
  );
}

const LOG_CLS = (line) => {
  if (/error|failed|FAIL/i.test(line))   return "#f87171";
  if (/warn/i.test(line))               return "#facc15";
  if (/✓|passed|OK\b|done/i.test(line)) return "#4ade80";
  if (/\[CEREBRAS\]/i.test(line))       return "#a78bfa";
  if (/===|###/.test(line))             return "#818cf8";
  return "#9ca3af";
};

export default function ProjectDetail() {
  const { id } = useParams();
  const [job, setJob] = useState(null);
  const [logs, setLogs] = useState([]);
  const [notFound, setNotFound] = useState(false);
  const logsRef = useRef(null);
  const bottom = useRef(true);

  const scrollBottom = () => { if (logsRef.current && bottom.current) logsRef.current.scrollTop = logsRef.current.scrollHeight; };
  useEffect(scrollBottom, [logs]);
  const onScroll = () => {
    if (!logsRef.current) return;
    const { scrollTop, scrollHeight, clientHeight } = logsRef.current;
    bottom.current = scrollTop + clientHeight >= scrollHeight - 30;
  };

  const fetchJob = useCallback(() =>
    jobsAPI.get(id).then(r => { setJob(r.data); setLogs(r.data.logs || []); }).catch(() => setNotFound(true)), [id]);

  useEffect(() => { fetchJob(); }, [fetchJob]);

  useEffect(() => {
    if (!job || (job.status !== "pending" && job.status !== "running")) return;
    const proto = (import.meta.env.VITE_WS_URL ? import.meta.env.VITE_WS_URL.replace(/^http/, "ws") : (location.protocol === "https:" ? "wss" : "ws") + "://" + location.host); const ws = new WebSocket(`${proto}/ws/${id}`);
    ws.onmessage = (e) => {
      const msg = JSON.parse(e.data);
      if (msg.type === "log") setLogs(p => [...p, msg.message]);
      else if (["done","error","cancelled"].includes(msg.type)) { fetchJob(); ws.close(); }
    };
    ws.onerror = () => ws.close();
    return () => ws.close();
  }, [job?.status, id, fetchJob]);

  if (notFound) return (
    <div className="min-h-screen flex items-center justify-center text-gray-500" style={{background:"#090912"}}>
      <div className="text-center"><p className="mb-4">Job not found</p><Link to="/dashboard" className="text-violet-400">Back to dashboard</Link></div>
    </div>
  );
  if (!job) return <div className="min-h-screen flex items-center justify-center text-gray-600" style={{background:"#090912"}}>Loading…</div>;

  const isActive = job.status === "pending" || job.status === "running";
  const isDone   = job.status === "done";
  const score    = job.forge_score;
  const grade    = score >= 90 ? "A" : score >= 80 ? "B" : score >= 70 ? "C" : score >= 60 ? "D" : "F";

  return (
    <div className="min-h-screen" style={{background:"#090912"}}>
      <NavBar />
      <div className="max-w-5xl mx-auto px-6 py-8 space-y-4">
        {/* Pipeline */}
        <div className="rounded-2xl border border-white/5 px-5 py-4" style={{background:"#12121f"}}>
          <PipelineBar logs={logs} status={job.status} />
        </div>

        {/* Info card */}
        <div className="rounded-2xl border border-white/5 p-5" style={{background:"#12121f"}}>
          <div className="flex items-start justify-between gap-4">
            <div className="flex-1 min-w-0">
              <div className="flex items-center gap-2 mb-2 flex-wrap">
                <span className="text-xs px-2.5 py-1 rounded-full font-medium" style={{
                  background: isActive ? "rgba(99,102,241,0.12)" : isDone ? "rgba(34,197,94,0.12)" : "rgba(239,68,68,0.12)",
                  color:      isActive ? "#818cf8" : isDone ? "#4ade80" : "#f87171",
                  border:     `1px solid ${isActive ? "rgba(99,102,241,0.25)" : isDone ? "rgba(34,197,94,0.25)" : "rgba(239,68,68,0.25)"}`
                }}>{job.status}</span>
                <span className="text-xs text-gray-600">{job.provider}</span>
              </div>
              <p className="text-gray-200 text-sm leading-relaxed">{job.idea}</p>
              {job.error && <p className="text-red-400 text-xs mt-3 px-3 py-2 rounded-lg bg-red-500/8 border border-red-500/15">{job.error}</p>}
            </div>
            {isDone && score != null && (
              <div className="text-right shrink-0">
                <div className="text-4xl font-extrabold" style={{color: score>=80?"#4ade80":score>=60?"#facc15":"#f87171"}}>{score}</div>
                <div className="text-gray-500 text-sm">({grade}) Forge Score</div>
              </div>
            )}
          </div>
          {isDone && (
            <div className="flex flex-wrap gap-2 mt-4 pt-4 border-t border-white/5">
              {job.frontend_url && <a href={job.frontend_url} target="_blank" rel="noopener noreferrer"
                className="text-xs font-medium px-3 py-1.5 rounded-lg border transition-colors"
                style={{background:"rgba(124,58,237,0.12)",color:"#a78bfa",borderColor:"rgba(124,58,237,0.25)"}}>🌐 Live frontend</a>}
              {job.backend_url && <a href={`${job.backend_url}/docs`} target="_blank" rel="noopener noreferrer"
                className="text-xs font-medium px-3 py-1.5 rounded-lg border border-white/8 text-gray-400 hover:text-white"
                style={{background:"rgba(255,255,255,0.04)"}}>📖 API docs</a>}
              {job.github_url && <a href={job.github_url} target="_blank" rel="noopener noreferrer"
                className="text-xs font-medium px-3 py-1.5 rounded-lg border border-white/8 text-gray-400 hover:text-white"
                style={{background:"rgba(255,255,255,0.04)"}}>🐙 GitHub</a>}
              {job.zip_path && <a href={`/api/download/${job.id}`} target="_blank"
                className="text-xs font-medium px-3 py-1.5 rounded-lg border border-white/8 text-gray-400 hover:text-white"
                style={{background:"rgba(255,255,255,0.04)"}}>⬇️ Download zip</a>}
            </div>
          )}
          {isActive && (
            <div className="mt-4 pt-4 border-t border-white/5">
              <button onClick={async () => { await jobsAPI.cancel(id); fetchJob(); }}
                className="text-xs text-red-400 hover:text-red-300 border border-red-500/20 px-3 py-1.5 rounded-lg transition-colors">
                Cancel generation
              </button>
            </div>
          )}
        </div>

        {/* Log terminal */}
        <div className="rounded-2xl border border-white/5 overflow-hidden" style={{background:"#12121f"}}>
          <div className="flex items-center gap-2 px-4 py-3 border-b border-white/5">
            <span className="text-xs font-medium text-gray-400">Generation log</span>
            <span className="text-xs text-gray-600">· {logs.length} lines</span>
            {isActive && <span className="w-1.5 h-1.5 rounded-full bg-violet-400 animate-pulse ml-1" />}
          </div>
          <div ref={logsRef} onScroll={onScroll}
            className="h-80 overflow-y-auto p-4 font-mono text-xs space-y-0.5"
            style={{background:"#07070f"}}>
            {logs.length === 0
              ? <p className="text-gray-700 italic">Waiting for pipeline output…</p>
              : logs.map((line, i) => <p key={i} className="leading-relaxed whitespace-pre-wrap" style={{color:LOG_CLS(line)}}>{line}</p>)
            }
            {isActive && <p className="animate-pulse" style={{color:"#333"}}>▌</p>}
          </div>
        </div>
      </div>
    </div>
  );
}