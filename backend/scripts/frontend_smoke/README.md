# Frontend smoke tests

Ad hoc Playwright scripts that verify frontend behavior against a built
`vite preview` server. No `npm test` is wired up in this repo — these are
it. All scripts mock the backend API via `page.route()`, so they need no
running backend, no `.env`, and cost zero LLM credits.

## Running

```bash
cd frontend
npm run build
npx vite preview --port 4173 --strictPort &   # background it
cd ../backend
./venv/Scripts/python scripts/frontend_smoke/smoke_landing.py
./venv/Scripts/python scripts/frontend_smoke/smoke_workspace.py
./venv/Scripts/python scripts/frontend_smoke/smoke_internal_veil.py
./venv/Scripts/python scripts/frontend_smoke/smoke_scenery.py
./venv/Scripts/python scripts/frontend_smoke/smoke_dashboard_redesign.py
```

Kill the preview server when done (`netstat -ano | grep :4173` on Windows to find the PID).

## Convention

Every script prints `PASS <name>` / `FAIL <name>` per assertion and exits 1
if any failed. Any check tied to a CSS transition's opacity uses
`page.wait_for_function(...)` (in-browser polling), never a fixed
`sleep()` then `evaluate()` — that pattern produced false negatives against
a transition that was actually behaving correctly (see git history on the
veil transition, 2026-07-10).
