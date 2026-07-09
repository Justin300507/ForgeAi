"""
Static Vite + React + Tailwind template files written into every generated project.
These are never LLM-generated — they are fixed scaffolding so npm run build works.

Web target:  Tailwind + lucide-react + recharts — polished modern UI
PWA target:  same + manifest.json + service worker + install prompt

index.css / tailwind.config.js / index.html (+ PWA variants) are rendered from
app/templates/theme_builder.py master templates: the constants below are the
DEFAULT_THEME (indigo/Inter) rendering used as the safe fallback, and
file_writer_service overlays per-app themed renderings of the same templates
(category brand color, style-pack font pairing, app title) at write time.
"""

from app.templates.theme_builder import (
    DEFAULT_THEME,
    INDEX_CSS_TEMPLATE,
    INDEX_HTML_TEMPLATE,
    PWA_INDEX_HTML_TEMPLATE,
    PWA_MANIFEST_TEMPLATE,
    TAILWIND_CONFIG_TEMPLATE,
    _render,
)

PACKAGE_JSON = """{
  "name": "forge-frontend",
  "private": true,
  "version": "0.0.1",
  "type": "module",
  "scripts": {
    "dev": "vite",
    "build": "vite build",
    "preview": "vite preview"
  },
  "dependencies": {
    "react": "^18.2.0",
    "react-dom": "^18.2.0",
    "react-router-dom": "^6.22.0",
    "axios": "^1.6.8",
    "lucide-react": "^0.263.1",
    "recharts": "^2.10.3",
    "clsx": "^2.1.0",
    "react-hot-toast": "^2.4.1"
  },
  "devDependencies": {
    "@vitejs/plugin-react": "^4.2.1",
    "vite": "^5.2.0",
    "tailwindcss": "^3.4.1",
    "autoprefixer": "^10.4.17",
    "postcss": "^8.4.35"
  }
}
"""

VITE_CONFIG_JS = """import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Proxy all non-asset paths to the FastAPI backend (port 8000).
// In production set VITE_API_URL to your deployed backend URL and api.js picks it up.
export default defineConfig({
  plugins: [react()],
  build: { outDir: 'dist', emptyOutDir: true },
  server: {
    proxy: {
      '^/(?!src|@|node_modules|favicon|assets|index\\.html)': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      }
    }
  }
})
"""

TAILWIND_CONFIG_JS = _render(TAILWIND_CONFIG_TEMPLATE, DEFAULT_THEME)

POSTCSS_CONFIG_JS = """export default {
  plugins: {
    tailwindcss: {},
    autoprefixer: {},
  },
}
"""

INDEX_HTML = _render(INDEX_HTML_TEMPLATE, DEFAULT_THEME)

INDEX_CSS = _render(INDEX_CSS_TEMPLATE, DEFAULT_THEME)

MAIN_JSX = """import React from 'react'
import ReactDOM from 'react-dom/client'
import App from './App'
import ErrorBoundary from './components/ErrorBoundary'
import './index.css'

ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <ErrorBoundary>
      <App />
    </ErrorBoundary>
  </React.StrictMode>
)
"""

ERROR_BOUNDARY_JSX = """import React from 'react'

class ErrorBoundary extends React.Component {
  constructor(props) {
    super(props)
    this.state = { error: null }
  }

  static getDerivedStateFromError(error) {
    return { error }
  }

  componentDidCatch(error, info) {
    console.error('ErrorBoundary caught:', error, info)
  }

  render() {
    if (!this.state.error) return this.props.children
    return (
      <div className="min-h-screen bg-slate-50 dark:bg-slate-900 flex items-center justify-center p-4">
        <div className="w-full max-w-md text-center bg-white/80 dark:bg-slate-800/70 backdrop-blur-xl rounded-2xl border border-slate-100 dark:border-slate-700/60 ring-1 ring-black/5 dark:ring-white/5 shadow-sm p-8 animate-scale-in">
          <div className="w-12 h-12 rounded-2xl bg-gradient-to-br from-red-500 to-rose-500 mx-auto mb-4 flex items-center justify-center shadow-lg shadow-red-500/30">
            <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="white" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <path d="m21.73 18-8-14a2 2 0 0 0-3.48 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.73-3Z" />
              <path d="M12 9v4" />
              <path d="M12 17h.01" />
            </svg>
          </div>
          <h1 className="text-xl font-bold text-slate-900 dark:text-white">Something went wrong</h1>
          <p className="text-sm text-slate-500 dark:text-slate-400 mt-2 mb-6">
            An unexpected error occurred. Reloading usually fixes it.
          </p>
          <button
            onClick={() => window.location.reload()}
            className="inline-flex items-center gap-1.5 px-4 py-2 rounded-lg text-white font-medium bg-slate-900 dark:bg-slate-100 dark:text-slate-900 hover:opacity-90 active:scale-[0.97] transition-all duration-150"
          >
            Reload app
          </button>
        </div>
      </div>
    )
  }
}

export default ErrorBoundary
"""

