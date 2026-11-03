"""Upcoming national-team fixtures from ESPN's public scoreboard JSON.

football-data.org's free tier only carries national teams at the World Cup
and Euros, so Nations League, qualifiers and friendlies come from here. The
endpoint is undocumented and keyless: one request per competition per month,
cached in memory for an hour. Team names are mapped to the spellings used by
the martj42 international results (the INTL data set) so the model can price
them.

ESPN's date-range form (dates=YYYYMMDD-YYYYMMDD) is broken; the month form
(dates=YYYYMM) works, so we fetch whole months and filter locally.
"""

from __future__ import annotations

import gzip
import json
import time
import urllib.request

import pandas as pd

BASE_URL = "https://site.api.espn.com/apis/site/v2/sports/soccer/{slug}/scoreboard"
USER_AGENT = "ninty-five/0.1"

# Senior men's national-team competitions. Many are empty in a given month;
# an empty answer costs one small request.
SLUGS = [
    "uefa.nations",
    "fifa.friendly",
    "fifa.worldq.uefa",
    "fifa.worldq.conmebol",
    "fifa.worldq.concacaf",
    "fifa.worldq.afc",
    "fifa.worldq.caf",
    "fifa.worldq.ofc",
    "fifa.wcq.ply",
    "uefa.euroq",
    "concacaf.nations.league",
    "concacaf.nations.league_qual",
    "concacaf.gold_qual",
    "caf.nations_qual",
    "afc.cupq",
]

# ESPN team name -> INTL data set name
TEAM_ALIASES = {
    "Bosnia-Herzegovina": "Bosnia and Herzegovina",
    "Chinese Taipei": "Taiwan",
    "Congo DR": "DR Congo",
    "Czechia": "Czech Republic",
    "Kyrgyz Republic": "Kyrgyzstan",
    "St. Kitts and Nevis": "Saint Kitts and Nevis",
    "St. Lucia": "Saint Lucia",
    "St. Martin": "Saint Martin",
    "St. Vincent and the Grenadines": "Saint Vincent and the Grenadines",
    "Türkiye": "Turkey",
    "US Virgin Islands": "United States Virgin Islands",
}
# ESPN venue country -> INTL team name, where they differ
COUNTRY_ALIASES = {
    "USA": "United States",
    "China PR": "China",
    "Cape Verde Islands": "Cape Verde",
    "Comoros Islands": "Comoros",
    "St Kitts and Nevis": "Saint Kitts and Nevis",
}
UK_NATIONS = {"England", "Scotland", "Wales", "Northern Ireland"}

SCHEDULED = {"STATUS_SCHEDULED"}
_TTL = 3600
_cache: dict[tuple[str, str], tuple[float, dict]] = {}


def team_name(name: str) -> str:
    return TEAM_ALIASES.get(name, name)


def is_neutral(home: str, venue_country: str | None) -> bool:
    """ESPN has no reliable neutral flag, so compare the venue's country with
    the home team. Unknown venue -> assume a real home game."""
    if not venue_country:
        return False
    country = team_name(COUNTRY_ALIASES.get(venue_country, venue_country))
    if country == home:
        return False
    if country == "Congo" and home in ("Congo", "DR Congo"):  # ESPN uses "Congo" for both
        return False
    if country in ("United Kingdom", "UK") and home in UK_NATIONS:
        return False
    return True


def _get(slug: str, month: str) -> dict:
    key = (slug, month)
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < _TTL:
        return hit[1]
    url = BASE_URL.format(slug=slug) + f"?dates={month}&limit=1000"
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept-Encoding": "gzip"})
    with urllib.request.urlopen(req, timeout=20) as resp:
        raw = resp.read()
        if resp.headers.get("Content-Encoding") == "gzip":
            raw = gzip.decompress(raw)
    payload = json.loads(raw)
    _cache[key] = (time.time(), payload)
    return payload


def parse_scoreboard(payload: dict, club: bool = False) -> pd.DataFrame:
    """Events of one scoreboard. National teams get INTL spellings and a
    neutral venue inferred from the venue country; clubs (`club=True`) keep
    ESPN's names and use ESPN's neutral-site flag."""
    leagues = payload.get("leagues") or [{}]
    league = leagues[0].get("name") or leagues[0].get("abbreviation") or "International"
    rows = []
    for e in payload.get("events", []):
        comp = (e.get("competitions") or [{}])[0]
        sides = {c.get("homeAway"): c for c in comp.get("competitors", [])}
        if "home" not in sides or "away" not in sides:
            continue
        home, away = sides["home"]["team"]["displayName"], sides["away"]["team"]["displayName"]
        if not club:
            home, away = team_name(home), team_name(away)
        venue = comp.get("venue") or e.get("venue") or {}
        country = (venue.get("address") or {}).get("country")
        neutral = bool(comp.get("neutralSite")) if club else is_neutral(home, country)
        rows.append(
            {
                "event_id": str(e["id"]),
                "kickoff": pd.Timestamp(e["date"]),
                "league": league,
                "home": home,
                "away": away,
                "neutral": neutral,
                "status": ((e.get("status") or {}).get("type") or {}).get("name", ""),
            }
        )
    cols = ["event_id", "kickoff", "league", "home", "away", "neutral", "status"]
    df = pd.DataFrame(rows, columns=cols)
    df["kickoff"] = pd.to_datetime(df["kickoff"], utc=True)
    return df


