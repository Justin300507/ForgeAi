import React from "react";
import { Link } from "react-router-dom";

// The auth threshold shared by Login/Register. No private backdrop anymore:
// the globally-mounted <Scenery /> (the same golden-hour world behind every
// app page) shows through, so the veil transition from the landing lands in
// one continuous place instead of a different black room with its own video
// (which also cost a fifth full-screen video decode). This scene only adds
// a doorway glow and the staged entrance of the card.
export default function AuthScene({ children }) {
  return (
    <div className="relative min-h-screen overflow-hidden flex flex-col items-center justify-center px-4 py-10">
      {/* Doorway glow: warm brand light pooling up from the threshold */}
      <div
        className="absolute inset-0 pointer-events-none"
        aria-hidden="true"
        style={{
          background:
            "radial-gradient(900px 600px at 50% 112%, rgba(255,138,61,0.22), transparent 65%), radial-gradient(640px 420px at 50% -12%, rgba(201,131,78,0.12), transparent 70%)",
        }}
      />
      <div className="relative z-10 w-full max-w-sm">
        <Link
          to="/"
          className="anim-fade-up hero-serif italic text-white text-2xl flex justify-center mb-8"
        >
          ForgeAI
        </Link>
        <div className="anim-scale-in" style={{ "--d": "80ms" }}>
          {children}
        </div>
      </div>
    </div>
  );
}
