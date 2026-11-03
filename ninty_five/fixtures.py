"""Automatic fixtures and results from football-data.org (free API key).

Get a key at https://www.football-data.org/client/register (or reuse the
token the sweepstakes app uses) and put it in a `.env` file in the project
root (git-ignored):

    FOOTBALL_DATA_TOKEN=your-token

or export it as an environment variable. FOOTBALL_DATA_API_KEY is accepted
as an alias.

The free tier covers these competitions, which together form the "EUROPE"
club data set: Champions League plus the domestic leagues most CL clubs come
from. Champions League matches link the leagues so cross-league matches can
be priced (see `DixonColes` group strengths).

Free tier rate limit: 10 requests/minute. Finished seasons are cached on disk
forever; the current season is re-downloaded at most every `max_age_hours`.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path

import pandas as pd

from .data import COLUMNS, DATA_DIR

BASE_URL = "https://api.football-data.org/v4"

# Domestic leagues -> group label used by the model
LEAGUES = {
    "PL": "England",
    "ELC": "England 2",
    "BL1": "Germany",
    "PD": "Spain",
    "SA": "Italy",
    "FL1": "France",
    "DED": "Netherlands",
    "PPL": "Portugal",
}
CUPS = {"CL": "Champions League"}
COMPETITIONS = {**LEAGUES, **CUPS}

# Bets are settled on 90 minutes; these stages can go to extra time.
FINISHED = {"FINISHED", "AWARDED"}
UPCOMING = {"SCHEDULED", "TIMED"}

CACHE = DATA_DIR / "football-data-org"
_MIN_INTERVAL = 6.5  # seconds between uncached requests (10/min limit)
_last_request = 0.0


class ApiKeyMissing(RuntimeError):
    pass


KEY_NAMES = ("FOOTBALL_DATA_TOKEN", "FOOTBALL_DATA_API_KEY")
ENV_FILE = Path(__file__).resolve().parent.parent / ".env"


def api_key() -> str:
    for name in KEY_NAMES:
        if os.environ.get(name):
            return os.environ[name]
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text().splitlines():
            k, _, v = line.partition("=")
            if k.strip() in KEY_NAMES and v.strip():
                return v.strip().strip('"').strip("'")
    raise ApiKeyMissing(
        "No football-data.org token. Add FOOTBALL_DATA_TOKEN=... to a .env file in "
        "the project root (free key: https://www.football-data.org/client/register)."
    )


def _get(path: str, params: dict | None = None) -> dict:
    global _last_request
    query = "&".join(f"{k}={v}" for k, v in (params or {}).items())
    url = f"{BASE_URL}{path}" + (f"?{query}" if query else "")
    wait = _MIN_INTERVAL - (time.monotonic() - _last_request)
    if wait > 0:
        time.sleep(wait)
    req = urllib.request.Request(url, headers={"X-Auth-Token": api_key()})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                _last_request = time.monotonic()
                return json.loads(resp.read())
        except urllib.error.HTTPError as exc:
            _last_request = time.monotonic()
            if exc.code == 429 and attempt < 2:  # rate limited: back off
                time.sleep(15 * (attempt + 1))
                continue
            raise
    raise RuntimeError("unreachable")


def current_season(today: pd.Timestamp | None = None) -> int:
    """football-data.org labels a season by its start year (2026 = 2026/27)."""
    today = today or pd.Timestamp.today()
    return today.year if today.month >= 7 else today.year - 1


# ----------------------------------------------------------------- parsing
def _goals_90(m: dict) -> tuple[int | None, int | None]:
    """Score after 90 minutes. fullTime includes extra time and penalty
    shootouts; regularTime is present but null on ordinary matches. So: use
    regularTime when it carries a value, else fullTime."""
    score = m.get("score") or {}
    rt = score.get("regularTime") or {}
    if rt.get("home") is not None:
        return rt.get("home"), rt.get("away")
    ft = score.get("fullTime") or {}
    return ft.get("home"), ft.get("away")


def parse_matches(payload: dict, default_competition: str | None = None) -> pd.DataFrame:
    rows = []
    for m in payload.get("matches", []):
        comp = (m.get("competition") or {}).get("code") or default_competition
        hg, ag = _goals_90(m)
        rows.append(
            {
                "match_id": m["id"],
                "kickoff": pd.Timestamp(m["utcDate"]),
                "code": comp,
                "competition": COMPETITIONS.get(comp, comp),
                "stage": m.get("stage", ""),
                "status": m.get("status", ""),
                "home": (m.get("homeTeam") or {}).get("name"),
                "away": (m.get("awayTeam") or {}).get("name"),
                "hg": hg,
                "ag": ag,
            }
        )
    df = pd.DataFrame(rows)
    if df.empty:
        return pd.DataFrame(
            columns=["match_id", "kickoff", "code", "competition", "stage", "status", "home", "away", "hg", "ag"]
        )
    df["kickoff"] = pd.to_datetime(df["kickoff"], utc=True)
    return df.dropna(subset=["home", "away"])


# ----------------------------------------------------------- history/cache
def season_matches(code: str, season: int, max_age_hours: float = 6.0, refresh: bool = False) -> pd.DataFrame:
    CACHE.mkdir(parents=True, exist_ok=True)
    path = CACHE / f"{code}_{season}.json"
    is_current = season >= current_season()
    stale = (
        not path.exists()
        or refresh
        or (is_current and time.time() - path.stat().st_mtime > max_age_hours * 3600)
    )
    if stale:
        try:
            payload = _get(f"/competitions/{code}/matches", {"season": season})
            path.write_text(json.dumps(payload))
        except urllib.error.HTTPError as exc:
            if not path.exists():
                if exc.code in (403, 404):  # season not available on this plan
                    return parse_matches({})
                raise
    return parse_matches(json.loads(path.read_text()), default_competition=code)


def load_europe(n_seasons: int = 3, refresh: bool = False) -> tuple[pd.DataFrame, dict[str, str]]:
    """Training data for the EUROPE data set plus team -> league groups."""
    cur = current_season()
    frames = []
    for season in range(cur - n_seasons + 1, cur + 1):
        for code in COMPETITIONS:
            frames.append(season_matches(code, season, refresh=refresh))
    all_m = pd.concat(frames, ignore_index=True).drop_duplicates("match_id")
    done = all_m[all_m["status"].isin(FINISHED)].dropna(subset=["hg", "ag"])
    if done.empty:
        raise RuntimeError("No finished matches returned by football-data.org")

    df = pd.DataFrame(
        {
            "date": pd.to_datetime(done["kickoff"], utc=True).dt.tz_localize(None),
            "home": done["home"],
            "away": done["away"],
            "hg": done["hg"].astype(int),
            "ag": done["ag"].astype(int),
            "neutral": done["stage"].eq("FINAL"),
            "competition": done["competition"],
        }
    ).sort_values("date").reset_index(drop=True)[COLUMNS]

    # A club's group is the domestic league it played most recently.
    dom = done[done["code"].isin(LEAGUES)].sort_values("kickoff")
    groups: dict[str, str] = {}
    for r in dom.itertuples():
        groups[r.home] = LEAGUES[r.code]
        groups[r.away] = LEAGUES[r.code]
    return df, groups


def upcoming(days: int = 7, competitions: list[str] | None = None) -> pd.DataFrame:
    """Scheduled matches in the next `days` days (API allows <= 10 per call)."""
    start = pd.Timestamp.today().normalize()
    codes = ",".join(competitions or COMPETITIONS)
    frames = []
    for offset in range(0, days, 10):
        a = start + pd.Timedelta(days=offset)
        b = start + pd.Timedelta(days=min(offset + 10, days))
        payload = _get("/matches", {"dateFrom": a.date(), "dateTo": b.date(), "competitions": codes})
        frames.append(parse_matches(payload))
    df = pd.concat(frames, ignore_index=True).drop_duplicates("match_id")
    return df[df["status"].isin(UPCOMING)].sort_values("kickoff").reset_index(drop=True)


def recent_results(days: int = 7) -> pd.DataFrame:
    """Finished matches over the last `days` days, for auto-settling bets."""
    end = pd.Timestamp.today().normalize() + pd.Timedelta(days=1)
    start = end - pd.Timedelta(days=min(days, 10))
    payload = _get(
        "/matches", {"dateFrom": start.date(), "dateTo": end.date(), "competitions": ",".join(COMPETITIONS)}
    )
    df = parse_matches(payload)
    return df[df["status"].isin(FINISHED)].dropna(subset=["hg", "ag"])
