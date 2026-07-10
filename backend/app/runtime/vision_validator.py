"""
V4 Vision-Based UI Validation

Playwright takes screenshots of key pages.
Claude (vision) analyzes each screenshot and reports:
  - Broken layout (overlapping elements, off-screen content)
  - Blank/white pages
  - Error messages visible on screen
  - Missing navigation elements
  - Overall UI quality score

Uses the Anthropic API directly since it needs multimodal input.
"""
import base64
import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path

from app.utils.json_cleaner import extract_json


@dataclass
class VisionIssue:
    severity: str  # "critical" | "warning" | "info"
    page: str
    description: str


@dataclass
class VisionResult:
    success: bool
    ui_score: int  # 0-100
    issues: list = field(default_factory=list)
    page_scores: dict = field(default_factory=dict)
    duration: float = 0.0
    skipped: bool = False
    skip_reason: str = ""


_VISION_PROMPT = """Analyze these React app screenshots and return ONLY valid JSON:
{
  "ui_score": <0-100>,
  "pages": {
    "<route>": {
      "score": <0-100>,
      "issues": [{"severity": "critical|warning|info", "description": "<issue>"}]
    }
  },
  "summary": "<one sentence>"
}

Score guide:
  90-100 = polished, modern design with consistent color theme, good typography, clear navigation (sidebar OR top nav bar — both are equally valid shells)
  75-89  = clean and functional, styled cards/forms, readable layout, minor imperfections
  60-74  = basic but working, minimal styling, some missing structure
  40-59  = broken layout elements, unstyled or white page with minimal content
  0-39   = blank white page, JS error visible, completely broken

Be GENEROUS: if a page has a visible heading, styled buttons, and readable content, it scores at least 70.
If it also has clear navigation (a sidebar or a top nav bar — some apps are
deliberately designed with a top-nav content shell instead of a sidebar; never
penalize the absence of a sidebar when a top nav is present), stats cards, and
a consistent color scheme, it scores 85+.
Only score below 60 if something is genuinely broken (white page, error text, invisible content).
Critical issues: blank white page, JS error shown on screen, layout completely broken.
Warnings: no visible navigation at all, unstyled form inputs, no example data shown.
Info: minor alignment, color inconsistency, small UX suggestions."""


