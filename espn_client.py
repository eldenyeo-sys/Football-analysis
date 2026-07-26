"""
Best-effort supplementary data from ESPN's public, unauthenticated site API
(no key required). Used only as a fallback:
  - live scores for in-play matches (sgodds doesn't show live scores, only
    pre-match odds), and
  - recent-form / head-to-head history for teams sgodds' ~5-week results
    window has no data for (typically big European clubs deep in preseason).

Every function degrades gracefully to "no data" (empty list / None) rather
than raising, so a mapping miss or an ESPN outage never breaks the dashboard.
"""

import re
import unicodedata
from datetime import datetime, timedelta
from typing import Optional
from urllib.parse import quote

import http_cache
from http_cache import FetchError

BASE = "https://site.api.espn.com/apis/site/v2/sports/soccer"
SEARCH_URL = "https://site.web.api.espn.com/apis/common/v3/search"
USER_AGENT = "football-analysis-dashboard/1.0 (personal informational use)"

_session = http_cache.new_session(USER_AGENT)

# Best-known ESPN league slugs for the leagues sgodds currently lists.
# Anything not in here (or not found) just means no ESPN-sourced live score /
# bonus history for that match -- sgodds data is still used as normal.
LEAGUE_SLUGS = {
    "norwegian league": "nor.1",
    "swedish league": "swe.1",
    "russian league": "rus.1",
    "argentine league": "arg.1",
    "brazilian league": "bra.1",
    "chilean league": "chi.1",
    "mexican league": "mex.1",
    "us soccer league": "usa.1",
    "club friendlies": "club.friendly",
    "english premier": "eng.1",
    "spanish league": "esp.1",
    "italian league": "ita.1",
    "german league": "ger.1",
    "french league": "fra.1",
}
FRIENDLY_FALLBACK_SLUGS = ["club.friendly", "fifa.friendly"]

_STOPWORDS = {"fc", "cf", "sc", "ac", "afc", "club", "de", "the", "cd", "ca"}


def _strip_diacritics(name: str) -> str:
    # "Mjällby" -> "Mjallby", "Örgryte" -> "Orgryte", etc. -- sgodds' odds
    # tables are plain ASCII, ESPN's aren't, so this has to happen before
    # any comparison.
    decomposed = unicodedata.normalize("NFKD", name)
    return "".join(c for c in decomposed if not unicodedata.combining(c))


def _normalize_words(name: str) -> list:
    ascii_name = _strip_diacritics(name).lower()
    return [w for w in re.sub(r"[^a-z0-9 ]", " ", ascii_name).split() if w not in _STOPWORDS]


def _names_match(a: str, b: str) -> bool:
    words_a, words_b = _normalize_words(a), _normalize_words(b)
    if not words_a or not words_b:
        return False

    joined_a, joined_b = "".join(words_a), "".join(words_b)
    if joined_a == joined_b or joined_a in joined_b or joined_b in joined_a:
        return True

    # Handles sgodds' abbreviated names (e.g. "D Makhachkala" for ESPN's
    # "Dynamo Makhachkala"): every word on the shorter side must prefix-match
    # (in either direction) a word on the longer side.
    shorter, longer = (words_a, words_b) if len(words_a) <= len(words_b) else (words_b, words_a)
    return all(
        any(short_word.startswith(long_word) or long_word.startswith(short_word) for long_word in longer)
        for short_word in shorter
    )


def find_team(team_name: str) -> Optional[dict]:
    try:
        data = http_cache.fetch_json(
            _session,
            f"{SEARCH_URL}?query={quote(team_name)}&limit=5&type=team",
            ttl_seconds=3600 * 12,
        )
    except FetchError:
        return None
    for item in data.get("items", []):
        if _names_match(item.get("displayName", ""), team_name):
            return {"id": item.get("id"), "league": item.get("defaultLeagueSlug")}
    return None


def _parse_event_date(event: dict) -> Optional[datetime]:
    try:
        return datetime.strptime(event.get("date", ""), "%Y-%m-%dT%H:%MZ")
    except ValueError:
        return None


def _extract_score(competitor: dict) -> Optional[int]:
    """ESPN's `score` field is sometimes a plain number/string and sometimes an
    object like {"value": 1.0, "displayValue": "1"} -- handle both."""
    score = competitor.get("score")
    if isinstance(score, dict):
        score = score.get("displayValue", score.get("value"))
    if score is None:
        return None
    try:
        return int(float(score))
    except (TypeError, ValueError):
        return None


def _finished_matches_for_team(team_id: str, league_slug: str, team_name: str, seasons: list) -> list:
    matches = []
    for season in seasons:
        url = f"{BASE}/{league_slug}/teams/{team_id}/schedule?season={season}"
        try:
            data = http_cache.fetch_json(_session, url, ttl_seconds=3600 * 6)
        except FetchError:
            continue
        for event in data.get("events", []):
            comp = event.get("competitions", [{}])[0]
            if comp.get("status", {}).get("type", {}).get("name") != "STATUS_FULL_TIME":
                continue
            competitors = comp.get("competitors", [])
            if len(competitors) != 2:
                continue
            home = next((c for c in competitors if c.get("homeAway") == "home"), None)
            away = next((c for c in competitors if c.get("homeAway") == "away"), None)
            if not home or not away:
                continue
            home_score, away_score = _extract_score(home), _extract_score(away)
            if home_score is None or away_score is None:
                continue
            matches.append(
                {
                    "date": _parse_event_date(event),
                    "home_team": home["team"]["displayName"],
                    "away_team": away["team"]["displayName"],
                    "home_score": home_score,
                    "away_score": away_score,
                    "league": league_slug,
                }
            )
        if matches:
            break  # got a season with data; older seasons would just be stale
    return matches


