"""
Generates a short, section-structured match analysis via OpenRouter (a
free-tier-friendly API gateway to many hosted models), built entirely from
data already computed elsewhere in the app (recent results, head-to-head,
odds). The model synthesizes what's given; it isn't a fresh prediction
source and doesn't get to invent facts.
"""

import hashlib
import json
import os
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import requests

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
# Plain instruct models only -- no hidden "thinking" tokens eating the output budget. Tried in
# order; free-tier models get rate-limited/at-capacity often enough that a fallback meaningfully
# cuts how often a click fails outright and has to be manually retried.
MODELS = ["google/gemma-4-26b-a4b-it:free", "google/gemma-4-31b-it:free"]
MAX_TOKENS = 500  # ~260-word target is well under this; caps worst-case generation time
REQUEST_TIMEOUT_SECONDS = 15  # fail fast into the next fallback model rather than hang
CACHE_DIR = Path(__file__).parent / "cache"
CACHE_TTL_SECONDS = 900  # 15 min -- odds/form don't change fast enough to justify shorter

# Reused across requests so repeated calls keep the TLS/TCP connection alive instead of
# renegotiating a new one every time (this endpoint is called on every "AI Analysis" click).
_session = requests.Session()

# Ordered section keys -> the exact heading text the model is asked to emit on its own line.
SECTION_HEADINGS = {
    "performance": "TEAM PERFORMANCE",
    "head_to_head": "HEAD-TO-HEAD",
    "market": "MARKET CONTEXT",
    "betting_angle": "BETTING ANGLE",
}
_HEADING_TO_KEY = {v: k for k, v in SECTION_HEADINGS.items()}

SYSTEM_PROMPT = """You are writing a short, data-grounded match analysis note for a personal \
football-analysis dashboard. You will be given structured data for one upcoming match: each \
team's recent results (with scores), head-to-head history, current market odds, and this app's \
own heuristic prediction.

Rules:
- Base your analysis ONLY on the data provided. Do not invent statistics, injuries, lineups, \
or historical facts that are not in the data given to you.
- Team performance and history are the PRIMARY focus. Market odds are SECONDARY context only --
mention them briefly, don't lead with them or over-weight them.
- Structure your reply as exactly four sections, each starting with the heading shown below in \
capitals on its own line (nothing else on that line), followed by 1-3 short sentences. Do not \
use any other headings, markdown, or bullet points.

TEAM PERFORMANCE
Analyse each team's recent results -- form trend, scoring/conceding patterns, any momentum --
using the specific matches given, not just the summary record.

HEAD-TO-HEAD
What these two teams' past meetings (if any) suggest. If none are given, say so plainly.

MARKET CONTEXT
One brief line on what current odds imply, noted only as secondary context.

BETTING ANGLE
Based on the performance/history analysis above, suggest ONE type of bet that best fits the \
pattern (e.g. Home Win, Away Win, Draw No Bet, Double Chance, Over/Under goals, Both Teams to \
Score, Asian Handicap) with a one-line rationale grounded in the data. If the data is too thin \
to support any angle, say so plainly instead of guessing. End this section with: "AI-generated \
read of the data above, not a guaranteed outcome -- gamble responsibly."

Keep the whole reply under ~260 words total."""


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
    # odds/prediction tick on every ~20s auto-refresh but don't materially change the analysis
    # (they're secondary context) -- excluding them from the key keeps a still-fresh cached
    # analysis a cache hit instead of forcing a fresh, slow API call every time the odds shift.
    cache_key_context = {k: v for k, v in match_context.items() if k not in ("odds", "prediction")}
    stable = json.dumps(cache_key_context, sort_keys=True)
    key = hashlib.sha256(stable.encode()).hexdigest()[:24]
    return CACHE_DIR / f"ai_{key}.json"


def _parse_sections(text: str) -> dict:
    """Splits the model's heading-delimited reply into {key: paragraph}. Falls back to a
    single 'performance' section if the model didn't follow the heading format."""
    heading_pattern = re.compile(r"^\s*(" + "|".join(re.escape(h) for h in _HEADING_TO_KEY) + r")\s*:?\s*$")

    sections: dict = {}
    current_key = None
    buffer: list = []

    for line in text.splitlines():
        match = heading_pattern.match(line.strip().upper())
        if match:
            if current_key:
                sections[current_key] = "\n".join(buffer).strip()
            current_key = _HEADING_TO_KEY[match.group(1)]
            buffer = []
        else:
            buffer.append(line)
    if current_key:
        sections[current_key] = "\n".join(buffer).strip()

    if len(sections) < 2:  # model didn't follow the format -- degrade to one block rather than fail
        return {"performance": text.strip()}
    return sections


def generate_analysis(match_context: dict) -> dict:
    path = _cache_path(match_context)
    if path.exists():
        try:
            cached = json.loads(path.read_text())
            generated_at = datetime.fromisoformat(cached["generated_at"])
            if datetime.now() - generated_at < timedelta(seconds=CACHE_TTL_SECONDS):
                return cached["sections"]
        except (json.JSONDecodeError, KeyError, ValueError):
            pass  # fall through and regenerate

    api_key = _api_key()
    user_content = (
        "Match data (JSON):\n"
        + json.dumps(match_context, indent=2)
        + "\n\nWrite the match analysis now."
    )
    payload_messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_content},
    ]

    text = ""
    last_error: Optional[str] = None
    for model in MODELS:
        try:
            response = _session.post(
                OPENROUTER_URL,
                headers={"Authorization": f"Bearer {api_key}"},
                json={"model": model, "messages": payload_messages, "max_tokens": MAX_TOKENS},
                timeout=REQUEST_TIMEOUT_SECONDS,
            )
            data = response.json()
        except requests.RequestException as exc:
            last_error = f"OpenRouter API error ({model}): {exc}"
            continue

        if not response.ok or "error" in data:
            last_error = f"OpenRouter API error ({model}): {data.get('error', response.text)}"
            continue

        text = (data.get("choices") or [{}])[0].get("message", {}).get("content", "").strip()
        if text:
            break
        last_error = f"OpenRouter returned an empty response ({model})"

    if not text:
        raise AIAnalysisError(
            last_error or "OpenRouter returned an empty response -- try again shortly."
        )

    sections = _parse_sections(text)

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"generated_at": datetime.now().isoformat(), "sections": sections}))
    return sections
