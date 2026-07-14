import React from "react";

// The one text-input recipe for glass surfaces (auth cards, credentials
// forms). Login/Register/CredentialsPage each carried their own slightly
// different inline version -- same intent, drifting values.
export default function GlassInput({ className = "", ...props }) {
  return (
    <input
      className={
        "w-full rounded-xl px-4 py-3 text-sm text-white placeholder:text-white/35 " +
        "bg-black/30 border border-white/15 focus:border-violet-300/70 " +
        "focus:outline-none transition-colors " + className
      }
      {...props}
    />
  );
}
