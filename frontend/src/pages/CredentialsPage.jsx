import React, { useState, useEffect, useCallback } from "react";
import NavBar from "../components/NavBar";
import { credentialsAPI } from "../api";

const SERVICES = [
  {
    key: "github",
    name: "GitHub",
    icon: "🐙",
    description: "Push generated repos to your GitHub account",
    fields: [
      {
        key: "github_token",
        label: "Personal Access Token",
        placeholder: "ghp_...",
        hint: "Settings → Developer settings → Personal access tokens → New token (repo scope)",
      },
    ],
  },
  {
    key: "cloudflare",
    name: "Cloudflare",
    icon: "☁️",
    description: "Deploy frontend to Cloudflare Pages",
    fields: [
      {
        key: "cloudflare_api_token",
        label: "API Token",
        placeholder: "...",
        hint: "Profile → API Tokens → Create Token (Pages:Edit permission)",
      },
      {
        key: "cloudflare_account_id",
        label: "Account ID",
        placeholder: "...",
        hint: "Cloudflare dashboard sidebar → right-hand panel → Account ID",
      },
    ],
  },
  {
    key: "railway",
    name: "Railway",
    icon: "🚂",
    description: "Deploy backend to Railway",
    fields: [
      {
        key: "railway_token",
        label: "Personal Token",
        placeholder: "...",
        hint: "railway.app → Account → Tokens → Create Token",
      },
    ],
  },
];

function ServiceCard({ service, status, credentials, onSave, onDisconnect }) {
  const [expanded, setExpanded] = useState(false);
  const [values, setValues] = useState({});
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState("");

  const connected = status?.connected === true;
  const accountLabel =
    status?.login || status?.name || status?.email || "Connected";

  const set = (key, val) => setValues((prev) => ({ ...prev, [key]: val }));

  const handleSave = async () => {
    setSaving(true);
    setErr("");
    try {
      const merged = { ...credentials };
      service.fields.forEach((f) => {
        if (values[f.key] !== undefined) merged[f.key] = values[f.key];
      });
      await onSave(merged);
      setExpanded(false);
      setValues({});
    } catch (e) {
      setErr(e?.response?.data?.detail || "Failed to save");
    } finally {
      setSaving(false);
    }
  };

  const handleDisconnect = async () => {
    const cleared = { ...credentials };
    service.fields.forEach((f) => { cleared[f.key] = ""; });
    await onSave(cleared);
  };

  return (
    <div
      className="rounded-xl border p-5 transition-colors"
      style={{
        background: "#12121f",
        borderColor: connected ? "rgba(52,211,153,0.25)" : "rgba(255,255,255,0.08)",
      }}
    >
      {/* Header */}
      <div className="flex items-center gap-3">
        <span className="text-2xl">{service.icon}</span>
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2">
            <span className="text-sm font-semibold text-white">{service.name}</span>
            {connected && (
              <span className="text-xs text-emerald-400 bg-emerald-400/10 px-2 py-0.5 rounded-full">
                Connected
              </span>
            )}
          </div>
          <p className="text-xs text-gray-500 mt-0.5">{service.description}</p>
        </div>

        {connected ? (
          <button
            onClick={handleDisconnect}
            className="text-xs text-gray-500 hover:text-red-400 transition-colors shrink-0"
          >
            Disconnect
          </button>
        ) : (
          <button
            onClick={() => setExpanded((v) => !v)}
            className="text-xs font-medium text-violet-400 hover:text-violet-300 border border-violet-500/30 hover:border-violet-400/50 px-3 py-1.5 rounded-lg transition-all shrink-0"
          >
            {expanded ? "Cancel" : "Connect"}
          </button>
        )}
      </div>

      {/* Connected account info */}
      {connected && (
        <div className="mt-3 flex items-center gap-2 text-xs text-emerald-400/80">
          <span>✓</span>
          <span>{accountLabel}</span>
          {!expanded && (
            <button
              onClick={() => setExpanded((v) => !v)}
              className="ml-auto text-gray-600 hover:text-gray-400 transition-colors"
            >
              Update token
            </button>
          )}
        </div>
      )}

      {/* Expand form */}
      {expanded && (
        <div className="mt-4 space-y-3">
          {service.fields.map((field) => (
            <div key={field.key}>
              <label className="block text-xs font-medium text-gray-400 mb-1">
                {field.label}
              </label>
              <p className="text-xs text-gray-600 mb-1.5">{field.hint}</p>
              <input
                type="password"
                placeholder={field.placeholder}
                defaultValue={credentials?.[field.key] || ""}
                onChange={(e) => set(field.key, e.target.value)}
                autoComplete="off"
                className="w-full rounded-lg px-3 py-2 text-sm text-white placeholder:text-gray-700 border border-white/10 focus:border-violet-500/50 focus:outline-none transition-colors"
                style={{ background: "#090912" }}
              />
            </div>
          ))}

          {err && (
            <p className="text-xs text-red-400">{err}</p>
          )}

          <button
            onClick={handleSave}
            disabled={saving}
            className="w-full py-2 rounded-lg text-sm font-semibold text-white transition-all disabled:opacity-40"
            style={{ background: "#7c3aed" }}
          >
            {saving ? "Saving…" : "Save & Connect"}
          </button>
        </div>
      )}
    </div>
  );
}