def get_team_recent_results(team_name: str, n: int = 5) -> list:
    """Returns scored match dicts (same shape analysis.summarize_form expects)."""
    team = find_team(team_name)
    if not team or not team.get("league"):
        return []
    this_year = datetime.now().year
    raw = _finished_matches_for_team(team["id"], team["league"], team_name, [this_year, this_year - 1])
    raw.sort(key=lambda m: m["date"] or datetime.min, reverse=True)

    scored = []
    for m in raw:
        is_home = _names_match(m["home_team"], team_name)
        gf, ga = (m["home_score"], m["away_score"]) if is_home else (m["away_score"], m["home_score"])
        result = "W" if gf > ga else "L" if gf < ga else "D"
        opponent = m["away_team"] if is_home else m["home_team"]
        scored.append(
            {
                "result": result,
                "goals_for": gf,
                "goals_against": ga,
                "opponent": opponent,
                "date": m["date"].strftime("%Y-%m-%d") if m["date"] else None,
                "score": f"{m['home_score']}-{m['away_score']}",
                "league": "ESPN",
            }
        )
        if len(scored) >= n:
            break
    return scored


def get_head_to_head(home_team: str, away_team: str, seasons_back: int = 3, limit: int = 5) -> list:
    team = find_team(home_team)
    if not team or not team.get("league"):
        return []
    this_year = datetime.now().year
    seasons = list(range(this_year, this_year - seasons_back, -1))
    raw = _finished_matches_for_team(team["id"], team["league"], home_team, seasons)
    meetings = [m for m in raw if _names_match(m["home_team"], away_team) or _names_match(m["away_team"], away_team)]
    meetings.sort(key=lambda m: m["date"] or datetime.min, reverse=True)
    return [
        {
            "date": m["date"].strftime("%Y-%m-%d") if m["date"] else "Unknown date",
            "home_team": m["home_team"],
            "away_team": m["away_team"],
            "score": f"{m['home_score']}-{m['away_score']}",
            "league": "ESPN",
        }
        for m in meetings[:limit]
    ]


def get_live_score(home_team: str, away_team: str, league_name: str, kickoff: Optional[datetime]) -> Optional[dict]:
    slugs = []
    mapped = LEAGUE_SLUGS.get((league_name or "").strip().lower())
    if mapped:
        slugs.append(mapped)
    for slug in FRIENDLY_FALLBACK_SLUGS:
        if slug not in slugs:
            slugs.append(slug)

    date_param = ""
    if kickoff:
        d0 = (kickoff - timedelta(days=1)).strftime("%Y%m%d")
        d1 = (kickoff + timedelta(days=1)).strftime("%Y%m%d")
        date_param = f"?dates={d0}-{d1}"

    for slug in slugs:
        url = f"{BASE}/{slug}/scoreboard{date_param}"
        try:
            data = http_cache.fetch_json(_session, url, ttl_seconds=20)
        except FetchError:
            continue
        for event in data.get("events", []):
            comp = event.get("competitions", [{}])[0]
            competitors = comp.get("competitors", [])
            if len(competitors) != 2:
                continue
            home_c = next((c for c in competitors if c.get("homeAway") == "home"), None)
            away_c = next((c for c in competitors if c.get("homeAway") == "away"), None)
            if not home_c or not away_c:
                continue
            if not (_names_match(home_c["team"]["displayName"], home_team) and _names_match(away_c["team"]["displayName"], away_team)):
                continue
            status = comp.get("status", {}).get("type", {})
            result = {
                "home": _extract_score(home_c),
                "away": _extract_score(away_c),
                "status": status.get("shortDetail") or status.get("description"),
                "is_final": status.get("name") == "STATUS_FULL_TIME",
            }
            details = get_match_center(event.get("id"), slug, home_team, away_team)
            if details:
                result.update(details)
            return result
    return None


def get_match_center(event_id, league_slug: str, home_team: str, away_team: str) -> Optional[dict]:
    """Venue + goal/card/sub timeline for one match, via ESPN's summary endpoint.
    Best-effort: this data isn't available for every league/match, so a miss
    just means the live view falls back to score-only."""
    if not event_id:
        return None
    url = f"{BASE}/{league_slug}/summary?event={event_id}"
    try:
        data = http_cache.fetch_json(_session, url, ttl_seconds=15)
    except FetchError:
        return None

    venue_info = None
    venue = data.get("gameInfo", {}).get("venue")
    if venue:
        address = venue.get("address", {})
        venue_info = {
            "name": venue.get("fullName"),
            "city": address.get("city"),
            "country": address.get("country"),
        }

    events = []
    for event in data.get("keyEvents", []):
        event_type = event.get("type", {}).get("text", "")
        team_name = event.get("team", {}).get("displayName", "")
        if _names_match(team_name, home_team):
            side = "home"
        elif _names_match(team_name, away_team):
            side = "away"
        else:
            side = None
        participants = event.get("participants", [])
        player = participants[0]["athlete"]["displayName"] if participants else None
        events.append(
            {
                "minute": event.get("clock", {}).get("displayValue"),
                "type": event_type,
                "player": player,
                "side": side,
            }
        )
    # ESPN returns these in chronological order already; show most recent first.
    events.reverse()

    if not venue_info and not events:
        return None
    return {"venue": venue_info, "events": events}
