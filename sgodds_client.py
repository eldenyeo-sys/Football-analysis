"""
Scrapes public, no-login pages on sgodds.com (a third-party site that mirrors
Singapore Pools' football odds) for upcoming fixtures/odds and recent
results/history. All pages fetched here are plain server-rendered HTML,
publicly viewable without an account, and allowed by robots.txt.
"""

import csv
import io
import re
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

import requests
from bs4 import BeautifulSoup

import http_cache
from http_cache import FetchError

BASE_URL = "https://sgodds.com"
CURRENT_ODDS_URL = f"{BASE_URL}/football/current-odds"
RESULTS_URL = f"{BASE_URL}/football/results-past-odds"
DATA_PAGE_URL = f"{BASE_URL}/football/data"

USER_AGENT = "football-analysis-dashboard/1.0 (personal informational use)"
REQUEST_DELAY_SECONDS = 0.5

_session = http_cache.new_session(USER_AGENT)


class SgoddsError(Exception):
    """Raised when sgodds.com can't be reached or its page structure can't be parsed."""


@dataclass
class Match:
    match_id: str
    league: str
    home_team: str
    away_team: str
    kickoff: Optional[datetime]
    is_live: bool
    detail_url: str
    odds_home: Optional[float]
    odds_draw: Optional[float]
    odds_away: Optional[float]


@dataclass
class ResultRow:
    league: str
    home_team: str
    away_team: str
    date: Optional[datetime]
    ht_score: Optional[str]
    ft_score: Optional[str]
    closing_odds_home: Optional[float]
    closing_odds_draw: Optional[float]
    closing_odds_away: Optional[float]


def _fetch_html(url: str, ttl_seconds: int) -> str:
    try:
        return http_cache.fetch_text(_session, url, ttl_seconds, delay=REQUEST_DELAY_SECONDS)
    except FetchError as exc:
        raise SgoddsError(str(exc)) from exc


def _parse_odds_row_container(soup: BeautifulSoup) -> list:
    """Finds the .my-3 table container and returns its direct child row divs."""
    header = soup.select_one(".row.table-dark.font-weight-bold")
    if header is None or header.parent is None:
        raise SgoddsError("Unexpected page structure: couldn't find the odds table header")
    return header.parent.find_all("div", recursive=False)


def _parse_date_header(row) -> Optional[datetime]:
    text = row.get_text(strip=True)
    try:
        return datetime.strptime(text, "%a, %d %b %Y")
    except ValueError:
        return None


def _parse_decimal_odds(cell) -> Optional[float]:
    strong = cell.find("strong")
    if strong is None:
        return None
    try:
        return float(strong.get_text(strip=True))
    except ValueError:
        return None