export default function CredentialsPage() {
  const [credentials, setCredentials] = useState({});
  const [status, setStatus] = useState({});
  const [loading, setLoading] = useState(true);

  const refresh = useCallback(async () => {
    const [credsRes, statusRes] = await Promise.allSettled([
      credentialsAPI.get(),
      credentialsAPI.status(),
    ]);
    if (credsRes.status === "fulfilled") setCredentials(credsRes.value.data || {});
    if (statusRes.status === "fulfilled") setStatus(statusRes.value.data || {});
    setLoading(false);
  }, []);

  useEffect(() => { refresh(); }, [refresh]);

  const handleSave = async (merged) => {
    await credentialsAPI.save(merged);
    await refresh();
  };

  return (
    <div className="min-h-screen" style={{ background: "#090912" }}>
      <NavBar />
      <div className="max-w-2xl mx-auto px-6 py-10">
        <h1 className="text-2xl font-bold text-white mb-1">Deploy Accounts</h1>
        <p className="text-gray-500 text-sm mb-8">
          Connect your accounts once — ForgeAI uses them automatically when you deploy.
        </p>

        {loading ? (
          <div className="flex justify-center py-16">
            <svg className="animate-spin h-6 w-6 text-gray-600" fill="none" viewBox="0 0 24 24">
              <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
              <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
            </svg>
          </div>
        ) : (
          <div className="space-y-4">
            {SERVICES.map((svc) => (
              <ServiceCard
                key={svc.key}
                service={svc}
                status={status[svc.key]}
                credentials={credentials}
                onSave={handleSave}
                onDisconnect={async () => {
                  const cleared = { ...credentials };
                  svc.fields.forEach((f) => { cleared[f.key] = ""; });
                  await handleSave(cleared);
                }}
              />
            ))}
          </div>
        )}

        <div className="mt-8 rounded-xl border border-white/5 p-5" style={{ background: "#12121f" }}>
          <h3 className="text-sm font-semibold text-gray-300 mb-3">How one-click deployment works</h3>
          <ol className="text-xs text-gray-500 space-y-2 list-decimal list-inside leading-relaxed">
            <li>ForgeAI generates your full-stack app (FastAPI backend + React frontend)</li>
            <li>Pushes the code to a new GitHub repo under your account</li>
            <li>Deploys the backend to Railway using your personal token</li>
            <li>Deploys the frontend to Cloudflare Pages using your API token</li>
            <li>Returns live URLs — your app is live in ~5 minutes</li>
          </ol>
        </div>
      </div>
    </div>
  );
}
