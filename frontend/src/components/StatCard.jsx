import React from "react";
import { TrendingUp, TrendingDown, Minus } from "lucide-react";

// Shared glass stat tile -- used by Dashboard's cockpit row and Observatory's
// cockpit row so both draw from the same primitive instead of two near-
// identical local components drifting apart over time.
//
// `accent` (any CSS color) ties the tile into the landing forge's stage
// color arc: it tints the icon and lights a soft ember glow along the top
// edge on hover. Omitted -> the previous neutral violet behavior.
export default function StatCard({ icon: Icon, label, value, sub, trend, delay = 0, accent }) {
  return (
    <div
      className="anim-fade-up hover-lift group glass-panel rounded-2xl px-5 py-4 relative overflow-hidden"
      style={{ "--d": `${delay}ms`, "--stat-accent": accent || "#a78bfa" }}
    >
      {/* Ember edge — wakes up on hover in the tile's own stage color */}
      <div
        aria-hidden="true"
        className="absolute inset-x-4 top-0 h-px opacity-40 group-hover:opacity-100 transition-opacity duration-500"
        style={{
          background:
            "linear-gradient(90deg, transparent, var(--stat-accent), transparent)",
        }}
      />
      <div className="flex items-start justify-between gap-2">
        <p className="forge-mono text-[10px] text-gray-500 uppercase">{label}</p>
        {Icon && (
          <Icon
            size={16}
            aria-hidden="true"
            className="shrink-0 transition-all duration-300 opacity-50 group-hover:opacity-100"
            style={{ color: "var(--stat-accent)" }}
          />
        )}
      </div>
      <div className="flex items-end gap-2 mt-1.5">
        <p className="stat-value hero-serif text-3xl leading-none">{value}</p>
        {trend != null && trend !== 0 && (
          <span
            className="flex items-center gap-0.5 text-xs font-medium mb-0.5"
            style={{ color: trend > 0 ? "#4ade80" : "#f87171" }}
          >
            {trend > 0 ? <TrendingUp size={13} aria-hidden="true" /> : <TrendingDown size={13} aria-hidden="true" />}
            {Math.abs(trend)}
          </span>
        )}
        {trend === 0 && (
          <span className="flex items-center gap-0.5 text-xs font-medium mb-0.5 text-gray-600">
            <Minus size={13} aria-hidden="true" />
          </span>
        )}
      </div>
      {sub && <p className="text-xs text-gray-600 mt-1">{sub}</p>}
    </div>
  );
}