def _call_via_anthropic_sdk(screenshots: list[dict], api_key: str) -> dict:
    import anthropic
    client = anthropic.Anthropic(api_key=api_key)
    content = []
    for shot in screenshots:
        content.extend([
            {"type": "text", "text": f"Screenshot of page: {shot['route']}"},
            {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": shot["png_b64"]}},
        ])
    content.append({"type": "text", "text": _VISION_PROMPT})
    resp = client.messages.create(model="claude-sonnet-4-6", max_tokens=1000, messages=[{"role": "user", "content": content}])
    return resp.content[0].text


def _call_via_openrouter(screenshots: list[dict], api_key: str) -> str:
    import urllib.request
    content = []
    for shot in screenshots:
        content.extend([
            {"type": "text", "text": f"Screenshot of page: {shot['route']}"},
            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{shot['png_b64']}"}},
        ])
    content.append({"type": "text", "text": _VISION_PROMPT})
    payload = json.dumps({
        "model": "google/gemini-2.5-flash",
        "max_tokens": 1000,
        "messages": [{"role": "user", "content": content}],
    }).encode()
    req = urllib.request.Request(
        "https://openrouter.ai/api/v1/chat/completions",
        data=payload,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read())
    return data["choices"][0]["message"]["content"]


def _call_via_gemini(screenshots: list[dict], api_key: str) -> str:
    """Use the current Gemini flash model for vision analysis (google.genai new SDK)."""
    from google import genai
    from google.genai import types
    from app.providers.gemini_provider import current_model
    client = genai.Client(api_key=api_key)
    parts = []
    for shot in screenshots:
        parts.append(types.Part.from_text(text=f"Screenshot of page: {shot['route']}"))
        img_bytes = base64.b64decode(shot["png_b64"])
        parts.append(types.Part.from_bytes(data=img_bytes, mime_type="image/png"))
    parts.append(types.Part.from_text(text=_VISION_PROMPT))
    response = client.models.generate_content(
        model=current_model(),
        contents=[types.Content(parts=parts, role="user")],
    )
    return response.text


def _call_vision_api(screenshots: list[dict]) -> dict:
    """
    Send screenshots to Claude/Gemini and get structured UI feedback.
    Priority: ANTHROPIC_API_KEY → OPENROUTER_API_KEY → GEMINI_API_KEY
    """
    anthropic_key = os.environ.get("ANTHROPIC_API_KEY")
    openrouter_key = os.environ.get("OPENROUTER_API_KEY")
    gemini_key = os.environ.get("GEMINI_API_KEY")

    if not anthropic_key and not openrouter_key and not gemini_key:
        raise RuntimeError("No vision API key set (ANTHROPIC_API_KEY, OPENROUTER_API_KEY, or GEMINI_API_KEY)")

    last_err = None
    if anthropic_key:
        try:
            raw = _call_via_anthropic_sdk(screenshots, anthropic_key)
            parsed = extract_json(raw)
            if parsed:
                return parsed
        except Exception as e:
            last_err = e

    if openrouter_key:
        try:
            raw = _call_via_openrouter(screenshots, openrouter_key)
            parsed = extract_json(raw)
            if parsed:
                return parsed
        except Exception as e:
            last_err = e

    if gemini_key:
        try:
            raw = _call_via_gemini(screenshots, gemini_key)
            parsed = extract_json(raw)
            if parsed:
                return parsed
        except Exception as e:
            last_err = e

    raise RuntimeError(f"All vision providers failed. Last error: {last_err}")


def run_vision_validation(
    project_path: str,
    architecture: dict | None = None,
    existing_screenshots: list[dict] | None = None,
) -> VisionResult:
    """
    Run vision-based UI validation.

    If existing_screenshots are provided (from a prior Playwright run),
    uses those. Otherwise takes fresh screenshots via headless Chromium.
    """
    t0 = time.time()

    if (not os.environ.get("ANTHROPIC_API_KEY")
            and not os.environ.get("OPENROUTER_API_KEY")
            and not os.environ.get("GEMINI_API_KEY")):
        return VisionResult(
            success=False, ui_score=0, skipped=True,
            skip_reason="No vision API key set (need ANTHROPIC_API_KEY, OPENROUTER_API_KEY, or GEMINI_API_KEY)",
            duration=round(time.time() - t0, 2),
        )

    screenshots = existing_screenshots or []

    # Take fresh screenshots if none provided
    if not screenshots:
        dist_dir = Path(project_path) / "dist"
        if not dist_dir.exists():
            return VisionResult(
                success=False, ui_score=0, skipped=True,
                skip_reason="dist/ not found — build frontend first",
                duration=round(time.time() - t0, 2),
            )

        try:
            from app.runtime.playwright_runner import run_playwright_tests
            result = run_playwright_tests(
                project_path, architecture, capture_screenshots=True
            )
            screenshots = result.screenshots
        except Exception as e:
            return VisionResult(
                success=False, ui_score=0, skipped=True,
                skip_reason=f"Screenshot capture failed: {e}",
                duration=round(time.time() - t0, 2),
            )

    if not screenshots:
        return VisionResult(
            success=False, ui_score=0, skipped=True,
            skip_reason="No screenshots captured",
            duration=round(time.time() - t0, 2),
        )

    # Send to Claude vision
    try:
        feedback = _call_vision_api(screenshots)
    except Exception as e:
        return VisionResult(
            success=False, ui_score=50, skipped=True,
            skip_reason=f"Vision API error: {e}",
            duration=round(time.time() - t0, 2),
        )

    ui_score = int(feedback.get("ui_score", 50))
    page_scores = {}
    issues = []

    for route, page_data in feedback.get("pages", {}).items():
        page_scores[route] = page_data.get("score", 50)
        for issue in page_data.get("issues", []):
            issues.append(VisionIssue(
                severity=issue.get("severity", "info"),
                page=route,
                description=issue.get("description", ""),
            ))

    critical_count = sum(1 for i in issues if i.severity == "critical")
    success = ui_score >= 60 and critical_count == 0

    return VisionResult(
        success=success,
        ui_score=max(0, min(100, ui_score)),
        issues=issues,
        page_scores=page_scores,
        duration=round(time.time() - t0, 2),
    )
