import React from "react";
import { Link, useNavigate } from "react-router-dom";
import { useAuth } from "../AuthContext";

export default function NavBar() {
  const { logout } = useAuth();
  const nav = useNavigate();
  const handleLogout = () => { logout(); nav("/"); };

  return (
    <nav className="flex items-center justify-between px-8 py-4 border-b border-white/5 sticky top-0 z-10"
      style={{background:"rgba(9,9,18,0.85)",backdropFilter:"blur(12px)"}}>
      <Link to="/" className="flex items-center gap-2">
        <span className="text-xl">⚡</span>
        <span className="font-bold text-lg" style={{background:"linear-gradient(135deg,#a78bfa,#818cf8)",WebkitBackgroundClip:"text",WebkitTextFillColor:"transparent"}}>
          ForgeAI
        </span>
      </Link>
      <div className="flex items-center gap-4">
        <Link to="/dashboard" className="text-sm text-gray-400 hover:text-white transition-colors">Dashboard</Link>
        <Link to="/settings" className="text-sm text-gray-400 hover:text-white transition-colors">Deploy Keys</Link>
        <Link to="/new" className="text-sm font-semibold text-white px-4 py-2 rounded-lg transition-colors"
          style={{background:"#7c3aed"}}>
          + New App
        </Link>
        <button onClick={handleLogout} className="text-sm text-gray-400 hover:text-white transition-colors">
          Logout
        </button>
      </div>
    </nav>
  );
}
