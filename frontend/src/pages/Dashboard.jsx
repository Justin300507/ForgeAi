import React, { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { jobsAPI } from "../api";
import NavBar from "../components/NavBar";
import { Trash2, Wrench } from "lucide-react";

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

export default function Dashboard() {
  const [jobs, setJobs] = useState([]);
  const [loading, setLoading] = useState(true);
  const [deleting, setDeleting] = useState(null);
  const [deletingAll, setDeletingAll] = useState(false);
  const navigate = useNavigate();

  const fetchJobs = () => jobsAPI.list().then(r => setJobs(r.data.jobs || [])).catch(console.error).finally(() => setLoading(false));
  useEffect(() => { fetchJobs(); const t = setInterval(fetchJobs, 4000); return () => clearInterval(t); }, []);

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

  return (
    <div className="min-h-screen" style={{background:"#090912"}}>
      <NavBar />
      <div className="max-w-5xl mx-auto px-6 py-10">
        {/* Stats */}
        <div className="grid grid-cols-3 gap-4 mb-10">
          {[
            { label:"Apps built",  value: jobs.length },
            { label:"Completed",   value: done.length },
            { label:"Avg score",   value: avgScore != null ? `${avgScore}/100` : "—" },
          ].map(({ label, value }) => (
            <div key={label} className="rounded-2xl p-5 border border-white/5" style={{background:"#12121f"}}>
              <div className="text-2xl font-bold text-white mb-1">{value}</div>
              <div className="text-sm text-gray-500">{label}</div>
            </div>
          ))}
        </div>

        <div className="flex items-center justify-between mb-4">
          <h1 className="font-bold text-lg text-white">Your Projects</h1>
          <div className="flex items-center gap-2">
            {running > 0 && (
              <span className="text-xs px-2.5 py-1 rounded-full border" style={{background:"rgba(99,102,241,0.1)",color:"#818cf8",borderColor:"rgba(99,102,241,0.2)"}}>
                ● {running} running
              </span>
            )}
            {jobs.some(j => j.status !== "pending" && j.status !== "running") && (
              <button
                onClick={handleDeleteAll}
                disabled={deletingAll}
                className="flex items-center gap-1.5 text-xs px-2.5 py-1 rounded-full border text-gray-500 border-white/10 hover:text-red-400 hover:border-red-500/30 hover:bg-red-500/10 transition-colors disabled:opacity-40">
                <Trash2 size={12} /> {deletingAll ? "Deleting…" : "Delete all"}
              </button>
            )}
          </div>
        </div>

        {loading ? (
          <div className="flex items-center justify-center py-24 text-gray-700">Loading…</div>
        ) : jobs.length === 0 ? (
          <div className="text-center py-24 rounded-2xl border border-dashed border-white/10">
            <div className="text-4xl mb-4">⚡</div>
            <p className="text-gray-400 font-medium mb-1">No projects yet</p>
            <p className="text-gray-600 text-sm mb-6">Your generated apps will appear here</p>
            <Link to="/new" className="text-sm font-semibold text-white px-5 py-2.5 rounded-lg"
              style={{background:"#7c3aed"}}>Build your first app</Link>
          </div>
        ) : (
          <div className="space-y-2">
            {jobs.map(job => {
              const s = STATUS_STYLE[job.status] || STATUS_STYLE.error;
              const isActive = job.status === "pending" || job.status === "running";
              const isDone = job.status === "done";
              const needsFix = isDone && (job.forge_score == null || job.forge_score < 80 || !job.backend_url);
              return (
                <div key={job.id}
                  className="flex items-center gap-4 rounded-xl px-4 py-3.5 border border-white/5 hover:border-white/10 transition-all group cursor-pointer"
                  style={{background:"#12121f"}}
                  onClick={() => navigate(`/projects/${job.id}`)}>
                  <span className="text-xs px-2.5 py-1 rounded-full font-medium shrink-0"
                    style={{background:s.bg, color:s.color, border:`1px solid ${s.border}`}}>
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
                      className="shrink-0 flex items-center gap-1 px-2 py-1 rounded-lg text-xs font-medium text-orange-400 border border-orange-500/30 hover:bg-orange-500/10 transition-colors">
                      <Wrench size={12} /> Fix
                    </button>
                  )}
                  {!isActive && (
                    <button
                      onClick={(e) => handleDelete(e, job.id)}
                      disabled={deleting === job.id}
                      title="Delete project"
                      className="shrink-0 p-1.5 rounded-lg text-gray-600 hover:text-red-400 hover:bg-red-500/10 transition-colors disabled:opacity-40">
                      <Trash2 size={15} />
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