import React, { useState, useEffect } from "react";
import NavBar from "../components/NavBar";
import { credentialsAPI } from "../api";

const FIELDS = [
  {
    key: "github_token",
    label: "GitHub Token",
    placeholder: "ghp_...",
    hint: "Personal Access Token with repo scope — github.com → Settings → Developer settings → Tokens",
  },
  {
    key: "render_api_key",
    label: "Render API Key",
    placeholder: "rnd_...",
    hint: "dashboard.render.com → Account Settings → API Keys",
  },
  {
    key: "render_owner_id",
    label: "Render Owner ID",
    placeholder: "usr-...",
    hint: "Your Render user or team ID (shown in Account Settings)",
  },
  {
    key: "cloudflare_api_token",
    label: "Cloudflare API Token",
    placeholder: "...",
    hint: "dash.cloudflare.com → Profile → API Tokens → Create Token",
  },
  {
    key: "cloudflare_account_id",
    label: "Cloudflare Account ID",
    placeholder: "...",
    hint: "Found in the Cloudflare dashboard sidebar (right side)",
  },
];

function CheckIcon() {
  return (
    <svg className="w-3.5 h-3.5" fill="currentColor" viewBox="0 0 20 20">
      <path fillRule="evenodd" d="M16.707 5.293a1 1 0 010 1.414l-8 8a1 1 0 01-1.414 0l-4-4a1 1 0 011.414-1.414L8 12.586l7.293-7.293a1 1 0 011.414 0z" clipRule="evenodd" />
    </svg>
  );
}

export default function CredentialsPage() {
  const [values, setValues] = useState({});
  const [savedKeys, setSavedKeys] = useState(new Set());
  const [saving, setSaving] = useState(false);
  const [success, setSuccess] = useState(false);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    credentialsAPI.get()
      .then(res => {
        const data = res.data || {};
        setValues(data);
        setSavedKeys(new Set(Object.keys(data).filter(k => !!data[k])));
      })
      .catch(() => {})
      .finally(() => setLoading(false));
  }, []);

  const save = async (e) => {
    e.preventDefault();
    setSaving(true); setError(""); setSuccess(false);
    try {
      await credentialsAPI.save(values);
      setSavedKeys(new Set(Object.keys(values).filter(k => !!values[k])));
      setSuccess(true);
      setTimeout(() => setSuccess(false), 3000);
    } catch (err) {
      setError(err.response?.data?.detail || "Failed to save credentials");
    } finally {
      setSaving(false);
    }
  };

  const set = (key, val) => setValues(prev => ({ ...prev, [key]: val }));

  return (
    <div className="min-h-screen" style={{ background: "#090912" }}>
      <NavBar />
      <div className="max-w-2xl mx-auto px-6 py-10">
        <h1 className="text-2xl font-bold text-white mb-1">Deploy Keys</h1>
        <p className="text-gray-500 text-sm mb-8">
          Your credentials are stored per-account and used automatically when you generate apps with deployment enabled.
        </p>

        {loading ? (
          <div className="flex justify-center py-16">
            <svg className="animate-spin h-6 w-6 text-gray-600" fill="none" viewBox="0 0 24 24">
              <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
              <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
            </svg>
          </div>
        ) : (
          <form onSubmit={save} className="space-y-4">
            {error && (
              <div className="text-red-400 text-sm bg-red-500/10 border border-red-500/20 rounded-xl px-4 py-3">
                {error}
              </div>
            )}

            {FIELDS.map(field => (
              <div key={field.key} className="rounded-xl border border-white/8 p-5" style={{ background: "#12121f" }}>
                <div className="flex items-center justify-between mb-1">
                  <label className="text-sm font-medium text-gray-300">{field.label}</label>
                  {savedKeys.has(field.key) && (
                    <span className="text-xs text-emerald-400 flex items-center gap-1">
                      <CheckIcon /> Saved
                    </span>
                  )}
                </div>
                <p className="text-xs text-gray-600 mb-3">{field.hint}</p>
                <input
                  type="password"
                  value={values[field.key] || ""}
                  onChange={e => set(field.key, e.target.value)}
                  placeholder={field.placeholder}
                  autoComplete="off"
                  className="w-full rounded-lg px-3.5 py-2.5 text-sm text-white placeholder:text-gray-700 focus:outline-none border border-white/10 focus:border-violet-500/50 transition-colors"
                  style={{ background: "#090912" }}
                />
              </div>
            ))}

            <button
              type="submit"
              disabled={saving}
              className="w-full font-semibold text-white py-3 rounded-xl text-sm transition-all disabled:opacity-40"
              style={{ background: success ? "#059669" : "#7c3aed" }}
            >
              {saving ? "Saving…" : success ? "Saved!" : "Save Credentials"}
            </button>
          </form>
        )}

        <div className="mt-8 rounded-xl border border-white/5 p-5" style={{ background: "#12121f" }}>
          <h3 className="text-sm font-semibold text-gray-300 mb-3">How one-click deployment works</h3>
          <ol className="text-xs text-gray-500 space-y-2 list-decimal list-inside leading-relaxed">
            <li>ForgeAI generates your full-stack app (FastAPI backend + React frontend)</li>
            <li>Pushes the code to a new GitHub repo under your account</li>
            <li>Deploys the backend to Render using your Render API key</li>
            <li>Deploys the frontend to Cloudflare Pages using your Cloudflare token</li>
            <li>Returns live URLs — your app is live in ~5 minutes</li>
          </ol>
        </div>
      </div>
    </div>
  );
}
