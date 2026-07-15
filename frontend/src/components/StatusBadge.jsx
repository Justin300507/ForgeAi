import React from "react";

// One status-pill vocabulary for everywhere a job/run state appears
// (Dashboard list rows, ProjectDetail header, Observatory). Colors stay
// semantic -- these are working screens and "failed = red-ish" must stay
// legible -- but they're drawn from the house palette's softened tints,
// defined once here instead of re-hardcoded per page.
const STATUS_STYLES = {
  pending:   { bg: "rgba(232,179,75,0.12)",  color: "#e8b34b", border: "rgba(232,179,75,0.25)",  glow: "rgba(232,179,75,0.35)" },
  // Running = the forge is hot: ember, the palette's one loud voice.
  running:   { bg: "rgba(255,138,61,0.12)",  color: "#ffa96b", border: "rgba(255,138,61,0.28)",  glow: "rgba(255,138,61,0.5)" },
  done:      { bg: "rgba(88,201,131,0.12)",  color: "#58c983", border: "rgba(88,201,131,0.25)",  glow: "rgba(88,201,131,0.4)" },
  error:     { bg: "rgba(230,99,88,0.12)",   color: "#e6675c", border: "rgba(230,99,88,0.25)",   glow: "rgba(230,99,88,0.4)" },
  cancelled: { bg: "rgba(143,167,196,0.10)", color: "#9fb0c4", border: "rgba(143,167,196,0.22)", glow: "rgba(143,167,196,0.3)" },
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
