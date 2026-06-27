import React from "react";
import { Link } from "react-router-dom";

export default function Landing() {
  return (
    <div className="min-h-screen" style={{background:"#090912"}}>
      {/* Nav */}
      <nav className="flex items-center justify-between px-8 py-4 border-b border-white/5">
        <div className="flex items-center gap-2">
          <span className="text-2xl">⚡</span>
          <span className="text-xl font-bold" style={{background:"linear-gradient(135deg,#a78bfa,#818cf8)",WebkitBackgroundClip:"text",WebkitTextFillColor:"transparent"}}>ForgeAI</span>
        </div>
        <div className="flex items-center gap-3">
          <Link to="/login" className="text-sm text-gray-400 hover:text-white transition-colors px-4 py-2">Login</Link>
          <Link to="/register" className="text-sm font-semibold text-white px-5 py-2 rounded-lg transition-colors"
            style={{background:"#7c3aed"}}>Get Started</Link>
        </div>
      </nav>

      {/* Hero */}
      <div className="max-w-3xl mx-auto px-6 pt-20 pb-16 text-center">
        <div className="inline-flex items-center gap-2 border border-violet-500/30 text-violet-300 text-sm px-4 py-1.5 rounded-full mb-8"
          style={{background:"rgba(124,58,237,0.1)"}}>
          <span className="w-1.5 h-1.5 rounded-full bg-violet-400" />
          V15 — One-click app builder
        </div>

        <h1 className="text-5xl sm:text-6xl font-extrabold leading-tight mb-6 text-white">
          Build full-stack apps{" "}
          <span style={{background:"linear-gradient(135deg,#a78bfa 0%,#60a5fa 100%)",WebkitBackgroundClip:"text",WebkitTextFillColor:"transparent"}}>
            with AI
          </span>
        </h1>

        <p className="text-lg text-gray-400 max-w-xl mx-auto mb-10 leading-relaxed">
          Describe your idea. ForgeAI generates the backend, frontend, deploys to
          GitHub, Render, and Cloudflare — and hands you the live URL.
        </p>

        <div className="flex items-center justify-center gap-3 mb-6">
          <Link to="/register"
            className="font-semibold text-white px-7 py-3 rounded-lg text-sm transition-colors"
            style={{background:"#7c3aed"}}>
            Start Building Free
          </Link>
          <Link to="/login"
            className="font-semibold text-white px-7 py-3 rounded-lg text-sm border border-white/20 hover:border-white/40 transition-colors"
            style={{background:"rgba(255,255,255,0.05)"}}>
            Sign In
          </Link>
        </div>

        <p className="text-sm text-gray-600">
          Generated 12,000+ applications · FastAPI + React · Deployed in 3–5 min
        </p>
      </div>

      {/* 3 cards */}
      <div className="max-w-4xl mx-auto px-6 pb-24 grid grid-cols-1 sm:grid-cols-3 gap-4">
        {[
          { emoji:"✏️", title:"Describe", desc:"Write what you want to build in plain English" },
          { emoji:"⚡", title:"Generate", desc:"AI architects, codes, and validates your full-stack app" },
          { emoji:"🚀", title:"Deploy", desc:"Get live URLs on Cloudflare + Render in minutes" },
        ].map(({ emoji, title, desc }) => (
          <div key={title} className="rounded-2xl p-6 text-center border border-white/5"
            style={{background:"#12121f"}}>
            <div className="text-3xl mb-4">{emoji}</div>
            <h3 className="font-bold text-white mb-2">{title}</h3>
            <p className="text-sm text-gray-500 leading-relaxed">{desc}</p>
          </div>
        ))}
      </div>
    </div>
  );
}