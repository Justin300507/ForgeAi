import React from "react";

// One status-pill vocabulary for everywhere a job/run state appears
// (Dashboard list rows, ProjectDetail header, Observatory). Colors stay
// semantic -- these are working screens and "failed = red-ish" must stay
// legible -- but they're drawn from the house palette's softened tints,
// defined once here instead of re-hardcoded per page.
const STATUS_STYLES = {
  pending:   { bg: "rgba(250,204,21,0.12)",  color: "#facc15", border: "rgba(250,204,21,0.25)",  glow: "rgba(250,204,21,0.35)" },
  running:   { bg: "rgba(99,102,241,0.12)",  color: "#818cf8", border: "rgba(99,102,241,0.25)",  glow: "rgba(99,102,241,0.45)" },
  done:      { bg: "rgba(34,197,94,0.12)",   color: "#4ade80", border: "rgba(34,197,94,0.25)",   glow: "rgba(34,197,94,0.4)" },
  error:     { bg: "rgba(239,68,68,0.12)",   color: "#f87171", border: "rgba(239,68,68,0.25)",   glow: "rgba(239,68,68,0.4)" },
  cancelled: { bg: "rgba(156,163,175,0.12)", color: "#9ca3af", border: "rgba(156,163,175,0.25)", glow: "rgba(156,163,175,0.3)" },
};

// live=true adds the pulsing current-color dot AND a soft breathing glow
// in the badge's own status color (.badge-live in index.css) -- the
// mission-control treatment, part of the primitive so Observatory and
// the run view can't drift into per-page glow hacks.
export default function StatusBadge({ status, live = false, className = "", style = {}, children }) {
  const s = STATUS_STYLES[status] || STATUS_STYLES.error;
  return (
    <span
      className={`flex items-center justify-center gap-1.5 text-xs font-medium px-2.5 py-1 rounded-full ${live ? "badge-live " : ""}${className}`}
      style={{
        background: s.bg,
        color: s.color,
        border: `1px solid ${s.border}`,
        ...(live ? { "--badge-glow": s.glow } : {}),
        ...style,
      }}
    >
      {live && (
        <span
          className="live-dot"
          style={{ width: 5, height: 5, background: "currentColor", borderRadius: 9999 }}
          aria-hidden="true"
        />
      )}
      {children ?? status}
    </span>
  );
}
