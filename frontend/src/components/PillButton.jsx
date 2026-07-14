import React from "react";

// The house primary CTA: white pill on dark glass ("Forge It", "New App",
// "Sign in", "Get Started"). One definition so landing, nav, auth, and
// app screens can't drift. variant="ghost" is the quiet secondary.
const VARIANTS = {
  primary: "bg-white text-slate-900 hover:bg-white/90",
  ghost:   "text-white/60 hover:text-white hover:bg-white/10",
  // Destructive actions (revoke key, delete project): quiet until hovered,
  // never a solid alarm-red block. Defined here so no page reaches for an
  // inline red button and reintroduces drift.
  danger:  "text-red-300 border border-red-500/30 hover:bg-red-500/10 hover:text-red-200 hover:border-red-400/50",
};

const SIZES = {
  xs: "text-xs px-3 py-1.5",
  sm: "text-sm px-4 py-2",
  md: "text-sm px-5 py-2.5",
  lg: "text-sm px-5 py-3",
};

export default function PillButton({
  variant = "primary",
  size = "sm",
  className = "",
  children,
  ...props
}) {
  return (
    <button
      className={
        `flex items-center justify-center gap-1.5 font-medium rounded-full ` +
        `transition-colors disabled:opacity-50 ` +
        `${VARIANTS[variant] || VARIANTS.primary} ${SIZES[size] || SIZES.sm} ${className}`
      }
      {...props}
    >
      {children}
    </button>
  );
}
