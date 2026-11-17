from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any

from . import news

SIGNAL_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "symbol": {"type": "STRING"},
        "action": {"type": "STRING", "enum": ["BUY", "SELL", "HOLD"]},
        "confidence": {"type": "NUMBER"},
        "horizon": {"type": "STRING"},
        "reasoning": {"type": "STRING"},
        "news_refs": {"type": "ARRAY", "items": {"type": "STRING"}},
        "data_as_of": {"type": "STRING"},
        "disclaimers": {"type": "STRING"},
    },
    "required": ["symbol", "action", "confidence", "horizon", "reasoning", "news_refs", "data_as_of", "disclaimers"],
}

DISCLAIMER = ("Advisory only; NSE CM data is delayed 1–3 minutes. "
              "Not investment advice. Verify source data and consider a SEBI-registered advisor.")


def normalize_signal(raw: Any, symbol: str, quote: dict[str, Any],
                     headlines: list[dict[str, str]], model: str) -> dict[str, Any]:
    """Validate and constrain model output before it reaches the UI/database."""
    if not isinstance(raw, dict):
        raise ValueError("Gemini returned a non-object signal")
    action = str(raw.get("action", "HOLD")).upper()
    if action not in {"BUY", "SELL", "HOLD"}:
        action = "HOLD"
    try:
        confidence = max(0.0, min(1.0, float(raw.get("confidence", 0))))
    except (TypeError, ValueError):
        confidence = 0.0
    valid_urls = {item.get("url") for item in headlines if item.get("url")}
    refs = raw.get("news_refs", [])
    if not isinstance(refs, list):
        refs = []
    refs = [str(ref) for ref in refs if str(ref) in valid_urls]
    return {
        "symbol": symbol.upper(),
        "action": action,
        "confidence": confidence,
        "horizon": str(raw.get("horizon") or "unspecified"),
        "reasoning": str(raw.get("reasoning") or "Insufficient evidence; monitoring only."),
        "news_refs": refs,
        "data_as_of": quote.get("as_of") or datetime.now(timezone.utc).isoformat(),
        "disclaimers": DISCLAIMER,
        "headlines": headlines,
        "model": model,
    }


def generate(symbol: str, quote: dict[str, Any], analysis: dict[str, Any] | None = None) -> dict[str, Any]:
    api_key = os.getenv("GEMINI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("Set GEMINI_API_KEY in .env to generate advisory signals.")
    from google import genai
    from google.genai import types

    try:
        headlines = news.headlines(symbol)
    except Exception:
        headlines = []
    model = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
    client = genai.Client(api_key=api_key)
    response = client.models.generate_content(
        model=model,
        contents=json.dumps({"symbol": symbol, "quote": quote, "analysis": analysis, "news": headlines}, ensure_ascii=False),
        config=types.GenerateContentConfig(
            system_instruction=("You are an advisory-only Indian stock research assistant. Use only the supplied NSE quote, analysis and headlines. "
                                "Do not invent facts, prices or news; when evidence is insufficient choose HOLD with low confidence. "
                                "State the quote delay and timestamp. Signals are not personalized financial advice and must not direct automatic orders."),
            response_mime_type="application/json", response_schema=SIGNAL_SCHEMA, temperature=0.1,
        ),
    )
    try:
        raw = json.loads(response.text or "{}")
    except json.JSONDecodeError as exc:
        raise ValueError("Gemini returned invalid JSON for the signal") from exc
    return normalize_signal(raw, symbol, quote, headlines, model)
