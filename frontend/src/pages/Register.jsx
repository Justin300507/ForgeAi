import React, { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { Eye, EyeOff, Loader2 } from "lucide-react";
import { useAuth } from "../AuthContext";
import AuthScene from "../components/AuthScene";
import GlassInput from "../components/GlassInput";
import PillButton from "../components/PillButton";
import { useVeil } from "../components/Veil";
import { IDEA_DRAFT_KEY } from "../lib/cinematic";

const SANS = { fontFamily: "system-ui, sans-serif" };

export default function Register() {
  const { register } = useAuth();
  const veil = useVeil();
  const nav = useNavigate();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [showPw, setShowPw] = useState(false);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  // If they typed an idea on the landing page, keep the thread going.
  const hasDraft = Boolean(sessionStorage.getItem(IDEA_DRAFT_KEY));

  const submit = async (e) => {
    e.preventDefault(); setError(""); setLoading(true);
    // Cover with the veil while the account is created, so the hop into
    // the app is a reveal instead of a hard page swap.
    const covered = veil.cover();
    try {
      await register(email, password);
      await covered;
      nav(hasDraft ? "/new" : "/dashboard");
      veil.liftSoon();
    } catch (err) {
      veil.lift();
      setError(err.response?.data?.detail || "Registration failed");
      setLoading(false);
    }
  };

  return (
    <AuthScene>
      <div className="glass-panel rounded-3xl p-8" style={SANS}>
        <h1 className="hero-serif text-3xl text-white mb-1">
          Create your account
        </h1>
        <p className="text-white/60 text-sm mb-6">
          {hasDraft ? "One step away from forging your idea" : "Start building apps with AI"}
        </p>
        {error && (
          <div role="alert" className="text-red-300 text-sm bg-red-500/15 border border-red-400/25 rounded-xl px-4 py-3 mb-4">
            {error}
          </div>
        )}
        <form onSubmit={submit} className="space-y-4">
          <div>
            <label htmlFor="register-email" className="block text-sm text-white/70 mb-1.5">Email</label>
            <GlassInput
              id="register-email"
              type="email"
              autoComplete="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              required
              placeholder="you@example.com"
            />
          </div>
          <div>
            <label htmlFor="register-password" className="block text-sm text-white/70 mb-1.5">Password</label>
            <div className="relative">
              <GlassInput
                id="register-password"
                type={showPw ? "text" : "password"}
                autoComplete="new-password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                required
                minLength={6}
                placeholder="Min 6 characters"
                style={{ paddingRight: "2.75rem" }}
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
          <PillButton type="submit" size="lg" disabled={loading} className="w-full">
            {loading && <Loader2 size={15} className="animate-spin" aria-hidden="true" />}
            {loading ? "Creating account…" : "Create account"}
          </PillButton>
        </form>
        <p className="text-sm text-white/50 text-center mt-6">
          Already have an account?{" "}
          <Link to="/login" className="text-white underline underline-offset-4 decoration-white/40 hover:decoration-white">
            Sign in
          </Link>
        </p>
      </div>
    </AuthScene>
  );
}
