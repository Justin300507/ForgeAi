import React from "react";
import { Link, NavLink, useNavigate } from "react-router-dom";
import { Plus, LogOut } from "lucide-react";
import { useAuth } from "../AuthContext";
import { useVeil } from "./Veil";

const linkCls = ({ isActive }) =>
  isActive
    ? "text-sm text-white bg-white/10 px-3.5 py-1.5 rounded-full transition-colors"
    : "text-sm text-white/60 hover:text-white px-3.5 py-1.5 rounded-full transition-colors";

export default function NavBar() {
  const { logout } = useAuth();
  const veil = useVeil();
  const nav = useNavigate();
  const handleLogout = async () => {
    // Leave the same way we arrived: behind the veil, back to the scenery.
    await veil.cover();
    logout();
    nav("/");
    veil.liftSoon();
  };

  return (
    <nav
      className="flex items-center justify-between px-5 sm:px-8 py-4 sticky top-0 z-10"
      style={{
        background: "linear-gradient(rgba(9,9,18,0.85), rgba(9,9,18,0.65))",
        backdropFilter: "blur(18px)",
        WebkitBackdropFilter: "blur(18px)",
        borderBottom: "1px solid rgba(255,255,255,0.06)",
      }}
    >
      <Link to="/" className="hero-serif italic text-white text-xl">
        ForgeAI
      </Link>

      <div className="flex items-center gap-2 sm:gap-3">
        <div className="hidden sm:flex items-center gap-0.5 liquid-glass rounded-full px-1.5 py-1">
          <NavLink to="/dashboard" className={linkCls}>Dashboard</NavLink>
          <NavLink to="/settings" className={linkCls}>Deploy Keys</NavLink>
        </div>
        {/* Mobile: plain links, no pill */}
        <div className="flex sm:hidden items-center gap-1">
          <NavLink to="/dashboard" className={linkCls}>Dashboard</NavLink>
          <NavLink to="/settings" className={linkCls}>Keys</NavLink>
        </div>

        <NavLink
          to="/new"
          className="flex items-center gap-1.5 text-sm font-medium bg-white text-slate-900 pl-3 pr-4 py-2 rounded-full hover:bg-white/90 transition-colors"
        >
          <Plus size={15} aria-hidden="true" /> New App
        </NavLink>
        <button
          onClick={handleLogout}
          aria-label="Log out"
          title="Log out"
          className="p-2.5 rounded-full text-white/50 hover:text-white hover:bg-white/10 transition-colors"
        >
          <LogOut size={16} aria-hidden="true" />
        </button>
      </div>
    </nav>
  );
}
