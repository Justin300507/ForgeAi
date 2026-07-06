"""
LLM Judge — interprets screenshots and failure context with vision AI.

Instead of "element not found", the judge returns:
  "The submit button exists but is covered by a loading overlay.
   The form POST succeeds (200) but the UI doesn't clear the overlay on response."

Requires a vision-capable model (Gemini or OpenRouter with vision).
Gracefully degrades to text-only analysis if no vision model is available.

Usage:
    judgment = judge_screenshot(screenshot_b64, console_errors, api_failures, app_idea)
    # → JudgmentResult(assessment="...", severity="high", fix_hint="...", confidence=0.85)
"""
from __future__ import annotations

import base64
import re
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class JudgmentResult:
    assessment:  str             # human-readable interpretation
    severity:    str             # "critical" | "high" | "medium" | "low"
    fix_hint:    str             # concrete suggestion for the fixer
    confidence:  float           # 0.0–1.0 how confident the judge is
    screenshot_available: bool = False
    model_used:  str = ""
    raw_output:  str = ""


_VISION_PROVIDERS = ["gemini", "openrouter"]  # prefer these for vision tasks


def _build_vision_prompt(
    console_errors: list[str],
    network_failures: list[str],
    workflow_failures: list[str],
    app_idea: str,
    has_screenshot: bool,
) -> str:
    errors_section = ""
    if console_errors:
        errors_section += "BROWSER CONSOLE ERRORS:\n"
        errors_section += "\n".join(f"  - {e}" for e in console_errors[:10])
        errors_section += "\n\n"
    if network_failures:
        errors_section += "NETWORK FAILURES (API calls that failed):\n"
        errors_section += "\n".join(f"  - {e}" for e in network_failures[:10])
        errors_section += "\n\n"
    if workflow_failures:
        errors_section += "USER JOURNEY FAILURES:\n"
        errors_section += "\n".join(f"  - {e}" for e in workflow_failures[:5])
        errors_section += "\n\n"

    screenshot_note = (
        "The attached screenshot shows the current state of the running app.\n"
        if has_screenshot else
        "No screenshot available — base your analysis on the error context only.\n"
    )

    return f"""You are a senior QA engineer reviewing a generated web application.
The app is supposed to: {app_idea[:200]}

{screenshot_note}
{errors_section}
Analyze what is visually and functionally wrong with this app.

Be specific and actionable. Focus on:
1. What the user would actually see (blank page? broken form? missing content?)
2. The most likely root cause (is it a JS error? CORS? wrong API URL? missing state?)
3. A concrete fix for the developer

Respond in this EXACT JSON format (no markdown fences):
{{
  "assessment": "One detailed paragraph describing what is wrong and why",
  "severity": "critical|high|medium|low",
  "fix_hint": "Specific code/config change to fix it",
  "confidence": 0.0-1.0
}}"""


def judge_screenshot(
    screenshot_b64: Optional[str],
    console_errors:   list[str],
    network_failures: list[str],
    workflow_failures: list[str],
    app_idea:         str,
    provider:         str = "auto",
) -> JudgmentResult:
    """
    Call a vision LLM to interpret screenshot + error context.
    Falls back to text-only if no screenshot or vision model unavailable.
    """
    has_screenshot = bool(screenshot_b64 and len(screenshot_b64) > 100)

    # Skip if there's nothing to judge
    if not console_errors and not network_failures and not workflow_failures and not has_screenshot:
        return JudgmentResult(
            assessment="No errors or screenshot to analyze",
            severity="info",
            fix_hint="",
            confidence=1.0,
        )

    prompt = _build_vision_prompt(
        console_errors, network_failures, workflow_failures, app_idea, has_screenshot
    )

    # Try to call with screenshot attached if we have one
    try:
        raw = _call_llm_with_optional_image(prompt, screenshot_b64 if has_screenshot else None, provider)
    except Exception as exc:
        return JudgmentResult(
            assessment=f"LLM judge unavailable: {exc}",
            severity="info",
            fix_hint="",
            confidence=0.0,
            model_used="none",
        )

    # Parse response
    try:
        from app.utils.json_cleaner import extract_json
        parsed = extract_json(raw)
        return JudgmentResult(
            assessment=parsed.get("assessment", raw[:300]),
            severity=parsed.get("severity", "medium"),
            fix_hint=parsed.get("fix_hint", ""),
            confidence=float(parsed.get("confidence", 0.7)),
            screenshot_available=has_screenshot,
            raw_output=raw[:500],
        )
    except Exception:
        # Fallback: try to extract useful text
        assessment = raw[:500].strip() if raw else "Could not parse LLM judgment"
        return JudgmentResult(
            assessment=assessment,
            severity="medium",
            fix_hint="",
            confidence=0.4,
            screenshot_available=has_screenshot,
            raw_output=raw[:200],
        )


def _call_llm_with_optional_image(
    prompt: str,
    image_b64: Optional[str],
    provider: str,
) -> str:
    """
    Call an LLM with an optional image attachment.
    Tries Gemini first (best vision support), then OpenRouter, then text-only.
    """
    # Try Gemini vision (best image support)
    if image_b64 and _has_api_key("GEMINI_API_KEY"):
        try:
            return _call_gemini_vision(prompt, image_b64)
        except Exception:
            pass

    # Try OpenRouter vision (many multimodal models)
    if image_b64 and _has_api_key("OPENROUTER_API_KEY"):
        try:
            return _call_openrouter_vision(prompt, image_b64)
        except Exception:
            pass

    # Fallback: text-only via existing provider system
    from app.providers.ai_provider import generate_content
    return generate_content(prompt, provider=provider, max_tokens=1000, stage="llm_judge")


def _has_api_key(key: str) -> bool:
    import os
    return bool(os.getenv(key))


def _call_gemini_vision(prompt: str, image_b64: str) -> str:
    """Call Gemini with image + text prompt."""
    import os
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))
    image_bytes = base64.b64decode(image_b64)

    response = client.models.generate_content(
        model="gemini-2.0-flash",
        contents=[
            types.Part.from_bytes(data=image_bytes, mime_type="image/png"),
            types.Part.from_text(text=prompt),
        ],
    )
    return response.text or ""


def _call_openrouter_vision(prompt: str, image_b64: str) -> str:
    """Call OpenRouter with a vision-capable model."""
    import os
    import httpx

    headers = {
        "Authorization": f"Bearer {os.getenv('OPENROUTER_API_KEY')}",
        "Content-Type": "application/json",
    }
    body = {
        "model": "google/gemini-2.0-flash-thinking-exp:free",
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{image_b64}"}},
                    {"type": "text", "text": prompt},
                ],
            }
        ],
        "max_tokens": 1000,
    }
    r = httpx.post("https://openrouter.ai/api/v1/chat/completions", headers=headers, json=body, timeout=30)
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"] or ""
