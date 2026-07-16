"""
Neon Postgres provisioning — gives every Vercel-deployed generated app a
real, persistent, free-forever database.

Vercel serverless functions have no disk at all (unlike Render's free tier,
which at least runs SQLite on ephemeral local disk between restarts), so a
generated app deployed there MUST have DATABASE_URL point at a real external
Postgres from the moment it goes live. Render's own free Postgres also
isn't a fit here: only one is allowed per account, and it expires after 30
days — Neon's free tier allows many projects per account with no expiry.

Requires:
  - NEON_API_KEY in backend/.env (console.neon.tech → Account Settings → API keys)

One Neon *project* per generated app (Neon's free tier allows many projects
per account — unlike Render's one-database cap), named after the app slug.
"""
import os
import re

import requests

NEON_API = "https://console.neon.tech/api/v2"


def _slug(name: str) -> str:
    slug = re.sub(r"[^a-z0-9-]", "-", name.lower().replace("_", "-")).strip("-")
    return slug[:52] or "forge-app"


def _headers(api_key: str) -> dict:
    return {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json", "Accept": "application/json"}


def provision_database(project_name: str) -> tuple[str | None, str | None]:
    """
    Create a fresh Neon project (= an isolated Postgres instance) for a
    generated app and return its connection string.

    Returns (database_url, error) — exactly one is non-None.
    """
    api_key = os.getenv("NEON_API_KEY")
    if not api_key:
        return None, "NEON_API_KEY not set in .env — get it from console.neon.tech → Account Settings → API keys"

    slug = _slug(project_name)
    try:
        resp = requests.post(
            f"{NEON_API}/projects",
            headers=_headers(api_key),
            json={"project": {"name": slug}},
            timeout=30,
        )
    except Exception as exc:
        return None, f"Neon API request failed: {exc}"

    if not resp.ok:
        return None, f"Neon project creation failed: {resp.status_code} {resp.text[:300]}"

    data = resp.json()
    uris = data.get("connection_uris") or []
    if not uris:
        return None, "Neon project created but no connection_uris returned"

    db_url = uris[0].get("connection_uri")
    if not db_url:
        return None, "Neon connection_uris entry missing connection_uri"

    print(f"  [Neon] Provisioned database for '{slug}': {data.get('project', {}).get('id')}")
    return db_url, None


def delete_database(project_id: str) -> bool:
    api_key = os.getenv("NEON_API_KEY")
    if not api_key or not project_id:
        return False
    resp = requests.delete(f"{NEON_API}/projects/{project_id}", headers=_headers(api_key), timeout=30)
    return resp.ok
