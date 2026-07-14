import React, { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { jobsAPI } from "../api";
import NavBar from "../components/NavBar";
import StatCard from "../components/StatCard";
import StatusBadge from "../components/StatusBadge";
import EmptyState from "../components/EmptyState";
import { useAuth } from "../AuthContext";
import { useVeil } from "../components/Veil";
import { IDEA_DRAFT_KEY } from "../lib/cinematic";
import { Trash2, Wrench, Zap, ArrowRight, Boxes, Rocket, Activity, ShieldCheck, AlertTriangle } from "lucide-react";

const TEMPLATES = [
  { label: "SaaS", idea: "A multi-tenant SaaS starter with team workspaces, billing, and role-based permissions" },
  { label: "CRM", idea: "A CRM with contacts, deals, and activity timeline" },
  { label: "Habit Tracker", idea: "A habit tracker with streaks, badges, dark mode, and weekly reports" },
  { label: "AI Agent", idea: "An AI agent dashboard with conversation history, tool-call logs, and usage analytics" },
];

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
  const [fetchError, setFetchError] = useState(false);
  const [deleting, setDeleting] = useState(null);
  const [deletingAll, setDeletingAll] = useState(false);
  const [idea, setIdea] = useState("");
  const { user } = useAuth();
  const { veilNav } = useVeil();
  const navigate = useNavigate();

  const fetchJobs = () => jobsAPI.list()
    .then(r => { setJobs(r.data.jobs || []); setFetchError(false); })
    .catch(() => setFetchError(true))
    .finally(() => setLoading(false));
  useEffect(() => { fetchJobs(); const t = setInterval(fetchJobs, 4000); return () => clearInterval(t); }, []);

  // Cockpit row — derived entirely from the jobs already in memory, no
  // extra requests.
  const stats = useMemo(() => {
    const total = jobs.length;
    const deployed = jobs.filter(j => j.backend_url || j.frontend_url).length;
    const inProgress = jobs.filter(j => j.status === "running" || j.status === "pending").length;
    const finished = jobs.filter(j => j.forge_score != null);
    const successRate = finished.length
      ? Math.round((finished.filter(j => j.forge_score >= 80).length / finished.length) * 100)
      : null;
    return { total, deployed, inProgress, successRate };
  }, [jobs]);

  const forgeIdea = (e) => {
    e.preventDefault();
    if (idea.trim()) sessionStorage.setItem(IDEA_DRAFT_KEY, idea.trim());
    // Same cinematic hand-off as the landing page's "Forge It" — every
    // "start something new" moment in the app gets the same veil sweep.
    veilNav("/new");
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

  const firstName = user?.email ? user.email.split("@")[0] : null;

  return (
    <div className="app-shell">
      <NavBar />
      <div className="max-w-5xl mx-auto px-6 py-12">
        {/* Greeting + Forge bar — the hero, unchanged from the landing's
            prompt-first framing. Delayed 150ms so it slides in just after
            the veil lifts from the camera-zoom exit off the landing page */}
        <div className="anim-fade-up mb-6" style={{ "--d": "150ms" }}>
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

        {/* Cockpit row — the same glass stat tile used on Observatory,
            reused rather than re-invented, with gradient headline numbers. */}
        {loading ? (
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 mt-6 mb-10" aria-label="Loading stats">
            {[0, 1, 2, 3].map(i => <div key={i} className="skeleton rounded-2xl" style={{ height: "84px" }} />)}
          </div>
        ) : (
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 mt-6 mb-10">
            <StatCard icon={Boxes} label="Total Projects" value={stats.total} delay={190} />
            <StatCard icon={Rocket} label="Deployed" value={stats.deployed} delay={230} />
            <StatCard icon={Activity} label="In Progress" value={stats.inProgress} delay={270} />
            <StatCard
              icon={ShieldCheck}
              label="Success Rate"
              value={stats.successRate != null ? `${stats.successRate}%` : "—"}
              sub={stats.successRate != null ? undefined : "no scored runs yet"}
              delay={310}
            />
          </div>
        )}

        {fetchError && (
          <div className="anim-fade-up glass-panel rounded-xl px-4 py-3 mb-6 flex items-center gap-2" style={{ borderColor: "rgba(250,204,21,0.25)" }}>
            <AlertTriangle size={15} className="text-amber-400 shrink-0" aria-hidden="true" />
            <p className="text-sm text-amber-200/90">Couldn't reach the server — this list may be out of date. Retrying automatically.</p>
          </div>
        )}

        {/* Templates — quick-fill chips, same pattern as New App's example
            chips. No navigation, just prefills the prompt above. */}
        <div className="anim-fade-up flex flex-wrap gap-2 mt-4 mb-10" style={{ "--d": "350ms" }}>
          {TEMPLATES.map((t) => (
            <button
              key={t.label}
              type="button"
              onClick={() => setIdea(t.idea)}
              className="text-xs text-gray-400 hover:text-gray-100 glass-panel hover:border-white/15 rounded-full px-3.5 py-1.5 transition-colors"
            >
              {t.label}
            </button>
          ))}
        </div>

        <div className="flex items-center justify-between mb-4">
          <h2 className="hero-serif text-2xl text-white">Recent Projects</h2>
          <div className="flex items-center gap-2">
            {stats.inProgress > 0 && (
              <span className="flex items-center gap-1.5 text-xs px-2.5 py-1 rounded-full border" style={{background:"rgba(99,102,241,0.1)",color:"#818cf8",borderColor:"rgba(99,102,241,0.2)"}}>
                <span className="live-dot bg-indigo-400" aria-hidden="true" /> {stats.inProgress} running
              </span>
            )}
            {jobs.some(j => j.status !== "pending" && j.status !== "running") && (
              <button
                onClick={handleDeleteAll}
                disabled={deletingAll}
                className="text-xs text-gray-600 hover:text-red-400 transition-colors disabled:opacity-40 underline underline-offset-2 decoration-white/20 hover:decoration-red-400/50">
                {deletingAll ? "Deleting…" : "Delete all"}
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
          <EmptyState
            icon={Zap}
            title="The anvil is quiet"
            sub="Describe an idea above — your forged apps will live here"
          />
        ) : (
          <div className="space-y-2">
            {jobs.map((job, i) => {
              const isActive = job.status === "pending" || job.status === "running";
              const isDone = job.status === "done";
              const needsFix = isDone && (job.forge_score == null || job.forge_score < 80 || !job.backend_url);
              const grade = job.forge_score != null
                ? (job.forge_score >= 90 ? "A" : job.forge_score >= 80 ? "B" : job.forge_score >= 70 ? "C" : job.forge_score >= 60 ? "D" : "F")
                : null;
              const gradeColor = job.forge_score != null
                ? (job.forge_score >= 80 ? "#4ade80" : job.forge_score >= 60 ? "#facc15" : "#f87171")
                : null;
              return (
                <div key={job.id}
                  className="anim-fade-up hover-lift flex items-center gap-4 glass-panel rounded-xl px-4 py-3.5 hover:border-white/15 group cursor-pointer"
                  style={{ "--d": `${390 + Math.min(i, 8) * 60}ms` }}
                  onClick={() => navigate(`/projects/${job.id}`)}>
                  {isDone && job.forge_score != null ? (
                    <span className="text-xs font-medium px-2.5 py-1 rounded-full shrink-0 w-24 text-center"
                      style={{background: `${gradeColor}20`, color: gradeColor, border: `1px solid ${gradeColor}40`}}>
                      ✓ {job.forge_score} · {grade}
                    </span>
                  ) : (
                    <StatusBadge status={job.status} live={isActive} className="shrink-0 w-24" />
                  )}
                  <p className="flex-1 text-sm text-gray-300 group-hover:text-white transition-colors line-clamp-1">{job.idea}</p>
                  <span className="text-xs text-gray-600 shrink-0">{timeAgo(job.created_at)}</span>
                  {needsFix && (
                    <button
                      onClick={(e) => handleFix(e, job.id)}
                      title={`Score ${job.forge_score ?? '?'}/100 — click to fix and improve`}
                      className="opacity-0 group-hover:opacity-100 transition-opacity duration-200 shrink-0 flex items-center gap-1 px-2 py-1.5 rounded-lg text-xs font-medium text-orange-400 border border-orange-500/30 hover:bg-orange-500/10">
                      <Wrench size={12} aria-hidden="true" /> Fix
                    </button>
                  )}
                  {!isActive && (
                    <button
                      onClick={(e) => handleDelete(e, job.id)}
                      disabled={deleting === job.id}
                      title="Delete project"
                      aria-label="Delete project"
                      className="opacity-0 group-hover:opacity-100 transition-opacity duration-200 shrink-0 p-2 rounded-lg text-gray-600 hover:text-red-400 hover:bg-red-500/10 disabled:opacity-40">
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
