"""
Generates a short natural-language match-preview writeup via the Claude API,
built entirely from data already computed elsewhere in the app (odds, recent
form, head-to-head). The model synthesizes what's given; it isn't a fresh
prediction source and doesn't get to invent facts.
"""

import hashlib
import json
import os
from datetime import datetime, timedelta
from pathlib import Path

import anthropic

MODEL = "claude-opus-5"
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
- Do not include internal or system XML tags (like <thinking>) in your response.
- End with a one-sentence reminder that this is an AI-generated summary of the data above, not \
betting advice.
- Keep the whole thing under ~200 words. Plain prose, no headers, no markdown tables."""


class AIAnalysisError(Exception):
    """Raised when the analysis can't be generated (missing key, API error, refusal)."""


def _client() -> anthropic.Anthropic:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise AIAnalysisError(
            "ANTHROPIC_API_KEY is not set. Get a free key at "
            "https://console.anthropic.com/settings/keys and set it in your environment "
            "(e.g. a .env file) to enable AI analysis."
        )
    return anthropic.Anthropic()


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
        response = client.messages.create(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            output_config={"effort": "low"},
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_content}],
        )
    except anthropic.APIError as exc:
        raise AIAnalysisError(f"Claude API error: {exc}") from exc

    if response.stop_reason == "refusal":
        raise AIAnalysisError("The model declined to generate an analysis for this match.")

    text = "".join(block.text for block in response.content if block.type == "text").strip()
    if not text:
        raise AIAnalysisError("The model returned an empty response.")

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"generated_at": datetime.now().isoformat(), "text": text}))
    return text
