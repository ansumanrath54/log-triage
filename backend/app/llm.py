"""Optional LLM summaries of a cluster, with two providers: Anthropic and Gemini.

Which provider is used (checked on every call, so changing env vars needs only a restart):
  * LLM_PROVIDER=anthropic|gemini forces one.
  * Otherwise: ANTHROPIC_API_KEY if set, else GEMINI_API_KEY (or GOOGLE_API_KEY).
  * No key at all -> a deterministic, data-only fallback summary.

Model names come from env vars so you can change them without touching code:
  LLM_MODEL    (Anthropic, default below)
  GEMINI_MODEL (Gemini, default below; copy the exact ID from Google AI Studio)

Reliability (good interview talking points):
  * hard timeout on every HTTP call
  * retry with exponential backoff on 429 / 5xx / network errors
  * errors that retrying cannot fix (bad key, unknown model) fail fast
  * every failure comes back as a short human-readable `reason`, so the UI can
    say *why* it fell back instead of guessing
  * API keys are sent in headers and never included in reasons or logs
  * the model only sees the pattern + a few sample lines, never the whole log
"""

import os
import time
from dataclasses import dataclass
from pathlib import Path

import httpx
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[2] / ".env", override=False)

ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
GEMINI_URL = "https://generativelanguage.googleapis.com/{version}/models/{model}:generateContent"
DEFAULT_ANTHROPIC_MODEL = "claude-haiku-4-5-20251001"
# Check this against the model list in Google AI Studio; override with GEMINI_MODEL.
DEFAULT_GEMINI_MODEL = "gemini-3.5-flash-lite"
DEFAULT_GEMINI_API_VERSION = "v1beta"

TIMEOUT_SECONDS = 20.0
MAX_ATTEMPTS = 3
RETRYABLE_STATUS = (429, 500, 502, 503, 504, 529)


@dataclass
class SummaryResult:
    text: str
    used_llm: bool
    provider: str | None = None
    reason: str | None = None  # why the fallback was used; None when the LLM answered


def fallback_summary(template: str, level: str, count: int, samples: list[str]) -> str:
    example = samples[0] if samples else template
    return f'{level} pattern seen {count} times: "{template}". Example line: {example[:200]}.'


def _prompt(template: str, level: str, count: int, samples: list[str]) -> str:
    joined = "\n".join(f"- {s[:300]}" for s in samples[:5])
    return (
        "You are helping an engineer triage application logs.\n"
        f"Level: {level}\nOccurrences: {count}\nNormalized pattern: {template}\n"
        f"Sample lines:\n{joined}\n\n"
        "In at most 4 sentences: say what is probably happening and list 2 or 3 "
        "likely causes to check first. Do not invent facts that are not "
        "supported by the lines above; say when something is a guess."
    )


def _choose_provider() -> tuple[str | None, str | None, str | None]:
    """Return (provider, api_key, reason_if_unavailable)."""
    forced = os.getenv("LLM_PROVIDER", "").strip().lower()
    anthropic_key = os.getenv("ANTHROPIC_API_KEY")
    gemini_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")

    if forced == "anthropic":
        if anthropic_key:
            return "anthropic", anthropic_key, None
        return None, None, "LLM_PROVIDER is anthropic but ANTHROPIC_API_KEY is not set"
    if forced == "gemini":
        if gemini_key:
            return "gemini", gemini_key, None
        return None, None, "LLM_PROVIDER is gemini but GEMINI_API_KEY is not set"
    if forced:
        return None, None, f"unknown LLM_PROVIDER '{forced}' (use anthropic or gemini)"

    if anthropic_key:
        return "anthropic", anthropic_key, None
    if gemini_key:
        return "gemini", gemini_key, None
    return None, None, "no API key set (set GEMINI_API_KEY or ANTHROPIC_API_KEY before starting the backend)"


def _http_reason(r: httpx.Response) -> str:
    try:
        msg = r.json()["error"]["message"]
    except Exception:
        msg = r.text
    msg = " ".join(str(msg).split())[:200]
    hint = {
        400: "request rejected",
        401: "API key rejected",
        403: "API key rejected or API not enabled for it",
        404: "model not found (set LLM_MODEL or GEMINI_MODEL to a valid model ID)",
    }.get(r.status_code, "request failed")
    return f"{hint} (HTTP {r.status_code}): {msg}"


def _post_with_retries(url: str, headers: dict, body: dict) -> tuple[httpx.Response | None, str | None]:
    """POST with timeout and exponential backoff. Returns (response, None) or (None, reason)."""
    delay = 1.0
    reason = "unknown error"
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            r = httpx.post(url, headers=headers, json=body, timeout=TIMEOUT_SECONDS)
        except httpx.TimeoutException:
            reason = f"request timed out after {TIMEOUT_SECONDS:.0f}s"
        except httpx.TransportError as e:
            reason = f"network error ({type(e).__name__})"
        else:
            if r.status_code == 200:
                return r, None
            if r.status_code not in RETRYABLE_STATUS:
                return None, _http_reason(r)  # retrying will not help
            reason = f"provider busy or rate limited (HTTP {r.status_code})"
        if attempt < MAX_ATTEMPTS:
            time.sleep(delay)
            delay *= 2
    return None, f"{reason}, gave up after {MAX_ATTEMPTS} attempts"


def _call_anthropic(key: str, prompt: str) -> tuple[str | None, str | None]:
    headers = {
        "x-api-key": key,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }
    body = {
        # `or`, not a getenv default: docker-compose passes unset vars as empty strings.
        "model": os.getenv("LLM_MODEL") or DEFAULT_ANTHROPIC_MODEL,
        "max_tokens": 300,
        "messages": [{"role": "user", "content": prompt}],
    }
    r, reason = _post_with_retries(ANTHROPIC_URL, headers, body)
    if r is None:
        return None, reason
    parts = r.json().get("content", [])
    text = "".join(p.get("text", "") for p in parts if p.get("type") == "text").strip()
    return (text, None) if text else (None, "model returned an empty answer")


def _call_gemini(key: str, prompt: str) -> tuple[str | None, str | None]:
    model = os.getenv("GEMINI_MODEL") or DEFAULT_GEMINI_MODEL
    version = os.getenv("GEMINI_API_VERSION") or DEFAULT_GEMINI_API_VERSION
    # Key goes in a header, not the URL, so it cannot end up in logs or error text.
    headers = {"x-goog-api-key": key, "content-type": "application/json"}
    # Generous token limit: some Gemini models spend part of it on internal "thinking".
    body = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"maxOutputTokens": 1024},
    }
    r, reason = _post_with_retries(
        GEMINI_URL.format(version=version, model=model), headers, body
    )
    if r is None:
        return None, reason
    data = r.json()
    try:
        parts = data["candidates"][0]["content"]["parts"]
        text = "".join(p.get("text", "") for p in parts).strip()
    except (KeyError, IndexError, TypeError):
        text = ""
    if text:
        return text, None
    block = (data.get("promptFeedback") or {}).get("blockReason")
    return None, f"model returned no text{f' (blocked: {block})' if block else ''}"


def summarize(template: str, level: str, count: int, samples: list[str]) -> SummaryResult:
    provider, key, unavailable = _choose_provider()
    if provider is None:
        return SummaryResult(fallback_summary(template, level, count, samples), False, None, unavailable)

    prompt = _prompt(template, level, count, samples)
    call = _call_anthropic if provider == "anthropic" else _call_gemini
    text, reason = call(key, prompt)
    if text:
        return SummaryResult(text, True, provider, None)
    return SummaryResult(fallback_summary(template, level, count, samples), False, provider, reason)
