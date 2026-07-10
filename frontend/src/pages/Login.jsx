import React, { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { Eye, EyeOff, Loader2 } from "lucide-react";
import { useAuth } from "../AuthContext";
import AuthScene from "../components/AuthScene";

const SANS = { fontFamily: "system-ui, sans-serif" };

export default function Login() {
  const { login } = useAuth();
  const nav = useNavigate();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [showPw, setShowPw] = useState(false);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  const submit = async (e) => {
    e.preventDefault(); setError(""); setLoading(true);
    try { await login(email, password); nav("/dashboard"); }
    catch (err) { setError(err.response?.data?.detail || "Login failed"); }
    finally { setLoading(false); }
  };

  return (
    <AuthScene>
      <div className="liquid-glass rounded-3xl p-8" style={SANS}>
        <h1 className="hero-serif text-3xl text-white mb-1">
          Welcome back
        </h1>
        <p className="text-white/60 text-sm mb-6">Sign in to keep forging</p>
        {error && (
          <div role="alert" className="text-red-300 text-sm bg-red-500/15 border border-red-400/25 rounded-xl px-4 py-3 mb-4">
            {error}
          </div>
        )}
        <form onSubmit={submit} className="space-y-4">
          <div>
            <label htmlFor="login-email" className="block text-sm text-white/70 mb-1.5">Email</label>
            <input
              id="login-email"
              type="email"
              autoComplete="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              required
              placeholder="you@example.com"
              className="w-full rounded-xl px-4 py-3 text-sm text-white placeholder:text-white/35 bg-black/30 border border-white/15 focus:border-violet-300/70 focus:outline-none transition-colors"
            />
          </div>
          <div>
            <label htmlFor="login-password" className="block text-sm text-white/70 mb-1.5">Password</label>
            <div className="relative">
              <input
                id="login-password"
                type={showPw ? "text" : "password"}
                autoComplete="current-password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                required
                placeholder="••••••••"
                className="w-full rounded-xl pl-4 pr-11 py-3 text-sm text-white placeholder:text-white/35 bg-black/30 border border-white/15 focus:border-violet-300/70 focus:outline-none transition-colors"
              />
              <button
                type="button"
                onClick={() => setShowPw((v) => !v)}
                aria-label={showPw ? "Hide password" : "Show password"}
                className="absolute right-1 top-1/2 -translate-y-1/2 p-2.5 text-white/50 hover:text-white transition-colors"
              >
                {showPw ? <EyeOff size={16} aria-hidden="true" /> : <Eye size={16} aria-hidden="true" />}
              </button>
            </div>
          </div>
          <button
            type="submit"
            disabled={loading}
            className="w-full flex items-center justify-center gap-2 font-medium bg-white text-slate-900 py-3 rounded-full text-sm hover:bg-white/90 transition-colors disabled:opacity-50"
          >
            {loading && <Loader2 size={15} className="animate-spin" aria-hidden="true" />}
            {loading ? "Signing in…" : "Sign in"}
          </button>
        </form>
        <p className="text-sm text-white/50 text-center mt-6">
          No account?{" "}
          <Link to="/register" className="text-white underline underline-offset-4 decoration-white/40 hover:decoration-white">
            Create one free
          </Link>
        </p>
      </div>
    </AuthScene>
  );
}
