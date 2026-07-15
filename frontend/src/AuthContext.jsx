import React, { createContext, useContext, useState, useEffect } from "react";
import { authAPI } from "./api";

const AuthContext = createContext(null);

// Survives logout (unlike the token): once someone has signed in on this
// device, the landing page may offer "Open Dashboard" instead of onboarding.
const RETURNING_KEY = "forge_returning";
export const hasLoggedInBefore = () =>
  localStorage.getItem(RETURNING_KEY) === "1";

export function AuthProvider({ children }) {
  const [user, setUser] = useState(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const token = localStorage.getItem("token");
    if (!token) { setLoading(false); return; }
    authAPI.me().then(r => setUser(r.data)).catch(() => localStorage.removeItem("token")).finally(() => setLoading(false));
  }, []);

  const login = async (email, password) => {
    const res = await authAPI.login(email, password);
    localStorage.setItem("token", res.data.access_token);
    localStorage.setItem(RETURNING_KEY, "1");
    const me = await authAPI.me();
    setUser(me.data);
  };

  const register = async (email, password) => {
    await authAPI.register(email, password);
    await login(email, password);
  };

  const logout = () => { localStorage.removeItem("token"); setUser(null); };

  return <AuthContext.Provider value={{ user, loading, login, register, logout }}>{children}</AuthContext.Provider>;
}

export const useAuth = () => useContext(AuthContext);