def _months(start: pd.Timestamp, end: pd.Timestamp) -> list[str]:
    return [p.strftime("%Y%m") for p in pd.period_range(start.tz_localize(None).to_period("M"), end.tz_localize(None).to_period("M"), freq="M")]


def upcoming(days: int = 7, now: pd.Timestamp | None = None, slugs: list[str] | None = None,
             club: bool = False) -> pd.DataFrame:
    """Scheduled national-team matches from now until `days` days ahead.
    Competitions that fail to load are skipped; if all fail, raise."""
    now = now or pd.Timestamp.now(tz="UTC")
    end = now.normalize() + pd.Timedelta(days=days)
    frames, errors = [], []
    for slug in slugs or SLUGS:
        for month in _months(now, end):
            try:
                frames.append(parse_scoreboard(_get(slug, month), club=club).assign(slug=slug))
            except Exception as exc:  # one competition down shouldn't hide the rest
                errors.append(f"{slug} {month}: {exc}")
    if not frames:
        what = "cup" if club else "national-team"
        raise RuntimeError(f"Could not load {what} fixtures from ESPN: " + "; ".join(errors[:3]))
    df = pd.concat(frames, ignore_index=True).drop_duplicates("event_id")
    df = df[df["status"].isin(SCHEDULED) & (df["kickoff"] >= now) & (df["kickoff"] < end)]
    return df.sort_values("kickoff").reset_index(drop=True)


# ------------------------------------------------------------------ results
FULL_TIME = "STATUS_FULL_TIME"
EXTRA_TIME = {"STATUS_FINAL_AET", "STATUS_FINAL_PEN"}
CALLED_OFF = {"STATUS_CANCELED", "STATUS_POSTPONED", "STATUS_ABANDONED"}


def _minute(display: str) -> int | None:
    """"90'+3'" -> 90, "105'" -> 105."""
    head = (display or "").split("'")[0].strip()
    return int(head) if head.isdigit() else None


def goals_90(event: dict) -> tuple[int, int] | None:
    """90-minute score of a finished event, or None if it isn't finished or
    can't be determined safely. Bets settle on 90 minutes, but ESPN's score
    includes extra time, so for AET/penalty games count goals up to 90'+.
    Own goals in those games are skipped entirely (None): which side ESPN
    credits them to is unverified, and no result beats a wrong one."""
    status = ((event.get("status") or {}).get("type") or {}).get("name", "")
    comp = (event.get("competitions") or [{}])[0]
    sides = {c.get("homeAway"): c for c in comp.get("competitors", [])}
    if "home" not in sides or "away" not in sides:
        return None
    if status == FULL_TIME:
        try:
            return int(sides["home"]["score"]), int(sides["away"]["score"])
        except (KeyError, TypeError, ValueError):
            return None
    if status not in EXTRA_TIME:
        return None
    try:
        final = (int(sides["home"]["score"]), int(sides["away"]["score"]))
    except (KeyError, TypeError, ValueError):
        return None
    team_side = {sides["home"]["team"]["id"]: 0, sides["away"]["team"]["id"]: 1}
    goals, total = [0, 0], [0, 0]
    for d in comp.get("details") or []:
        if not d.get("scoringPlay") or d.get("shootout"):
            continue
        if d.get("ownGoal"):
            return None
        minute = _minute((d.get("clock") or {}).get("displayValue", ""))
        side = team_side.get((d.get("team") or {}).get("id"))
        if minute is None or side is None:
            return None
        total[side] += 1
        if minute <= 90:
            goals[side] += 1
    # The goal list must account for the whole final score (extra time
    # included); if ESPN's list is incomplete, a 90-minute score would be a guess.
    if tuple(total) != final:
        return None
    return goals[0], goals[1]


def results(since: pd.Timestamp, now: pd.Timestamp | None = None, slugs: list[str] | None = None) -> pd.DataFrame:
    """Finished or called-off national-team events between `since` and `now`:
    event_id, hg, ag (90 minutes; NaN when undetermined), status."""
    now = now or pd.Timestamp.now(tz="UTC")
    rows, errors, ok = [], [], False
    for slug in slugs or SLUGS:
        for month in _months(since, now):
            try:
                payload = _get(slug, month)
            except Exception as exc:
                errors.append(f"{slug} {month}: {exc}")
                continue
            ok = True
            for e in payload.get("events", []):
                kickoff = pd.Timestamp(e["date"])
                status = ((e.get("status") or {}).get("type") or {}).get("name", "")
                if not (since <= kickoff <= now) or status not in {FULL_TIME, *EXTRA_TIME, *CALLED_OFF}:
                    continue
                g = goals_90(e)
                rows.append({"event_id": str(e["id"]), "hg": g[0] if g else None, "ag": g[1] if g else None,
                             "status": status})
    if not ok:
        raise RuntimeError("Could not load national-team results from ESPN: " + "; ".join(errors[:3]))
    df = pd.DataFrame(rows, columns=["event_id", "hg", "ag", "status"])
    return df.drop_duplicates("event_id").reset_index(drop=True)
