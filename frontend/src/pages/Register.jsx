import React, { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useAuth } from "../AuthContext";

export default function Register() {
  const { register } = useAuth();
  const nav = useNavigate();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  const submit = async (e) => {
    e.preventDefault(); setError(""); setLoading(true);
    try { await register(email, password); nav("/dashboard"); }
    catch (err) { setError(err.response?.data?.detail || "Registration failed"); }
    finally { setLoading(false); }
  };

  return (
    <div className="min-h-screen flex items-center justify-center px-4" style={{background:"#090912"}}>
      <div className="w-full max-w-sm">
        <Link to="/" className="flex items-center justify-center gap-2 mb-8">
          <span className="text-2xl">⚡</span>
          <span className="text-xl font-bold" style={{background:"linear-gradient(135deg,#a78bfa,#818cf8)",WebkitBackgroundClip:"text",WebkitTextFillColor:"transparent"}}>ForgeAI</span>
        </Link>
        <div className="rounded-2xl border border-white/8 p-8" style={{background:"#12121f"}}>
          <h1 className="text-xl font-bold text-white mb-1">Create your account</h1>
          <p className="text-gray-500 text-sm mb-6">Start building apps with AI</p>
          {error && <div className="text-red-400 text-sm bg-red-500/10 border border-red-500/20 rounded-xl px-4 py-3 mb-4">{error}</div>}
          <form onSubmit={submit} className="space-y-4">
            <div>
              <label className="block text-sm text-gray-400 mb-1.5">Email</label>
              <input type="email" value={email} onChange={e => setEmail(e.target.value)} required placeholder="you@example.com"
                className="w-full rounded-xl px-4 py-3 text-sm text-white placeholder:text-gray-600 border border-white/8 focus:outline-none focus:border-violet-500/60"
                style={{background:"#090912"}} />
            </div>
            <div>
              <label className="block text-sm text-gray-400 mb-1.5">Password</label>
              <input type="password" value={password} onChange={e => setPassword(e.target.value)} required minLength={6} placeholder="Min 6 characters"
                className="w-full rounded-xl px-4 py-3 text-sm text-white placeholder:text-gray-600 border border-white/8 focus:outline-none focus:border-violet-500/60"
                style={{background:"#090912"}} />
            </div>
            <button type="submit" disabled={loading}
              className="w-full font-semibold text-white py-3 rounded-xl text-sm disabled:opacity-50"
              style={{background:"#7c3aed"}}>
              {loading ? "Creating account…" : "Create account"}
            </button>
          </form>
          <p className="text-sm text-gray-600 text-center mt-6">
            Already have an account? <Link to="/login" className="text-violet-400 hover:text-violet-300">Sign in</Link>
          </p>
        </div>
      </div>
    </div>
  );
}