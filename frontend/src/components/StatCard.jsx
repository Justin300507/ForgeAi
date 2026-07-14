import React from "react";
import { TrendingUp, TrendingDown, Minus } from "lucide-react";

// Shared glass stat tile -- used by Dashboard's cockpit row and Observatory's
// cockpit row so both draw from the same primitive instead of two near-
// identical local components drifting apart over time.
export default function StatCard({ icon: Icon, label, value, sub, trend, delay = 0 }) {
  return (
    <div
      className="anim-fade-up hover-lift group glass-panel rounded-2xl px-5 py-4"
      style={{ "--d": `${delay}ms` }}
    >
      <div className="flex items-start justify-between gap-2">
        <p className="text-xs text-gray-500 uppercase tracking-wide">{label}</p>
        {Icon && (
          <Icon
            size={16}
            className="text-violet-300/50 group-hover:text-violet-300 transition-colors duration-300 shrink-0"
            aria-hidden="true"
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
