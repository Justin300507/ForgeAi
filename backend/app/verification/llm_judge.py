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

    If vision is unavailable (no key, dead quota, transient error) and there
    is also no error-context signal, this returns "info" rather than falling
    back to a blind text completion. A text model asked to judge "the
    attached screenshot" with zero image data and zero console/network/
    workflow errors has nothing to reason from -- it can only pattern-match
    on "AI-generated apps often have a blank-screen bug" and hallucinate a
    plausible-sounding CRITICAL verdict. That's not hypothetical: it's
    exactly what produced a false CRITICAL "blank screen" finding (confidence
    0.84) that hard-blocked deployment of an otherwise 94.1/A run with 0
    console errors, 0 network failures, and an 11/11-passing CRUD journey.
    """
    screenshot_provided = bool(screenshot_b64 and len(screenshot_b64) > 100)
    has_error_signal = bool(console_errors or network_failures or workflow_failures)

    if not has_error_signal and not screenshot_provided:
        return JudgmentResult(
            assessment="No errors or screenshot to analyze",
            severity="info",
            fix_hint="",
            confidence=1.0,
        )

    raw: Optional[str] = None
    image_analyzed = False
    if screenshot_provided:
        vision_prompt = _build_vision_prompt(
            console_errors, network_failures, workflow_failures, app_idea, has_screenshot=True
        )
        try:
            raw = _call_vision(vision_prompt, screenshot_b64)
            image_analyzed = True
        except Exception:
            raw = None

    if raw is None:
        if not has_error_signal:
            # No image was actually analyzed and there's no other signal --
            # a blind guess here is worse than no verdict at all.
            return JudgmentResult(
                assessment=("Screenshot could not be analyzed (no vision model available) and no "
                            "console/network/workflow errors were detected -- nothing to judge."),
                severity="info",
                fix_hint="",
                confidence=0.5,
                screenshot_available=False,
            )
        text_prompt = _build_vision_prompt(
            console_errors, network_failures, workflow_failures, app_idea, has_screenshot=False
        )
        try:
            from app.providers.ai_provider import generate_content
            raw = generate_content(text_prompt, provider=provider, max_tokens=1000, stage="llm_judge")
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
            screenshot_available=image_analyzed,
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
            screenshot_available=image_analyzed,
            raw_output=raw[:200],
        )


def _call_vision(prompt: str, image_b64: str) -> str:
    """
    Call a vision-capable LLM with the image actually attached.
    Tries Gemini first (best vision support), then OpenRouter. Raises if
    neither is available/working -- callers must NOT silently degrade to a
    text-only call using a prompt that claims an image was attached.
    """
    if _has_api_key("GEMINI_API_KEY"):
        try:
            return _call_gemini_vision(prompt, image_b64)
        except Exception:
            pass

    if _has_api_key("OPENROUTER_API_KEY"):
        try:
            return _call_openrouter_vision(prompt, image_b64)
        except Exception:
            pass

    raise RuntimeError("no vision-capable provider available")


def _has_api_key(key: str) -> bool:
    import os
    return bool(os.getenv(key))


def _call_gemini_vision(prompt: str, image_b64: str) -> str:
    """Call Gemini with image + text prompt."""
    import os
    from google import genai
    from google.genai import types

    from app.providers.gemini_provider import current_model

    client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))
    image_bytes = base64.b64decode(image_b64)

    response = client.models.generate_content(
        model=current_model(),
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
