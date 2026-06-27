import React, { useEffect, useRef, useState } from "react";
import { useParams, Link } from "react-router-dom";
import { jobsAPI } from "../api";
import {
  ArrowLeft, Zap, CheckCircle, XCircle, Loader2,
  Clock, Download, ExternalLink, Github, RefreshCw, X
} from "lucide-react";

function ScoreBadge({ score }) {
  if (score == null) return null;
  const grade = score >= 90 ? "A" : score >= 80 ? "B" : score >= 70 ? "C" : score >= 60 ? "D" : "F";
  const color = score >= 80 ? "text-green-400" : score >= 60 ? "text-yellow-400" : "text-red-400";
  const bg = score >= 80 ? "bg-green-500/10 border-green-500/30" : score >= 60 ? "bg-yellow-500/10 border-yellow-500/30" : "bg-red-500/10 border-red-500/30";
  return (
    <div className={`inline-flex items-center gap-2 px-3 py-1.5 rounded-lg border ${bg}`}>
      <span className={`text-2xl font-bold ${color}`}>{score}</span>
      <span className={`text-lg font-semibold ${color}`}>({grade})</span>
      <span className="text-gray-500 text-sm">Forge Score</span>
    </div>
  );
}

const LOG_COLOR = (line) => {
  if (line.includes("ERROR") || line.includes("Error") || line.includes("failed")) return "text-red-400";
  if (line.includes("WARN") || line.includes("Warning")) return "text-yellow-400";
  if (line.includes("✓") || line.includes("success") || line.includes("done")) return "text-green-400";
  if (line.includes("[")) return "text-indigo-300";
  return "text-gray-300";
};

