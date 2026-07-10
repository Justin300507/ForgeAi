import React from "react";
import { Link, NavLink, useNavigate } from "react-router-dom";
import { Plus, LogOut } from "lucide-react";
import { useAuth } from "../AuthContext";

const linkCls = ({ isActive }) =>
  isActive
    ? "text-sm text-white px-3 py-1.5 rounded-full bg-white/10 transition-colors"
    : "text-sm text-gray-400 hover:text-white px-3 py-1.5 rounded-full transition-colors";

export default function NavBar() {
  const { logout } = useAuth();
  const nav = useNavigate();
  const handleLogout = () => { logout(); nav("/"); };

  return (
    <nav
      className="flex items-center justify-between px-5 sm:px-8 py-4 border-b border-white/5 sticky top-0 z-10"
      style={{ background: "rgba(9,9,18,0.75)", backdropFilter: "blur(16px)", WebkitBackdropFilter: "blur(16px)" }}
    >
      <Link to="/" className="hero-serif italic text-white text-xl">
        ForgeAI
      </Link>
      <div className="flex items-center gap-1 sm:gap-2">
        <NavLink to="/dashboard" className={linkCls}>Dashboard</NavLink>
        <NavLink to="/settings" className={linkCls}>Deploy Keys</NavLink>
        <NavLink
          to="/new"
          className="ml-1 flex items-center gap-1.5 text-sm font-medium text-white pl-3 pr-4 py-2 rounded-full transition-colors hover:opacity-90"
          style={{ background: "var(--brand)" }}
        >
          <Plus size={15} aria-hidden="true" /> New App
        </NavLink>
        <button
          onClick={handleLogout}
          aria-label="Log out"
          title="Log out"
          className="ml-1 p-2.5 rounded-full text-gray-500 hover:text-white hover:bg-white/10 transition-colors"
        >
          <LogOut size={16} aria-hidden="true" />
        </button>
      </div>
    </nav>
  );
}
