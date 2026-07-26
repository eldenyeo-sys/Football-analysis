"""
Pure, side-effect-free scoring functions: turns raw odds + recent results into
outcome probabilities and a confidence label. This is a transparent heuristic
for informational purposes only -- not a trained model and not betting advice.
"""

import math
import re
from datetime import datetime
from typing import Optional

HOME_ADVANTAGE = 0.15
MARKET_WEIGHT = 0.65
FORM_WEIGHT_PPG = 0.7
FORM_WEIGHT_GD = 0.3
NEUTRAL_PPG = 1.2  # roughly league-average points per game
NEUTRAL_GD = 0.0

CONFIDENCE_HIGH = 0.55
CONFIDENCE_MEDIUM = 0.40


def implied_probabilities(odds_home, odds_draw, odds_away) -> Optional[dict]:
    """Converts decimal odds to implied probabilities, normalized to remove
    the bookmaker's overround (so the three probabilities sum to 1)."""
    if not (odds_home and odds_draw and odds_away):
        return None
    raw_home, raw_draw, raw_away = 1 / odds_home, 1 / odds_draw, 1 / odds_away
    total = raw_home + raw_draw + raw_away
    return {"home": raw_home / total, "draw": raw_draw / total, "away": raw_away / total}


def score_result(team: str, result_row) -> Optional[dict]:
    """Returns {'result': 'W'|'D'|'L', 'goals_for': int, 'goals_against': int}
    for the given team in a finished ResultRow, or None if unparseable."""
    if not result_row.ft_score or "-" not in result_row.ft_score:
        return None
    try:
        home_goals, away_goals = (int(x) for x in result_row.ft_score.split("-", 1))
    except ValueError:
        return None

    if result_row.home_team.lower() == team.lower():
        goals_for, goals_against = home_goals, away_goals
    elif result_row.away_team.lower() == team.lower():
        goals_for, goals_against = away_goals, home_goals
    else:
        return None

    if goals_for > goals_against:
        result = "W"
    elif goals_for < goals_against:
        result = "L"
    else:
        result = "D"
    return {"result": result, "goals_for": goals_for, "goals_against": goals_against}


def recent_form(pool, team: str, n: int = 5) -> dict:
    team_matches = [
        row for row in pool if team.lower() in (row.home_team.lower(), row.away_team.lower())
    ]
    dated = sorted((m for m in team_matches if m.date is not None), key=lambda r: r.date, reverse=True)
    undated = [m for m in team_matches if m.date is None]
    team_matches = dated + undated

    recent = []
    for row in team_matches:
        scored = score_result(team, row)
        if scored is None:
            continue
        recent.append({**scored, "opponent": row.away_team if row.home_team.lower() == team.lower() else row.home_team,
                        "date": row.date.strftime("%Y-%m-%d") if row.date else None,
                        "score": row.ft_score, "league": row.league})
        if len(recent) >= n:
            break

    return summarize_form(recent, source="sgodds")


def summarize_form(matches: list, source: str = "sgodds") -> dict:
    """Turns a list of already-scored match dicts (result/goals_for/goals_against/...)
    into the ppg/gd_pg/record summary. Shared by sgodds-pool-based and
    ESPN-fallback-based recent form, so both feed the prediction the same way."""
    if not matches:
        return {"matches": [], "ppg": None, "gd_pg": None, "record": "No recent results found", "source": None}

    points = sum({"W": 3, "D": 1, "L": 0}[m["result"]] for m in matches)
    goal_diff = sum(m["goals_for"] - m["goals_against"] for m in matches)
    wins = sum(1 for m in matches if m["result"] == "W")
    draws = sum(1 for m in matches if m["result"] == "D")
    losses = sum(1 for m in matches if m["result"] == "L")

    return {
        "matches": matches,
        "ppg": points / len(matches),
        "gd_pg": goal_diff / len(matches),
        "record": f"{wins}W-{draws}D-{losses}L (last {len(matches)})",
        "source": source,
    }


def head_to_head(pool, home_team: str, away_team: str) -> list:
    pair = {home_team.lower(), away_team.lower()}
    matches = [
        row
        for row in pool
        if {row.home_team.lower(), row.away_team.lower()} == pair
    ]
    matches.sort(key=lambda r: r.date or datetime.min, reverse=True)
    return [
        {
            "date": row.date.strftime("%Y-%m-%d") if row.date else "Unknown date",
            "home_team": row.home_team,
            "away_team": row.away_team,
            "score": row.ft_score,
            "league": row.league,
        }
        for row in matches
    ]


def head_to_head_summary(meetings: list, team_a: str, team_b: str) -> dict:
    """Aggregates a list of head-to-head meeting dicts (date/home_team/away_team/score)
    into a W-D-L / goals record from team_a's perspective, regardless of which
    side was home in each individual past meeting."""
    a_wins = b_wins = draws = 0
    a_goals = b_goals = 0
    counted = 0

    for m in meetings:
        score = m.get("score") or ""
        if "-" not in score:
            continue
        try:
            home_goals, away_goals = (int(x) for x in score.split("-", 1))
        except ValueError:
            continue

        home_is_a = m["home_team"].lower() == team_a.lower()
        home_is_b = m["home_team"].lower() == team_b.lower()
        if not (home_is_a or home_is_b):
            continue

        a_goals_this, b_goals_this = (home_goals, away_goals) if home_is_a else (away_goals, home_goals)
        a_goals += a_goals_this
        b_goals += b_goals_this
        counted += 1
        if a_goals_this > b_goals_this:
            a_wins += 1
        elif a_goals_this < b_goals_this:
            b_wins += 1
        else:
            draws += 1

    return {
        "meetings_count": counted,
        "team_a_wins": a_wins,
        "team_b_wins": b_wins,
        "draws": draws,
        "team_a_goals": a_goals,
        "team_b_goals": b_goals,
        "avg_goals_per_game": round((a_goals + b_goals) / counted, 2) if counted else None,
    }