export default function JobDetail() {
  const { id } = useParams();
  const [job, setJob] = useState(null);
  const [logs, setLogs] = useState([]);
  const [error, setError] = useState("");
  const [cancelling, setCancelling] = useState(false);
  const [retrying, setRetrying] = useState(false);
  const logsRef = useRef(null);
  const wsRef = useRef(null);

  // Scroll logs to bottom
  useEffect(() => {
    if (logsRef.current) logsRef.current.scrollTop = logsRef.current.scrollHeight;
  }, [logs]);

  // Load initial job state
  useEffect(() => {
    jobsAPI.get(id).then(r => { setJob(r.data); setLogs(r.data.logs || []); }).catch(() => setError("Job not found"));
  }, [id]);

  // WebSocket for live logs
  useEffect(() => {
    if (!job) return;
    if (job.status !== "pending" && job.status !== "running") return;

    const proto = (import.meta.env.VITE_WS_URL ? import.meta.env.VITE_WS_URL.replace(/^http/, "ws") : (location.protocol === "https:" ? "wss" : "ws") + "://" + location.host); const ws = new WebSocket(`${proto}/ws/${id}`);
    wsRef.current = ws;

    ws.onmessage = (e) => {
      const msg = JSON.parse(e.data);
      if (msg.type === "log") setLogs(prev => [...prev, msg.message]);
      else if (msg.type === "done" || msg.type === "error" || msg.type === "cancelled") {
        jobsAPI.get(id).then(r => setJob(r.data));
        ws.close();
      }
    };
    ws.onerror = () => ws.close();

    return () => ws.close();
  }, [job?.status, id]);

  const handleCancel = async () => {
    setCancelling(true);
    try { await jobsAPI.cancel(id); jobsAPI.get(id).then(r => setJob(r.data)); }
    catch (e) { alert(e.response?.data?.detail || "Cancel failed"); }
    finally { setCancelling(false); }
  };

  const handleRetry = async () => {
    setRetrying(true);
    try {
      const res = await jobsAPI.retry(id);
      window.location.href = `/jobs/${res.data.job_id}`;
    } catch (e) {
      alert(e.response?.data?.detail || "Retry failed");
      setRetrying(false);
    }
  };

  if (error) return (
    <div className="min-h-screen bg-gray-950 flex items-center justify-center text-gray-400">
      <div className="text-center"><p className="mb-4">{error}</p><Link to="/" className="text-indigo-400 hover:underline">Back to dashboard</Link></div>
    </div>
  );

  if (!job) return (
    <div className="min-h-screen bg-gray-950 flex items-center justify-center">
      <Loader2 size={24} className="animate-spin text-gray-600" />
    </div>
  );

  const isActive = job.status === "pending" || job.status === "running";
  const isDone = job.status === "done";
  const isFailed = job.status === "error" || job.status === "cancelled";

  return (
    <div className="min-h-screen bg-gray-950 text-white">
      <nav className="border-b border-gray-800 bg-gray-900/80 backdrop-blur sticky top-0 z-10">
        <div className="max-w-5xl mx-auto px-4 h-14 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <Link to="/" className="text-gray-400 hover:text-white transition-colors"><ArrowLeft size={18} /></Link>
            <div className="flex items-center gap-2">
              <div className="bg-indigo-600 p-1.5 rounded-lg"><Zap size={16} className="text-white" /></div>
              <span className="font-bold">ForgeAI</span>
            </div>
            {job.project_name && <span className="text-gray-500 text-sm font-mono">/ {job.project_name}</span>}
          </div>
          <div className="flex items-center gap-2">
            {isActive && (
              <button onClick={handleCancel} disabled={cancelling}
                className="flex items-center gap-1.5 text-sm text-red-400 hover:text-red-300 border border-red-500/30 hover:border-red-400/50 px-3 py-1.5 rounded-lg transition-colors disabled:opacity-50">
                <X size={14} />{cancelling ? "Cancelling…" : "Cancel"}
              </button>
            )}
            {isFailed && (
              <button onClick={handleRetry} disabled={retrying}
                className="flex items-center gap-1.5 text-sm text-indigo-400 hover:text-indigo-300 border border-indigo-500/30 px-3 py-1.5 rounded-lg transition-colors disabled:opacity-50">
                <RefreshCw size={14} />{retrying ? "Retrying…" : "Retry"}
              </button>
            )}
          </div>
        </div>
      </nav>

      <div className="max-w-5xl mx-auto px-4 py-6 space-y-6">
        {/* Status header */}
        <div className="bg-gray-900 border border-gray-800 rounded-xl p-5">
          <div className="flex items-start justify-between gap-4">
            <div>
              <div className="flex items-center gap-2 mb-2">
                {isActive && <Loader2 size={16} className="animate-spin text-blue-400" />}
                {isDone && <CheckCircle size={16} className="text-green-400" />}
                {isFailed && <XCircle size={16} className="text-red-400" />}
                <span className={`text-sm font-medium capitalize ${isActive ? "text-blue-400" : isDone ? "text-green-400" : "text-red-400"}`}>
                  {job.status}
                </span>
                <span className="text-gray-600 text-xs">·</span>
                <span className="text-gray-500 text-xs">{job.provider}</span>
              </div>
              <p className="text-gray-200">{job.idea}</p>
              {job.error && <p className="text-red-400 text-sm mt-2 bg-red-500/10 border border-red-500/20 rounded-lg px-3 py-2">{job.error}</p>}
            </div>
            {isDone && job.forge_score != null && <ScoreBadge score={job.forge_score} />}
          </div>

          {/* Links */}
          {isDone && (
            <div className="flex flex-wrap items-center gap-3 mt-4 pt-4 border-t border-gray-800">
              {job.frontend_url && (
                <a href={job.frontend_url} target="_blank" rel="noopener noreferrer"
                  className="flex items-center gap-1.5 text-sm bg-indigo-600/20 hover:bg-indigo-600/30 text-indigo-300 border border-indigo-500/30 px-3 py-1.5 rounded-lg transition-colors">
                  <ExternalLink size={13} /> Live frontend
                </a>
              )}
              {job.backend_url && (
                <a href={`${job.backend_url}/docs`} target="_blank" rel="noopener noreferrer"
                  className="flex items-center gap-1.5 text-sm bg-gray-800 hover:bg-gray-700 text-gray-300 border border-gray-700 px-3 py-1.5 rounded-lg transition-colors">
                  <ExternalLink size={13} /> API docs
                </a>
              )}
              {job.github_url && (
                <a href={job.github_url} target="_blank" rel="noopener noreferrer"
                  className="flex items-center gap-1.5 text-sm bg-gray-800 hover:bg-gray-700 text-gray-300 border border-gray-700 px-3 py-1.5 rounded-lg transition-colors">
                  <Github size={13} /> GitHub
                </a>
              )}
              {job.zip_path && (
                <a href={`/api/download/${job.id}`} target="_blank" rel="noopener noreferrer"
                  className="flex items-center gap-1.5 text-sm bg-gray-800 hover:bg-gray-700 text-gray-300 border border-gray-700 px-3 py-1.5 rounded-lg transition-colors">
                  <Download size={13} /> Download zip
                </a>
              )}
            </div>
          )}
        </div>

        {/* Live logs */}
        <div>
          <div className="flex items-center gap-2 mb-2">
            <h2 className="text-sm font-medium text-gray-400">Generation log</h2>
            {isActive && <span className="inline-block w-1.5 h-1.5 rounded-full bg-blue-400 animate-pulse" />}
          </div>
          <div ref={logsRef}
            className="bg-gray-900/60 border border-gray-800 rounded-xl p-4 h-96 overflow-y-auto font-mono text-xs space-y-0.5 scroll-smooth">
            {logs.length === 0 ? (
              <p className="text-gray-600 italic">Waiting for output…</p>
            ) : (
              logs.map((line, i) => (
                <p key={i} className={`leading-relaxed ${LOG_COLOR(line)}`}>{line}</p>
              ))
            )}
            {isActive && <p className="text-gray-600 animate-pulse">▌</p>}
          </div>
        </div>
      </div>
    </div>
  );
}