# ── PWA extras ───────────────────────────────────────────────────────────────

PWA_INDEX_HTML = _render(PWA_INDEX_HTML_TEMPLATE, DEFAULT_THEME)

PWA_MANIFEST_JSON = _render(PWA_MANIFEST_TEMPLATE, DEFAULT_THEME)

PWA_SERVICE_WORKER_JS = """const CACHE = 'forgeai-v1';
const PRECACHE = ['/', '/index.html'];

self.addEventListener('install', e => {
  e.waitUntil(
    caches.open(CACHE).then(c => c.addAll(PRECACHE)).then(() => self.skipWaiting())
  );
});

self.addEventListener('activate', e => {
  e.waitUntil(
    caches.keys().then(keys =>
      Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k)))
    ).then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', e => {
  if (e.request.method !== 'GET') return;
  if (e.request.url.includes('/api/') || e.request.url.includes(':8000')) return;
  e.respondWith(
    caches.match(e.request).then(cached => {
      const network = fetch(e.request).then(res => {
        const clone = res.clone();
        caches.open(CACHE).then(c => c.put(e.request, clone));
        return res;
      });
      return cached || network;
    })
  );
});

// Push notifications (server-sent)
self.addEventListener('push', e => {
  const data = e.data ? e.data.json() : { title: 'ForgeApp', body: 'You have a new notification' };
  e.waitUntil(
    self.registration.showNotification(data.title || 'ForgeApp', {
      body: data.body || '',
      icon: '/icon-192.svg',
      badge: '/icon-192.svg',
      data: { url: data.url || '/' },
      actions: data.actions || [],
    })
  );
});

self.addEventListener('notificationclick', e => {
  e.notification.close();
  const url = (e.notification.data || {}).url || '/';
  e.waitUntil(
    clients.matchAll({ type: 'window' }).then(windowClients => {
      for (const client of windowClients) {
        if (client.url === url && 'focus' in client) return client.focus();
      }
      if (clients.openWindow) return clients.openWindow(url);
    })
  );
});
"""

PWA_ICON_SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 192 192">
  <rect width="192" height="192" rx="32" fill="#4f46e5"/>
  <text x="50%" y="55%" font-size="100" text-anchor="middle" dominant-baseline="middle"
        font-family="Inter,system-ui,sans-serif" font-weight="700" fill="white">F</text>
</svg>
"""

PWA_INSTALL_JSX = """import React, { useState, useEffect } from 'react';
import { Download, X } from 'lucide-react';

