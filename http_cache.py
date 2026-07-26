"""Generic on-disk JSON cache wrapper around requests, shared by every scraping/API client."""

import json
import re
import time
from datetime import datetime, timedelta
from pathlib import Path

import requests

CACHE_DIR = Path(__file__).parent / "cache"


class FetchError(Exception):
    """Raised when a URL can't be fetched and there's no usable cached fallback."""


def _cache_path(key: str) -> Path:
    safe = re.sub(r"[^a-zA-Z0-9]+", "_", key).strip("_")
    return CACHE_DIR / f"{safe}.json"


def fetch_text(session: requests.Session, url: str, ttl_seconds: int, delay: float = 0.3) -> str:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = _cache_path(url)

    if path.exists():
        try:
            cached = json.loads(path.read_text())
            fetched_at = datetime.fromisoformat(cached["fetched_at"])
            if datetime.now() - fetched_at < timedelta(seconds=ttl_seconds):
                return cached["body"]
        except (json.JSONDecodeError, KeyError, ValueError):
            pass

    try:
        if delay:
            time.sleep(delay)
        resp = session.get(url, timeout=15)
        resp.raise_for_status()
    except requests.RequestException as exc:
        if path.exists():
            try:
                return json.loads(path.read_text())["body"]
            except (json.JSONDecodeError, KeyError):
                pass
        raise FetchError(f"Could not fetch {url}: {exc}") from exc

    body = resp.text
    path.write_text(json.dumps({"fetched_at": datetime.now().isoformat(), "body": body}))
    return body


def fetch_json(session: requests.Session, url: str, ttl_seconds: int, delay: float = 0.3):
    return json.loads(fetch_text(session, url, ttl_seconds, delay))


def new_session(user_agent: str) -> requests.Session:
    import os

    session = requests.Session()
    session.headers.update({"User-Agent": user_agent})
    ca_bundle = os.environ.get("REQUESTS_CA_BUNDLE") or os.environ.get("SSL_CERT_FILE")
    if ca_bundle and os.path.exists(ca_bundle):
        session.verify = ca_bundle
    return session
