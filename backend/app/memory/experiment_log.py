"""
Parses experiments.md into structured entries -- $0, no new tracking
system, just reads the log every reliability cycle already writes to.
Used by the Observatory cockpit endpoint; deliberately read-only and
lossy (title + first paragraph only) rather than a full markdown parse,
since the cockpit just needs "what happened recently," not the full
writeup -- experiments.md itself is still the source of truth for detail.
"""
from __future__ import annotations

import re
from pathlib import Path

_HEADING_RE = re.compile(r"^## Experiment (\d+) — (.+)$", re.MULTILINE)


def parse_recent_experiments(experiments_md_path: Path, limit: int = 8) -> list[dict]:
    if not experiments_md_path.exists():
        return []
    try:
        text = experiments_md_path.read_text(encoding="utf-8")
    except Exception:
        return []

    matches = list(_HEADING_RE.finditer(text))
    entries = []
    for i, m in enumerate(matches):
        number, title = m.group(1), m.group(2).strip()
        body_start = m.end()
        body_end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        body = text[body_start:body_end].strip()
        # First non-empty paragraph, markdown bold/backtick markers stripped
        # for plain display.
        first_para = next((p.strip() for p in body.split("\n\n") if p.strip()), "")
        first_para = re.sub(r"[*`_]", "", first_para)
        first_para = re.sub(r"\s+", " ", first_para)
        cost_free = "$0" in title or "no LLM calls" in title or "no generation" in first_para[:200]
        entries.append({
            "number": number,
            "title": title,
            "summary": first_para[:320] + ("…" if len(first_para) > 320 else ""),
            "cost_free": cost_free,
        })
    entries.reverse()  # newest first
    return entries[:limit]
