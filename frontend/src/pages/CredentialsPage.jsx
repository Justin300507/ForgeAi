import React, { useState, useEffect, useCallback } from "react";
import { Github, Cloud, Train, Check, Triangle, Database } from "lucide-react";
import NavBar from "../components/NavBar";
import GlassInput from "../components/GlassInput";
import PillButton from "../components/PillButton";
import { credentialsAPI } from "../api";

const SERVICES = [
  {
    key: "github",
    name: "GitHub",
    Icon: Github,
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
    Icon: Cloud,
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
    Icon: Train,
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
  {
    key: "vercel",
    name: "Vercel",
    Icon: Triangle,
    description: "Deploy full-stack apps (frontend + backend, one project)",
    fields: [
      {
        key: "vercel_token",
        label: "Access Token",
        placeholder: "...",
        hint: "vercel.com → Account Settings → Tokens → Create Token",
      },
    ],
  },
  {
    key: "neon",
    name: "Neon",
    Icon: Database,
    description: "Provisions a free Postgres database for each Vercel deploy",
    fields: [
      {
        key: "neon_api_key",
        label: "API Key",
        placeholder: "...",
        hint: "console.neon.tech → Account Settings → API keys → Create key",
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
      className="glass-panel rounded-xl p-5"
      style={{
        borderColor: connected ? "rgba(52,211,153,0.25)" : undefined,
      }}
    >
      {/* Header */}
      <div className="flex items-center gap-3">
        <span className="w-10 h-10 rounded-full liquid-glass flex items-center justify-center shrink-0">
          <service.Icon size={18} className="text-gray-300" aria-hidden="true" />
        </span>
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
          <PillButton variant="danger" size="xs" onClick={handleDisconnect} className="shrink-0">
            Disconnect
          </PillButton>
        ) : (
          <button
            onClick={() => setExpanded((v) => !v)}
            className="text-xs font-medium text-[#ffb47a] hover:text-[#ffcf9e] border border-[#ff8a3d]/30 hover:border-[#ffa96b]/50 px-3 py-1.5 rounded-lg transition-all shrink-0"
          >
            {expanded ? "Cancel" : "Connect"}
          </button>
        )}
      </div>

      {/* Connected account info */}
      {connected && (
        <div className="mt-3 flex items-center gap-2 text-xs text-emerald-400/80">
          <Check size={12} aria-hidden="true" />
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
              <GlassInput
                type="password"
                placeholder={field.placeholder}
                defaultValue={credentials?.[field.key] || ""}
                onChange={(e) => set(field.key, e.target.value)}
                autoComplete="off"
              />
            </div>
          ))}

          {err && (
            <p className="text-xs text-red-400">{err}</p>
          )}

          <button
            onClick={handleSave}
            disabled={saving}
            className="w-full py-2 rounded-lg text-sm font-semibold text-slate-900 transition-all disabled:opacity-40 hover:brightness-110"
            style={{ background: "var(--ember)" }}
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
    <div className="app-shell">
      <NavBar />
      <div className="max-w-2xl mx-auto px-6 py-10">
        <h1 className="anim-fade-up hero-serif text-3xl text-white mb-1">Deploy Accounts</h1>
        <p className="anim-fade-up text-gray-500 text-sm mb-8" style={{ "--d": "60ms" }}>
          Connect your accounts once — ForgeAI uses them automatically when you deploy.
        </p>

        {loading ? (
          <div className="space-y-4" aria-label="Loading accounts">
            {[0, 1, 2].map((i) => (
              <div key={i} className="skeleton h-20 rounded-xl" />
            ))}
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

        <div className="anim-fade-up mt-8 glass-panel rounded-xl p-5" style={{ "--d": "120ms" }}>
          <h3 className="hero-serif text-lg text-white mb-3">How one-click deployment works</h3>
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
