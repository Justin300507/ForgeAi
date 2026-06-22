"""
Static Vite + React + Tailwind template files written into every generated project.
These are never LLM-generated — they are fixed scaffolding so npm run build works.

Web target:  Tailwind + lucide-react + recharts — polished modern UI
PWA target:  same + manifest.json + service worker + install prompt
"""

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
    "clsx": "^2.1.0"
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

export default defineConfig({
  plugins: [react()],
  build: {
    outDir: 'dist',
    emptyOutDir: true,
  },
})
"""

TAILWIND_CONFIG_JS = """/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{js,jsx,ts,tsx}'],
  darkMode: 'class',
  theme: {
    extend: {
      colors: {
        primary: {
          50:  '#eef2ff',
          100: '#e0e7ff',
          500: '#6366f1',
          600: '#4f46e5',
          700: '#4338ca',
          900: '#312e81',
        },
      },
      fontFamily: {
        sans: ['Inter', 'system-ui', 'sans-serif'],
      },
    },
  },
  plugins: [],
}
"""

POSTCSS_CONFIG_JS = """export default {
  plugins: {
    tailwindcss: {},
    autoprefixer: {},
  },
}
"""

INDEX_HTML = """<!DOCTYPE html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <meta name="theme-color" content="#4f46e5" />
    <title>ForgeAI App</title>
    <link rel="preconnect" href="https://fonts.googleapis.com" />
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin />
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet" />
  </head>
  <body>
    <div id="root"></div>
    <script type="module" src="/src/main.jsx"></script>
  </body>
</html>
"""

INDEX_CSS = """@tailwind base;
@tailwind components;
@tailwind utilities;

@layer base {
  :root {
    --bg: #f8fafc;
    --surface: #ffffff;
    --text: #0f172a;
    --muted: #64748b;
    --border: #e2e8f0;
    --accent: #4f46e5;
  }

  .dark {
    --bg: #0f172a;
    --surface: #1e293b;
    --text: #f1f5f9;
    --muted: #94a3b8;
    --border: #334155;
    --accent: #818cf8;
  }

  * { box-sizing: border-box; }
  body {
    background-color: var(--bg);
    color: var(--text);
    font-family: 'Inter', system-ui, sans-serif;
    margin: 0;
  }
}

@layer components {
  .card {
    @apply bg-white dark:bg-slate-800 rounded-xl shadow-sm border border-slate-100 dark:border-slate-700;
  }
  .btn-primary {
    @apply bg-indigo-600 hover:bg-indigo-700 text-white font-medium px-4 py-2 rounded-lg transition-colors duration-150 cursor-pointer;
  }
  .btn-secondary {
    @apply bg-slate-100 hover:bg-slate-200 dark:bg-slate-700 dark:hover:bg-slate-600 text-slate-700 dark:text-slate-200 font-medium px-4 py-2 rounded-lg transition-colors duration-150 cursor-pointer;
  }
  .input {
    @apply w-full border border-slate-200 dark:border-slate-600 bg-white dark:bg-slate-800 text-slate-900 dark:text-slate-100 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500;
  }
  .badge {
    @apply inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium;
  }
  .stat-card {
    @apply card p-5;
  }
  .nav-link {
    @apply flex items-center gap-2.5 px-3 py-2 rounded-lg text-sm font-medium transition-colors duration-150;
  }
  .nav-link-active {
    @apply bg-indigo-50 dark:bg-indigo-900/40 text-indigo-700 dark:text-indigo-300;
  }
  .nav-link-idle {
    @apply text-slate-600 dark:text-slate-400 hover:bg-slate-100 dark:hover:bg-slate-700 hover:text-slate-900 dark:hover:text-slate-100;
  }
}
"""

MAIN_JSX = """import React from 'react'
import ReactDOM from 'react-dom/client'
import App from './App'
import './index.css'

ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>
)
"""

# ── PWA extras ───────────────────────────────────────────────────────────────

PWA_INDEX_HTML = """<!DOCTYPE html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <meta name="theme-color" content="#4f46e5" />
    <meta name="mobile-web-app-capable" content="yes" />
    <meta name="apple-mobile-web-app-capable" content="yes" />
    <meta name="apple-mobile-web-app-status-bar-style" content="black-translucent" />
    <meta name="apple-mobile-web-app-title" content="ForgeApp" />
    <link rel="manifest" href="/manifest.json" />
    <link rel="apple-touch-icon" href="/icon-192.svg" />
    <link rel="icon" type="image/svg+xml" href="/icon-192.svg" />
    <title>ForgeAI App</title>
    <link rel="preconnect" href="https://fonts.googleapis.com" />
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin />
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet" />
  </head>
  <body>
    <div id="root"></div>
    <script type="module" src="/src/main.jsx"></script>
    <script>
      if ('serviceWorker' in navigator) {
        window.addEventListener('load', () => {
          navigator.serviceWorker.register('/sw.js').catch(() => {});
        });
      }
    </script>
  </body>
</html>
"""

PWA_MANIFEST_JSON = """{
  "name": "ForgeAI App",
  "short_name": "ForgeApp",
  "description": "Generated by ForgeAI",
  "start_url": "/",
  "display": "standalone",
  "background_color": "#0f172a",
  "theme_color": "#4f46e5",
  "orientation": "portrait-primary",
  "icons": [
    { "src": "/icon-192.svg", "sizes": "any", "type": "image/svg+xml", "purpose": "any maskable" }
  ],
  "categories": ["productivity", "utilities"]
}
"""

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
