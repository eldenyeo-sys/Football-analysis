from datetime import datetime

from flask import Flask, jsonify, request, send_from_directory

import analysis
import espn_client
import sgodds_client
from sgodds_client import SgoddsError

app = Flask(__name__, static_folder="static", static_url_path="")

MATCHES_SHOWN = 11  # "current" + next 10


@app.route("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


def _form_with_fallback(pool, team_name):
    form = analysis.recent_form(pool, team_name)
    if not form["matches"]:
        espn_matches = espn_client.get_team_recent_results(team_name)
        if espn_matches:
            form = analysis.summarize_form(espn_matches, source="espn")
    return form


def _head_to_head_with_fallback(pool, league, home_team, away_team):
    h2h = analysis.head_to_head(pool, home_team, away_team)
    if h2h:
        return h2h

    bonus = sgodds_client.get_season_matches(league)
    if bonus:
        h2h = analysis.head_to_head(bonus, home_team, away_team)
        if h2h:
            return h2h

    return espn_client.get_head_to_head(home_team, away_team)


@app.route("/api/matches")
def api_matches():
    try:
        matches = sgodds_client.get_upcoming_matches(limit=MATCHES_SHOWN)
        pool = sgodds_client.get_results_pool()
    except SgoddsError as exc:
        return jsonify({"error": str(exc)}), 502
    except Exception as exc:  # sgodds changed its page structure, etc.
        return jsonify({"error": f"Unexpected error while reading sgodds.com: {exc}"}), 502

    payload = []
    for match in matches:
        home_form = _form_with_fallback(pool, match.home_team)
        away_form = _form_with_fallback(pool, match.away_team)
        h2h = _head_to_head_with_fallback(pool, match.league, match.home_team, match.away_team)

        prediction = analysis.predict(
            match.odds_home, match.odds_draw, match.odds_away, home_form, away_form
        )

        live_score = None
        if match.is_live:
            live_score = espn_client.get_live_score(
                match.home_team, match.away_team, match.league, match.kickoff
            )

        payload.append(
            {
                "match_id": match.match_id,
                "league": match.league,
                "home_team": match.home_team,
                "away_team": match.away_team,
                "kickoff": match.kickoff.isoformat() if match.kickoff else None,
                "is_live": match.is_live,
                "live_score": live_score,
                "odds": {
                    "home": match.odds_home,
                    "draw": match.odds_draw,
                    "away": match.odds_away,
                },
                "prediction": prediction,
                "home_form": home_form,
                "away_form": away_form,
                "head_to_head": h2h,
                "detail_url": match.detail_url,
            }
        )

    return jsonify({"generated_at": datetime.now().isoformat(), "matches": payload})


@app.route("/api/live-score")
def api_live_score():
    """Lightweight single-match lookup for the live match-tracker view to poll
    frequently, without re-scraping sgodds or recomputing every match's analysis."""
    home_team = request.args.get("home", "")
    away_team = request.args.get("away", "")
    league = request.args.get("league", "")
    kickoff_raw = request.args.get("kickoff", "")

    if not home_team or not away_team:
        return jsonify({"error": "home and away query params are required"}), 400

    kickoff = None
    if kickoff_raw:
        try:
            kickoff = datetime.fromisoformat(kickoff_raw)
        except ValueError:
            kickoff = None

    live_score = espn_client.get_live_score(home_team, away_team, league, kickoff)
    return jsonify({"generated_at": datetime.now().isoformat(), "live_score": live_score})


if __name__ == "__main__":
    app.run(debug=False, port=5000, use_reloader=False)
