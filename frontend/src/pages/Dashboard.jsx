import React, { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { jobsAPI } from "../api";
import NavBar from "../components/NavBar";
import { useAuth } from "../AuthContext";
import { IDEA_DRAFT_KEY } from "../lib/cinematic";
import { Trash2, Wrench, Zap, ArrowRight } from "lucide-react";

const STATUS_STYLE = {
  pending:   {bg:"rgba(234,179,8,0.1)",   color:"#facc15", border:"rgba(234,179,8,0.2)"},
  running:   {bg:"rgba(99,102,241,0.1)",  color:"#818cf8", border:"rgba(99,102,241,0.2)"},
  done:      {bg:"rgba(34,197,94,0.1)",   color:"#4ade80", border:"rgba(34,197,94,0.2)"},
  error:     {bg:"rgba(239,68,68,0.1)",   color:"#f87171", border:"rgba(239,68,68,0.2)"},
  cancelled: {bg:"rgba(107,114,128,0.1)", color:"#9ca3af", border:"rgba(107,114,128,0.2)"},
};

function ScoreBadge({ score }) {
  if (score == null) return null;
  const grade = score >= 90 ? "A" : score >= 80 ? "B" : score >= 70 ? "C" : score >= 60 ? "D" : "F";
  const color = score >= 80 ? "#4ade80" : score >= 60 ? "#facc15" : "#f87171";
  return <span style={{color}} className="text-sm font-bold">{score} ({grade})</span>;
}

function timeAgo(iso) {
  if (!iso) return "";
  const s = Math.floor((Date.now() - new Date(iso)) / 1000);
  if (s < 60) return `${s}s ago`;
  if (s < 3600) return `${Math.floor(s/60)}m ago`;
  if (s < 86400) return `${Math.floor(s/3600)}h ago`;
  return `${Math.floor(s/86400)}d ago`;
}

function greeting() {
  const h = new Date().getHours();
  if (h < 5) return "Working late";
  if (h < 12) return "Good morning";
  if (h < 18) return "Good afternoon";
  return "Good evening";
}

export default function Dashboard() {
  const [jobs, setJobs] = useState([]);
  const [loading, setLoading] = useState(true);
  const [deleting, setDeleting] = useState(null);
  const [deletingAll, setDeletingAll] = useState(false);
  const [idea, setIdea] = useState("");
  const { user } = useAuth();
  const navigate = useNavigate();

  const fetchJobs = () => jobsAPI.list().then(r => setJobs(r.data.jobs || [])).catch(console.error).finally(() => setLoading(false));
  useEffect(() => { fetchJobs(); const t = setInterval(fetchJobs, 4000); return () => clearInterval(t); }, []);

  const forgeIdea = (e) => {
    e.preventDefault();
    if (idea.trim()) sessionStorage.setItem(IDEA_DRAFT_KEY, idea.trim());
    navigate("/new");
  };

  const handleDelete = async (e, jobId) => {
    e.preventDefault();
    e.stopPropagation();
    if (!window.confirm("Delete this project and all its files? This cannot be undone.")) return;
    setDeleting(jobId);
    try {
      await jobsAPI.delete(jobId);
      setJobs(prev => prev.filter(j => j.id !== jobId));
    } catch (err) {
      alert(err.response?.data?.detail || "Delete failed");
    } finally {
      setDeleting(null);
    }
  };

  const handleDeleteAll = async () => {
    const deletableCount = jobs.filter(j => j.status !== "pending" && j.status !== "running").length;
    if (!deletableCount) return;
    if (!window.confirm(
      `Delete all ${deletableCount} project${deletableCount === 1 ? "" : "s"} and their files? This cannot be undone.`
    )) return;
    setDeletingAll(true);
    try {
      const res = await jobsAPI.deleteAll();
      if (res.data.skipped) {
        alert(`Deleted ${res.data.deleted} project(s). ${res.data.skipped} running/pending job(s) were skipped — cancel them first to delete.`);
      }
      await fetchJobs();
    } catch (err) {
      alert(err.response?.data?.detail || "Delete all failed");
    } finally {
      setDeletingAll(false);
    }
  };

  const handleFix = async (e, jobId) => {
    e.preventDefault();
    e.stopPropagation();
    try {
      const res = await jobsAPI.retry(jobId);
      navigate(`/projects/${res.data.job_id}`);
    } catch (err) {
      alert(err.response?.data?.detail || "Fix failed");
    }
  };

  const done = jobs.filter(j => j.status === "done");
  const running = jobs.filter(j => j.status === "running" || j.status === "pending").length;
  const avgScore = done.length ? Math.round(done.reduce((s, j) => s + (j.forge_score || 0), 0) / done.length) : null;
  const firstName = user?.email ? user.email.split("@")[0] : null;

  return (
    <div className="app-shell">
      <NavBar />
      <div className="max-w-5xl mx-auto px-6 py-12">
        {/* Greeting + Forge bar */}
        <div className="anim-fade-up mb-10">
          <h1 className="hero-serif text-4xl sm:text-5xl text-white leading-tight">
            {greeting()}{firstName ? <span className="italic">, {firstName}</span> : ""}.
          </h1>
          <p className="text-gray-500 text-sm mt-2">What shall we forge today?</p>
          <form
            onSubmit={forgeIdea}
            className="glass-panel glow-focus rounded-full flex items-center gap-2 p-1.5 pl-5 mt-6 w-full max-w-xl"
          >
            <input
              type="text"
              value={idea}
              onChange={(e) => setIdea(e.target.value)}
              placeholder="Describe the app you imagine…"
              aria-label="Describe the app you want to build"
              className="flex-1 min-w-0 bg-transparent outline-none text-sm text-white placeholder:text-gray-600"
            />
            <button
              type="submit"
              className="flex items-center gap-1 bg-white text-slate-900 text-sm font-medium pl-4 pr-3 py-2 rounded-full whitespace-nowrap hover:bg-white/90 transition-colors"
            >
              Forge It
              <ArrowRight size={14} aria-hidden="true" />
            </button>
          </form>
        </div>

        {/* Stats */}
        <div className="grid grid-cols-3 gap-4 mb-12">
          {[
            { label:"Apps built",  value: jobs.length },
            { label:"Completed",   value: done.length },
            { label:"Avg score",   value: avgScore != null ? `${avgScore}/100` : "—" },
          ].map(({ label, value }, i) => (
            <div
              key={label}
              className="anim-fade-up glass-panel hover-lift rounded-2xl p-5"
              style={{ "--d": `${100 + i * 70}ms` }}
            >
              <div className="hero-serif text-3xl sm:text-4xl stat-value mb-1">{value}</div>
              <div className="text-xs uppercase tracking-widest text-gray-500">{label}</div>
            </div>
          ))}
        </div>

        <div className="flex items-center justify-between mb-4">
          <h2 className="hero-serif text-2xl text-white">Your Projects</h2>
          <div className="flex items-center gap-2">
            {running > 0 && (
              <span className="flex items-center gap-1.5 text-xs px-2.5 py-1 rounded-full border" style={{background:"rgba(99,102,241,0.1)",color:"#818cf8",borderColor:"rgba(99,102,241,0.2)"}}>
                <span className="live-dot bg-indigo-400" aria-hidden="true" /> {running} running
              </span>
            )}
            {jobs.some(j => j.status !== "pending" && j.status !== "running") && (
              <button
                onClick={handleDeleteAll}
                disabled={deletingAll}
                className="flex items-center gap-1.5 text-xs px-2.5 py-1.5 rounded-full border text-gray-500 border-white/10 hover:text-red-400 hover:border-red-500/30 hover:bg-red-500/10 transition-colors disabled:opacity-40">
                <Trash2 size={12} aria-hidden="true" /> {deletingAll ? "Deleting…" : "Delete all"}
              </button>
            )}
          </div>
        </div>

        {loading ? (
          <div className="space-y-2" aria-label="Loading projects">
            {[0, 1, 2].map(i => (
              <div key={i} className="skeleton h-16 rounded-xl" />
            ))}
          </div>
        ) : jobs.length === 0 ? (
          <div className="anim-fade-up text-center py-20 glass-panel rounded-2xl border-dashed">
            <div className="mx-auto w-14 h-14 rounded-full liquid-glass flex items-center justify-center mb-5">
              <Zap size={22} className="text-violet-300" aria-hidden="true" />
            </div>
            <p className="hero-serif text-xl text-white mb-1">The anvil is quiet</p>
            <p className="text-gray-600 text-sm mb-6">Describe an idea above — your forged apps will live here</p>
          </div>
        ) : (
          <div className="space-y-2">
            {jobs.map((job, i) => {
              const s = STATUS_STYLE[job.status] || STATUS_STYLE.error;
              const isActive = job.status === "pending" || job.status === "running";
              const isDone = job.status === "done";
              const needsFix = isDone && (job.forge_score == null || job.forge_score < 80 || !job.backend_url);
              return (
                <div key={job.id}
                  className="anim-fade-up hover-lift flex items-center gap-4 glass-panel rounded-xl px-4 py-3.5 hover:border-white/15 group cursor-pointer"
                  style={{ "--d": `${Math.min(i, 8) * 40}ms` }}
                  onClick={() => navigate(`/projects/${job.id}`)}>
                  <span className="flex items-center gap-1.5 text-xs px-2.5 py-1 rounded-full font-medium shrink-0"
                    style={{background:s.bg, color:s.color, border:`1px solid ${s.border}`}}>
                    {isActive && <span className="live-dot" style={{background:s.color, width:6, height:6}} aria-hidden="true" />}
                    {job.status}
                  </span>
                  <p className="flex-1 text-sm text-gray-300 group-hover:text-white transition-colors line-clamp-1">{job.idea}</p>
                  <div className="shrink-0 text-right">
                    {job.forge_score != null && <ScoreBadge score={job.forge_score} />}
                    <div className="text-xs text-gray-600 mt-0.5">{timeAgo(job.created_at)}</div>
                  </div>
                  {needsFix && (
                    <button
                      onClick={(e) => handleFix(e, job.id)}
                      title={`Score ${job.forge_score ?? '?'}/100 — click to fix and improve`}
                      className="shrink-0 flex items-center gap-1 px-2 py-1.5 rounded-lg text-xs font-medium text-orange-400 border border-orange-500/30 hover:bg-orange-500/10 transition-colors">
                      <Wrench size={12} aria-hidden="true" /> Fix
                    </button>
                  )}
                  {!isActive && (
                    <button
                      onClick={(e) => handleDelete(e, job.id)}
                      disabled={deleting === job.id}
                      title="Delete project"
                      aria-label="Delete project"
                      className="shrink-0 p-2 rounded-lg text-gray-600 hover:text-red-400 hover:bg-red-500/10 transition-colors disabled:opacity-40">
                      <Trash2 size={15} aria-hidden="true" />
                    </button>
                  )}
                </div>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}
