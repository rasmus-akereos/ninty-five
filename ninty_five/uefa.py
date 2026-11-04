"""The UEFA club model: Europa League and Conference League.

Trained on 20 domestic top flights (football-data.co.uk) plus three seasons
of Champions, Europa and Conference League results, qualifiers included
(ESPN). European matches link the leagues' strengths. Clubs from countries
without domestic data are rated from their European results alone
("European only"); the board tags their matches "thin data".
"""

from __future__ import annotations

import json
import logging

import pandas as pd

from . import data, intl_fixtures

log = logging.getLogger("ninty_five")

# football-data.co.uk code -> (country key, label)
UEFA_LEAGUES: dict[str, tuple[str, str]] = {
    "E0": ("ENG", "England 1"),
    "D1": ("GER", "Germany 1"),
    "SP1": ("ESP", "Spain 1"),
    "I1": ("ITA", "Italy 1"),
    "F1": ("FRA", "France 1"),
    "N1": ("NED", "Netherlands 1"),
    "P1": ("POR", "Portugal 1"),
    "B1": ("BEL", "Belgium 1"),
    "SC0": ("SCO", "Scotland 1"),
    "T1": ("TUR", "Turkey 1"),
    "G1": ("GRE", "Greece 1"),
    "AUT": ("AUT", "Austria 1"),
    "SWZ": ("SUI", "Switzerland 1"),
    "DNK": ("DEN", "Denmark 1"),
    "NOR": ("NOR", "Norway 1"),
    "SWE": ("SWE", "Sweden 1"),
    "POL": ("POL", "Poland 1"),
    "ROU": ("ROU", "Romania 1"),
    "IRL": ("IRL", "Ireland 1"),
    "FIN": ("FIN", "Finland 1"),
}
N_SEASONS = 3
EUROPEAN_ONLY = "European only"


def _window_start(now: pd.Timestamp, start: pd.Timestamp | None, n_seasons: int) -> pd.Timestamp:
    """First date of training data: n_seasons years before `start` (validation)
    or before now."""
    ref = pd.Timestamp(start) if start is not None else pd.Timestamp(now)
    ref = ref.tz_convert(None) if ref.tzinfo else ref
    return ref - pd.DateOffset(years=n_seasons)


def load_domestic(n_seasons: int = N_SEASONS, refresh_current: bool = False, start: pd.Timestamp | None = None,
                  now: pd.Timestamp | None = None) -> tuple[pd.DataFrame, dict[str, str], dict[str, list[str]]]:
    """Domestic results (competition = league label), team -> label of the
    league it played most recently, and country -> teams seen in that
    league's last year of data. A league that fails to load is skipped."""
    now = pd.Timestamp(now) if now is not None else pd.Timestamp.today()
    now = now.tz_convert(None) if now.tzinfo else now
    first = _window_start(now, start, n_seasons)
    load_seasons = n_seasons + 1 + max(0, now.year - first.year - n_seasons)
    frames, teams = [], {}
    for code, (country, label) in UEFA_LEAGUES.items():
        try:
            df = data.load_club_league(code, n_seasons=load_seasons, refresh_current=refresh_current)
        except Exception as exc:
            log.warning("UEFA model: %s results unavailable: %s", code, exc)
            continue
        df = df[(df["date"] >= first) & (df["date"] <= now)].assign(competition=label)
        if df.empty:
            continue
        frames.append(df)
        recent = df[df["date"] >= df["date"].max() - pd.DateOffset(years=1)]
        teams[country] = sorted(set(recent["home"]) | set(recent["away"]))
    if not frames:
        raise RuntimeError("No domestic results for the UEFA model")
    out = pd.concat(frames, ignore_index=True).sort_values("date", kind="stable").reset_index(drop=True)
    groups: dict[str, str] = {}
    for r in out.itertuples():
        groups[r.home] = r.competition
        groups[r.away] = r.competition
    return out[data.COLUMNS], groups, teams


# ------------------------------------------------------------ European results
BOARD_SLUGS = {"uefa.europa": "Europa League", "uefa.europa.conf": "Conference League"}
TRAIN_SLUGS = {
    "uefa.champions": "Champions League",
    **BOARD_SLUGS,
    "uefa.champions_qual": "Champions League qualifying",
    "uefa.europa_qual": "Europa League qualifying",
    "uefa.europa.conf_qual": "Conference League qualifying",
}
ESPN_CACHE = data.DATA_DIR / "espn"


def _month_payload(slug: str, month: str, current: str, refresh_current: bool) -> dict:
    """A scoreboard month. Finished months are kept on disk for good; the
    current month always comes from ESPN (re-read when `refresh_current`)."""
    path = ESPN_CACHE / f"{slug}_{month}.json"
    if month < current and path.exists():
        return json.loads(path.read_text())
    if month >= current and refresh_current:
        intl_fixtures._cache.pop((slug, month), None)
    payload = intl_fixtures._get(slug, month)
    if month < current:
        ESPN_CACHE.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload))
    return payload


def european_results(start: pd.Timestamp, end: pd.Timestamp,
                     refresh_current: bool = False) -> tuple[pd.DataFrame, list[str]]:
    """Finished European matches between `start` and `end` with 90-minute
    scores (data.COLUMNS + event_id, qualifier), and warnings for months that
    could not be loaded."""
    start, end = pd.Timestamp(start), pd.Timestamp(end)
    start = start if start.tzinfo else start.tz_localize("UTC")
    end = end if end.tzinfo else end.tz_localize("UTC")
    current = end.strftime("%Y%m")
    rows, warnings = [], []
    for slug, label in TRAIN_SLUGS.items():
        for month in intl_fixtures._months(start, end):
            try:
                payload = _month_payload(slug, month, current, refresh_current)
            except Exception as exc:
                warnings.append(f"UEFA: could not load {slug} {month}: {exc}")
                continue
            for e in payload.get("events", []):
                score = intl_fixtures.goals_90(e)
                if score is None:
                    continue
                comp = (e.get("competitions") or [{}])[0]
                sides = {c.get("homeAway"): c for c in comp.get("competitors", [])}
                rows.append({
                    "date": pd.Timestamp(e["date"]).tz_convert(None),
                    "home": sides["home"]["team"]["displayName"],
                    "away": sides["away"]["team"]["displayName"],
                    "hg": score[0], "ag": score[1],
                    "neutral": bool(comp.get("neutralSite")),
                    "competition": label,
                    "event_id": str(e["id"]),
                    "qualifier": slug.endswith("_qual"),
                })
    cols = data.COLUMNS + ["event_id", "qualifier"]
    df = pd.DataFrame(rows, columns=cols).drop_duplicates("event_id")
    lo, hi = start.tz_convert(None), end.tz_convert(None)
    df = df[(df["date"] >= lo) & (df["date"] <= hi)]
    return df.sort_values("date", kind="stable").reset_index(drop=True), warnings