def _outcome_probabilities_from_diff(diff: float) -> dict:
    """Maps a home-strength-minus-away-strength differential to a 3-way
    outcome distribution via a logistic curve, with draw probability
    shrinking as the match looks more lopsided."""
    sigmoid = 1 / (1 + math.exp(-diff))
    draw_prob = max(0.12, min(0.30, 0.26 - 0.02 * abs(diff)))
    remaining = 1 - draw_prob
    return {
        "home": remaining * sigmoid,
        "draw": draw_prob,
        "away": remaining * (1 - sigmoid),
    }


def form_probabilities(home_form: dict, away_form: dict) -> dict:
    home_ppg = home_form["ppg"] if home_form["ppg"] is not None else NEUTRAL_PPG
    away_ppg = away_form["ppg"] if away_form["ppg"] is not None else NEUTRAL_PPG
    home_gd = home_form["gd_pg"] if home_form["gd_pg"] is not None else NEUTRAL_GD
    away_gd = away_form["gd_pg"] if away_form["gd_pg"] is not None else NEUTRAL_GD

    home_strength = FORM_WEIGHT_PPG * home_ppg + FORM_WEIGHT_GD * home_gd
    away_strength = FORM_WEIGHT_PPG * away_ppg + FORM_WEIGHT_GD * away_gd
    diff = (home_strength - away_strength) + HOME_ADVANTAGE
    return _outcome_probabilities_from_diff(diff)


def blend(market_prob: Optional[dict], form_prob: dict, market_weight: float = MARKET_WEIGHT) -> dict:
    if market_prob is None:
        return form_prob
    blended = {
        key: market_weight * market_prob[key] + (1 - market_weight) * form_prob[key]
        for key in ("home", "draw", "away")
    }
    total = sum(blended.values())
    return {key: value / total for key, value in blended.items()}


def confidence_label(top_prob: float) -> str:
    if top_prob >= CONFIDENCE_HIGH:
        return "High"
    if top_prob >= CONFIDENCE_MEDIUM:
        return "Medium"
    return "Low"


def parse_match_minute(status: Optional[str]) -> Optional[int]:
    """Best-effort minute-of-match from an ESPN-style status string
    ("63'", "45'+2'", "HT", "FT"). Returns None if it can't be parsed --
    callers should treat that as "unknown, assume roughly half-time"."""
    if not status:
        return None
    s = status.strip().upper()
    if s in ("HT", "HALFTIME", "HALF-TIME"):
        return 45
    if s in ("FT", "FULL TIME", "FULL-TIME", "AET", "PEN"):
        return 90
    match = re.match(r"(\d+)", s)
    if match:
        return min(90, int(match.group(1)))
    return None


def live_score_adjustment(pre_match_prob: dict, live_score: Optional[dict]) -> dict:
    """Re-weights the pre-match probabilities against what's actually happening
    on the pitch. The live scoreline gets more say the further into the match
    we are -- a 1-0 lead at kickoff barely moves the needle, the same lead in
    the 85th minute should dominate."""
    if not live_score:
        return pre_match_prob

    try:
        home_goals = int(live_score.get("home"))
        away_goals = int(live_score.get("away"))
    except (TypeError, ValueError):
        return pre_match_prob

    minute = parse_match_minute(live_score.get("status"))
    elapsed = 0.5 if minute is None else min(1.0, minute / 90)
    goal_diff = home_goals - away_goals

    if goal_diff == 0:
        # A goalless or level scoreline makes a draw more credible than the
        # pre-match line implied, growing as the clock runs down.
        live_prob = {"home": 0.28, "draw": 0.44, "away": 0.28}
        live_weight = 0.25 + 0.35 * elapsed
    else:
        intensity = 1.0 + 2.5 * elapsed  # a late lead is worth much more than an early one
        live_prob = _outcome_probabilities_from_diff(goal_diff * intensity)
        live_weight = min(0.93, 0.35 + 0.55 * elapsed)

    blended = {
        key: live_weight * live_prob[key] + (1 - live_weight) * pre_match_prob[key]
        for key in ("home", "draw", "away")
    }
    total = sum(blended.values())
    return {key: value / total for key, value in blended.items()}


def predict(odds_home, odds_draw, odds_away, home_form: dict, away_form: dict, live_score: Optional[dict] = None) -> dict:
    market_prob = implied_probabilities(odds_home, odds_draw, odds_away)
    form_prob = form_probabilities(home_form, away_form)
    probabilities = blend(market_prob, form_prob)
    probabilities = live_score_adjustment(probabilities, live_score)

    outcome = max(probabilities, key=probabilities.get)
    outcome_label = {"home": "Home Win", "draw": "Draw", "away": "Away Win"}[outcome]

    return {
        "outcome": outcome_label,
        "probabilities": {k: round(v * 100, 1) for k, v in probabilities.items()},
        "confidence": confidence_label(probabilities[outcome]),
        "market_based": market_prob is not None,
        "live_adjusted": live_score is not None and live_score.get("home") is not None,
    }
