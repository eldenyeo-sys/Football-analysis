# Football Match Analysis

A small local web app showing the current + next 5 upcoming football matches, with a heuristic
outcome prediction, confidence level, recent form, and head-to-head history for each.

## What this does and doesn't do

- Reads **public, no-login pages** on [sgodds.com](https://sgodds.com) — a third-party site that
  mirrors Singapore Pools' football odds (opening + current, refreshed roughly every 10 minutes)
  and publishes results/history. It does **not** log into Singapore Pools, does **not** touch any
  betting account, and does **not** place bets.
- The prediction/confidence shown is a simple, transparent heuristic (blend of the market's
  implied win probability from current odds + each team's recent form) — **not** a trained model
  and **not** betting advice.
- Odds are Singapore Pools' odds *as mirrored by sgodds.com*, not a live/official feed — always
  check Singapore Pools' own site before acting on any odds.
- This scrapes a public third-party website. It's built for personal, casual/informational use;
  if you plan to use it heavily or redistribute the data, check sgodds.com's own terms first.

## Setup

```bash
pip install -r requirements.txt
```

## Run

```bash
python app.py
```

Then open [http://localhost:5000](http://localhost:5000) in your browser.

## How it works

- `sgodds_client.py` scrapes `sgodds.com/football/current-odds` (fixtures + live odds) and
  `sgodds.com/football/results-past-odds` (recent results, ~5 weeks, for form/head-to-head),
  with an on-disk JSON cache (`cache/`) so repeated page loads don't re-scrape every time.
- `espn_client.py` is a best-effort fallback against ESPN's public, unauthenticated site API:
  used for live scores on in-play matches (sgodds only shows pre-match odds, not live scores),
  and for recent-form/head-to-head when sgodds' ~5-week window has no data for a team (common
  for big clubs deep in preseason friendlies). Degrades to "no data" silently if a team/league
  isn't found there either — it never blocks the rest of the dashboard.
- `analysis.py` turns odds into implied probabilities, scores each team's last 5 results into a
  form rating, blends the two, and labels the result High/Medium/Low confidence.
- `app.py` is a small Flask API (`GET /api/matches`) that the static frontend
  (`static/index.html` + `app.js`) fetches and renders, auto-refreshing every 20 seconds (with a
  visible countdown), with a client-side filter to show only High/Medium/Low confidence matches.

## Deploying online (Render)

No API keys or secrets needed. Steps:

1. Push this folder to a GitHub repo (public or private).
2. Create a free account at [render.com](https://render.com) and click **New > Web Service**,
   connect the GitHub repo.
3. Render should auto-detect Python. Set:
   - **Build command**: `pip install -r requirements.txt`
   - **Start command**: `gunicorn app:app` (already declared in `Procfile` too, so Render may
     pick it up automatically)
4. Deploy. Render gives you a public `https://<your-app>.onrender.com` URL.

Notes:
- The free tier spins down after ~15 min idle and takes a few seconds to wake back up on the
  next request — fine for personal use, not for an always-instant public tool.
- The on-disk `cache/` directory resets on every redeploy/restart. That's fine — it's a
  performance optimization, not real data; the app just re-scrapes on first request.
- If you outgrow the free tier or want it always-on with a persistent cache disk, look at
  [Fly.io](https://fly.io) instead (needs a `Dockerfile`, more setup).
- Avoid PythonAnywhere's free tier for this — its outbound network access is restricted to an
  allowlist of domains, which would block the sgodds.com/ESPN requests this app depends on.

## Responsible gambling

If gambling is affecting you or someone you know, call Singapore's National Problem Gambling
Helpline at **1800-6-668-668**.
