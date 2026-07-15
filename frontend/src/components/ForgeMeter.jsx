import React from "react";

// The forge's charge column — a vertical meter that reads as metal
// heating, not as a percentage bar. The fill is a window onto a fixed
// molten gradient (see .forge-meter in index.css), so the visible color
// climbs from dull coal-red toward white-hot as a run progresses; while
// running, embers rise off the melt line. One shared primitive for the
// New App pipeline preview (idle), the live run view, and Observatory's
// live-runs strip — never re-implemented per page.
//
// props:
//   progress  0..1 — how far through the 8 stages the run is
//   status    "idle" | "running" | "done" | "error"
//   className sizing/positioning from the host (the host owns height)

const EMBER_COUNT = 6;

export default function ForgeMeter({ progress = 0, status = "idle", className = "" }) {
  const fill = Math.max(0, Math.min(1, status === "done" ? 1 : progress));

  // Ember flight plans are randomized once per mount — stable across
  // re-renders (log lines arrive constantly on the run view).
  const embers = React.useMemo(
    () =>
      Array.from({ length: EMBER_COUNT }, (_, i) => ({
        id: i,
        left: `${12 + Math.random() * 64}%`,
        size: 2 + Math.round(Math.random() * 2),
        delay: `${(Math.random() * 2.4).toFixed(2)}s`,
        duration: `${(1.8 + Math.random() * 1.6).toFixed(2)}s`,
      })),
    []
  );

  const stateLabel =
    status === "error" ? "run failed, forge cooling"
    : status === "done" ? "run complete"
    : status === "running" ? "forging"
    : "forge idle";

  return (
    <div
      className={`forge-meter ${status} ${className}`}
      style={{ "--fill": fill }}
      role="img"
      aria-label={`Forge charge ${Math.round(fill * 100)}% — ${stateLabel}`}
    >
      <div className="forge-meter__tube">
        <div className="forge-meter__fill">
          <div className="forge-meter__molten" />
          <div className="forge-meter__surface" />
        </div>
        {Array.from({ length: 8 }, (_, i) => (
          <span
            key={i}
            className="forge-meter__notch"
            style={{ bottom: `${(i / 7) * 100}%` }}
            aria-hidden="true"
          />
        ))}
        {status === "running" &&
          embers.map((e) => (
            <span
              key={e.id}
              className="forge-meter__ember"
              aria-hidden="true"
              style={{
                left: e.left,
                width: e.size,
                height: e.size,
                "--ember-delay": e.delay,
                "--ember-duration": e.duration,
              }}
            />
          ))}
      </div>
    </div>
  );
}
