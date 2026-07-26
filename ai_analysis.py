"""
Generates a short natural-language match-preview writeup via Google's Gemini
API (free tier, no credit card required), built entirely from data already
computed elsewhere in the app (odds, recent form, head-to-head). The model
synthesizes what's given; it isn't a fresh prediction source and doesn't get
to invent facts.
"""

import hashlib
import json
import os
from datetime import datetime, timedelta
from pathlib import Path

from google import genai
from google.genai import types

MODEL = "gemini-2.5-flash"
MAX_OUTPUT_TOKENS = 700
CACHE_DIR = Path(__file__).parent / "cache"
CACHE_TTL_SECONDS = 900  # 15 min -- odds/form don't change fast enough to justify shorter

SYSTEM_PROMPT = """You are writing a short, informational match-preview note for a personal \
football-analysis dashboard. You will be given structured data for one upcoming match: \
current market odds, each team's recent form (last ~5 results), and any known head-to-head \
history.

Rules:
- Base your analysis ONLY on the data provided. Do not invent statistics, injuries, lineups, \
or historical facts that are not in the data given to you.
- Write 3-4 short paragraphs: (1) what the market odds imply about the favourite, (2) each \
team's recent form and what it suggests, (3) head-to-head context if any is given, or a brief \
note that there isn't enough history to draw on, (4) a one-line takeaway tying it together.
- End with a one-sentence reminder that this is an AI-generated summary of the data above, not \
betting advice.
- Keep the whole thing under ~200 words. Plain prose, no headers, no markdown tables."""


class AIAnalysisError(Exception):
    """Raised when the analysis can't be generated (missing key, API error, empty response)."""


def _client() -> genai.Client:
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise AIAnalysisError(
            "GEMINI_API_KEY is not set. Get a free key (no credit card needed) at "
            "https://aistudio.google.com/apikey and set it in your environment "
            "(e.g. a .env file) to enable AI analysis."
        )
    return genai.Client(api_key=api_key)


def _cache_path(match_context: dict) -> Path:
    stable = json.dumps(match_context, sort_keys=True)
    key = hashlib.sha256(stable.encode()).hexdigest()[:24]
    return CACHE_DIR / f"ai_{key}.json"


def generate_analysis(match_context: dict) -> str:
    path = _cache_path(match_context)
    if path.exists():
        try:
            cached = json.loads(path.read_text())
            generated_at = datetime.fromisoformat(cached["generated_at"])
            if datetime.now() - generated_at < timedelta(seconds=CACHE_TTL_SECONDS):
                return cached["text"]
        except (json.JSONDecodeError, KeyError, ValueError):
            pass  # fall through and regenerate

    client = _client()
    user_content = (
        "Match data (JSON):\n"
        + json.dumps(match_context, indent=2)
        + "\n\nWrite the match preview now."
    )

    try:
        response = client.models.generate_content(
            model=MODEL,
            contents=user_content,
            config=types.GenerateContentConfig(
                system_instruction=SYSTEM_PROMPT,
                max_output_tokens=MAX_OUTPUT_TOKENS,
            ),
        )
    except Exception as exc:  # google-genai's own exception types aren't pinned here
        raise AIAnalysisError(f"Gemini API error: {exc}") from exc

    text = (getattr(response, "text", None) or "").strip()
    if not text:
        raise AIAnalysisError(
            "Gemini returned an empty response (the request may have been blocked by "
            "its safety filters)."
        )

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"generated_at": datetime.now().isoformat(), "text": text}))
    return text