def get_source_last_updated() -> Optional[datetime]:
    """Parses the 'Last Updated on ...' timestamp sgodds prints on the current-odds
    page, so the UI can show how fresh the underlying Singapore Pools-sourced odds
    actually are (shares the same cached fetch as get_upcoming_matches)."""
    html = _fetch_html(CURRENT_ODDS_URL, ttl_seconds=90)
    match = re.search(r"Last Updated on (\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})", html)
    if not match:
        return None
    try:
        return datetime.strptime(match.group(1), "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None


def get_upcoming_matches(limit: int = 6) -> list:
    html = _fetch_html(CURRENT_ODDS_URL, ttl_seconds=90)
    soup = BeautifulSoup(html, "lxml")
    rows = _parse_odds_row_container(soup)

    matches = []
    current_date: Optional[datetime] = None

    for row in rows:
        classes = row.get("class", [])
        if "table-dark" in classes:
            continue
        if "table-active" in classes:
            current_date = _parse_date_header(row)
            continue
        if "border-bottom" not in classes:
            continue

        cells = row.find_all("div", recursive=False)
        if len(cells) < 6:
            continue
        time_cell, league_cell, fixture_cell, home_cell, draw_cell, away_cell = cells[:6]

        is_live = time_cell.select_one("i.fa-spin") is not None
        time_match = re.search(r"\d{1,2}:\d{2}", time_cell.get_text())
        kickoff = None
        if current_date is not None and time_match:
            hh, mm = map(int, time_match.group().split(":"))
            kickoff = current_date.replace(hour=hh, minute=mm)

        link = fixture_cell.find("a")
        badge = fixture_cell.find("span", class_="badge")
        if link is None:
            continue
        teams = link.get_text(strip=True)
        if " vs " not in teams:
            continue
        home_team, away_team = teams.split(" vs ", 1)

        matches.append(
            Match(
                match_id=badge.get_text(strip=True) if badge else teams,
                league=league_cell.get_text(strip=True),
                home_team=home_team.strip(),
                away_team=away_team.strip(),
                kickoff=kickoff,
                is_live=is_live,
                detail_url=link.get("href", ""),
                odds_home=_parse_decimal_odds(home_cell),
                odds_draw=_parse_decimal_odds(draw_cell),
                odds_away=_parse_decimal_odds(away_cell),
            )
        )

    # Live matches (no reliable kickoff time) sort first, then by kickoff time.
    matches.sort(key=lambda m: (not m.is_live, m.kickoff or datetime.max))
    return matches[:limit]


def _parse_results_page(html: str) -> list:
    soup = BeautifulSoup(html, "lxml")
    rows = _parse_odds_row_container(soup)

    results = []
    current_date: Optional[datetime] = None

    for row in rows:
        classes = row.get("class", [])
        if "table-dark" in classes:
            continue
        if "table-active" in classes:
            current_date = _parse_date_header(row)
            continue
        if "border-bottom" not in classes:
            continue

        cells = row.find_all("div", recursive=False)
        if len(cells) < 6:
            continue
        league_cell, fixture_cell, score_cell, home_cell, draw_cell, away_cell = cells[:6]

        link = fixture_cell.find("a")
        if link is None:
            continue
        teams = link.get_text(strip=True)
        if " vs " not in teams:
            continue
        home_team, away_team = teams.split(" vs ", 1)

        score_cols = score_cell.find_all("div", class_="col")
        ht_score = score_cols[0].get_text(strip=True) if len(score_cols) > 0 else None
        ft_score = score_cols[1].get_text(strip=True) if len(score_cols) > 1 else None
        if ft_score in ("", "-", None):
            ft_score = None

        results.append(
            ResultRow(
                league=league_cell.get_text(strip=True),
                home_team=home_team.strip(),
                away_team=away_team.strip(),
                date=current_date,
                ht_score=ht_score,
                ft_score=ft_score,
                closing_odds_home=_parse_decimal_odds(home_cell),
                closing_odds_draw=_parse_decimal_odds(draw_cell),
                closing_odds_away=_parse_decimal_odds(away_cell),
            )
        )

    return results


def get_results_pool(pages: int = 5) -> list:
    pool = []
    for page in range(1, pages + 1):
        url = RESULTS_URL if page == 1 else f"{RESULTS_URL}/page/{page}"
        try:
            html = _fetch_html(url, ttl_seconds=1800)
        except SgoddsError:
            if page == 1:
                raise
            break  # later pages are a bonus; don't fail the whole request for them
        pool.extend(_parse_results_page(html))
    return [r for r in pool if r.ft_score]  # only finished matches are useful for form/H2H


def get_season_csv_url(league: str) -> Optional[str]:
    try:
        html = _fetch_html(DATA_PAGE_URL, ttl_seconds=3600 * 12)
    except SgoddsError:
        return None
    soup = BeautifulSoup(html, "lxml")
    for row in soup.select("table tr"):
        cells = row.find_all("td")
        if not cells:
            continue
        if cells[0].get_text(strip=True).lower() == league.lower():
            link = row.find("a", attrs={"href": True})
            if link:
                href = link["href"]
                return href if href.startswith("http") else f"{BASE_URL}{href}"
    return None


def get_season_matches(league: str) -> list:
    """Best-effort deeper history for leagues that publish a season CSV."""
    csv_url = get_season_csv_url(league)
    if not csv_url:
        return []
    try:
        time.sleep(REQUEST_DELAY_SECONDS)
        resp = _session.get(csv_url, timeout=20)
        resp.raise_for_status()
    except requests.RequestException:
        return []

    rows = []
    reader = csv.DictReader(io.StringIO(resp.text))
    for row in reader:
        match_text = row.get("Match", "")
        if " vs " not in match_text:
            continue
        home_team, away_team = match_text.split(" vs ", 1)
        result = row.get("Result", "")
        ft_match = re.search(r"FT:(\d+)-(\d+)", result)
        if not ft_match:
            continue
        date = None
        try:
            date = datetime.strptime(row.get("Start Time", ""), "%Y-%m-%d %H:%M:%S")
        except ValueError:
            pass
        rows.append(
            ResultRow(
                league=league,
                home_team=home_team.strip(),
                away_team=away_team.strip(),
                date=date,
                ht_score=None,
                ft_score=f"{ft_match.group(1)}-{ft_match.group(2)}",
                closing_odds_home=None,
                closing_odds_draw=None,
                closing_odds_away=None,
            )
        )
    return rows