const InstallPrompt = () => {
  const [prompt, setPrompt] = useState(null);
  const [visible, setVisible] = useState(false);

  useEffect(() => {
    const handler = (e) => { e.preventDefault(); setPrompt(e); setVisible(true); };
    window.addEventListener('beforeinstallprompt', handler);
    return () => window.removeEventListener('beforeinstallprompt', handler);
  }, []);

  const install = async () => {
    if (!prompt) return;
    prompt.prompt();
    const { outcome } = await prompt.userChoice;
    if (outcome === 'accepted') setVisible(false);
  };

  if (!visible) return null;

  return (
    <div className="fixed bottom-4 left-4 right-4 sm:left-auto sm:right-4 sm:w-80 bg-indigo-600 text-white rounded-xl shadow-xl p-4 flex items-start gap-3 z-50 animate-in slide-in-from-bottom-4">
      <div className="flex-1">
        <p className="font-semibold text-sm">Install App</p>
        <p className="text-xs text-indigo-200 mt-0.5">Add to your home screen for the best experience.</p>
        <div className="flex gap-2 mt-3">
          <button onClick={install} className="flex items-center gap-1.5 bg-white text-indigo-700 text-xs font-semibold px-3 py-1.5 rounded-lg hover:bg-indigo-50 transition-colors">
            <Download size={13} /> Install
          </button>
          <button onClick={() => setVisible(false)} className="text-xs text-indigo-200 hover:text-white px-2 py-1.5 rounded-lg hover:bg-indigo-700 transition-colors">
            Not now
          </button>
        </div>
      </div>
      <button onClick={() => setVisible(false)} className="text-indigo-300 hover:text-white mt-0.5">
        <X size={16} />
      </button>
    </div>
  );
};

export default InstallPrompt;
"""

# ── Template file maps ────────────────────────────────────────────────────────

FRONTEND_TEMPLATE_FILES = {
    "package.json":         PACKAGE_JSON,
    "vite.config.js":       VITE_CONFIG_JS,
    "tailwind.config.js":   TAILWIND_CONFIG_JS,
    "postcss.config.js":    POSTCSS_CONFIG_JS,
    "index.html":           INDEX_HTML,
    "src/index.css":        INDEX_CSS,
    "src/main.jsx":         MAIN_JSX,
    "src/components/ErrorBoundary.jsx": ERROR_BOUNDARY_JSX,
}

PWA_NOTIFICATIONS_JS = """// ForgeAI Push Notification Utilities
// Local (scheduled) notifications — no server required

export const requestNotificationPermission = async () => {
  if (!('Notification' in window)) return 'unsupported';
  if (Notification.permission === 'granted') return 'granted';
  if (Notification.permission === 'denied') return 'denied';
  const result = await Notification.requestPermission();
  return result;
};

export const showNotification = (title, options = {}) => {
  if (Notification.permission !== 'granted') return;
  if ('serviceWorker' in navigator && navigator.serviceWorker.controller) {
    navigator.serviceWorker.ready.then(reg => {
      reg.showNotification(title, {
        icon: '/icon-192.svg',
        badge: '/icon-192.svg',
        ...options,
      });
    });
  } else {
    new Notification(title, { icon: '/icon-192.svg', ...options });
  }
};

// Schedule a recurring daily reminder at a specific local time (HH:MM)
export const scheduleDaily = (title, body, timeHHMM = '09:00') => {
  const [hh, mm] = timeHHMM.split(':').map(Number);
  const fire = () => {
    const now = new Date();
    const next = new Date(now);
    next.setHours(hh, mm, 0, 0);
    if (next <= now) next.setDate(next.getDate() + 1);
    const delay = next - now;
    return setTimeout(() => { showNotification(title, { body }); fire(); }, delay);
  };
  return fire();
};

// One-shot notification after a delay (milliseconds)
export const scheduleOnce = (title, body, delayMs) => {
  return setTimeout(() => showNotification(title, { body }), delayMs);
};

// Budget alert — call when spending exceeds threshold
export const alertBudgetExceeded = (category, spent, budget) => {
  showNotification('Budget Alert', {
    body: `${category}: $${spent.toFixed(0)} spent of $${budget} budget`,
    tag: `budget-${category}`,
  });
};

// Workout reminder
export const alertWorkoutReminder = (workoutName = 'your workout') => {
  showNotification("Time to train!", {
    body: `Don't skip ${workoutName} today. You've got this! 💪`,
    tag: 'workout-reminder',
  });
};
"""

PWA_EXTRA_FILES = {
    "index.html":                           PWA_INDEX_HTML,
    "public/manifest.json":                 PWA_MANIFEST_JSON,
    "public/sw.js":                         PWA_SERVICE_WORKER_JS,
    "public/icon-192.svg":                  PWA_ICON_SVG,
    "src/components/InstallPrompt.jsx":     PWA_INSTALL_JSX,
    "src/utils/notifications.js":           PWA_NOTIFICATIONS_JS,
}
