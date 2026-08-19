"""
Generates a short natural-language match-preview writeup via OpenRouter (a
free-tier-friendly API gateway to many hosted models), built entirely from
data already computed elsewhere in the app (odds, recent form, head-to-head).
The model synthesizes what's given; it isn't a fresh prediction source and
doesn't get to invent facts.
"""

import hashlib
import json
import os
from datetime import datetime, timedelta
from pathlib import Path

import requests

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
MODEL = "google/gemma-4-26b-a4b-it:free"  # plain instruct model -- no hidden "thinking" tokens eating the output budget
MAX_TOKENS = 700
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


def _api_key() -> str:
    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        raise AIAnalysisError(
            "OPENROUTER_API_KEY is not set. Get a free key (no credit card needed) at "
            "https://openrouter.ai/settings/keys and set it in your environment "
            "(e.g. a .env file) to enable AI analysis."
        )
    return api_key


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

    api_key = _api_key()
    user_content = (
        "Match data (JSON):\n"
        + json.dumps(match_context, indent=2)
        + "\n\nWrite the match preview now."
    )

    try:
        response = requests.post(
            OPENROUTER_URL,
            headers={"Authorization": f"Bearer {api_key}"},
            json={
                "model": MODEL,
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": user_content},
                ],
                "max_tokens": MAX_TOKENS,
            },
            timeout=30,
        )
        response.raise_for_status()
        data = response.json()
    except requests.RequestException as exc:
        raise AIAnalysisError(f"OpenRouter API error: {exc}") from exc

    if "error" in data:
        raise AIAnalysisError(f"OpenRouter API error: {data['error'].get('message', data['error'])}")

    text = (data.get("choices") or [{}])[0].get("message", {}).get("content", "").strip()
    if not text:
        raise AIAnalysisError(
            "OpenRouter returned an empty response (the chosen free model may be temporarily "
            "rate-limited upstream -- try again shortly)."
        )

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"generated_at": datetime.now().isoformat(), "text": text}))
    return text
