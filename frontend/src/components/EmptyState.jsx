import React from "react";

// On-brand empty state: glass panel, liquid-glass icon well, serif title.
// Extracted from Dashboard's "The anvil is quiet" moment so other screens
// get the same treatment instead of a bare "No data" line.
export default function EmptyState({ icon: Icon, title, sub, children, className = "" }) {
  return (
    <div className={`anim-fade-up text-center py-20 glass-panel rounded-2xl border-dashed ${className}`}>
      {Icon && (
        <div className="mx-auto w-14 h-14 rounded-full liquid-glass flex items-center justify-center mb-5">
          <Icon size={22} style={{ color: "var(--ember-soft)" }} aria-hidden="true" />
        </div>
      )}
      <p className="hero-serif text-xl text-white mb-1">{title}</p>
      {sub && <p className="text-gray-600 text-sm mb-6">{sub}</p>}
      {children}
    </div>
  );
}
