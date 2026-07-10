import React from "react";
import { Link } from "react-router-dom";
import { VIDEOS } from "../lib/cinematic";

// Cinematic backdrop shared by Login/Register: the landing's first ambient
// video under a heavy scrim, with the content card floating in liquid glass.
// If the video fails to load, the black base + brand glow still reads fine.
export default function AuthScene({ children }) {
  return (
    <div className="relative min-h-screen overflow-hidden bg-black flex flex-col items-center justify-center px-4 py-10">
      <video
        src={VIDEOS[0].src}
        autoPlay
        muted
        loop
        playsInline
        disablePictureInPicture
        aria-hidden="true"
        tabIndex={-1}
        onError={(e) => { e.currentTarget.style.display = "none"; }}
        className="absolute inset-0 w-full h-full object-cover opacity-60"
      />
      <div
        className="absolute inset-0"
        style={{
          background:
            "radial-gradient(900px 600px at 50% 110%, rgba(124,58,237,0.25), transparent 65%), rgba(0,0,0,0.55)",
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
