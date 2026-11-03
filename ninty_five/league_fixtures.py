"""Lower-league fixtures and results from football-data.co.uk (no key).

Fixtures come from two files the site publishes roughly twice a week, just
before each round, holding only the next few days' matches:

* fixtures.csv            main-league format: Div, Date, Time, HomeTeam, AwayTeam, ...
* new_league_fixtures.csv extra-league format: Country, League, Date, Time, Home, Away, ...

Results come from the same season files the country models train on, so team
names match the models with no mapping. Times are UK local time.
"""

from __future__ import annotations

import io
import time

import pandas as pd

from . import data
from .data import _fetch

FIXTURES_URL = "https://www.football-data.co.uk/fixtures.csv"
NEW_FIXTURES_URL = "https://www.football-data.co.uk/new_league_fixtures.csv"

# Division code -> board label for the leagues this module adds to the board.
NEW_DIVISIONS = {
    "E2": "England 3",
    "E3": "England 4",
    "D2": "Germany 2",
    "SP2": "Spain 2",
    "I2": "Italy 2",
    "F2": "France 2",
    "DNK": "Denmark 1",
}
EXTRA_LEAGUES = {("Denmark", "Superliga"): "DNK"}  # (Country, League) in the extra format

UK = "Europe/London"
CACHE_SECONDS = 3600
RESULT_WINDOW = pd.Timedelta(days=30)
COLUMNS = ["division", "kickoff", "home", "away"]

_cache: dict[str, tuple[float, object]] = {}


def _memo(name: str, load):
    hit = _cache.get(name)
    if hit and time.time() - hit[0] < CACHE_SECONDS:
        return hit[1]
    value = load()
    _cache[name] = (time.time(), value)
    return value


def _read(raw: bytes) -> pd.DataFrame:
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw.decode("latin-1")
    return pd.read_csv(io.StringIO(text), on_bad_lines="skip", dtype=str)


def _kickoffs(df: pd.DataFrame) -> pd.Series:
    local = pd.to_datetime(df["Date"].str.strip() + " " + df["Time"].fillna("15:00").str.strip(),
                           dayfirst=True, format="mixed")
    return local.dt.tz_localize(UK, ambiguous="NaT", nonexistent="shift_forward").dt.tz_convert("UTC")


def _frame(division: pd.Series, df: pd.DataFrame, home: str, away: str) -> pd.DataFrame:
    out = pd.DataFrame({"division": division, "kickoff": _kickoffs(df),
                        "home": df[home].str.strip(), "away": df[away].str.strip()})
    return out.dropna().reset_index(drop=True)[COLUMNS]


def parse_main(raw: bytes) -> pd.DataFrame:
    """The new divisions' rows of fixtures.csv."""
    df = _read(raw)
    df = df[df["Div"].str.strip().isin(NEW_DIVISIONS)].reset_index(drop=True)
    return _frame(df["Div"].str.strip(), df, "HomeTeam", "AwayTeam")


def parse_extra(raw: bytes) -> pd.DataFrame:
    """The Danish Superliga rows of new_league_fixtures.csv."""
    df = _read(raw)
    codes = [EXTRA_LEAGUES.get((str(c).strip(), str(l).strip())) for c, l in zip(df["Country"], df["League"])]
    df = df.assign(_div=codes)
    df = df[df["_div"].notna()].reset_index(drop=True)
    return _frame(df["_div"], df, "Home", "Away")


def match_key(division: str, kickoff_utc: pd.Timestamp, home: str, away: str) -> str:
    """fdcu:<division>:<UK kick-off date>:<home>:<away> (the files carry no id)."""
    day = pd.Timestamp(kickoff_utc).tz_convert(UK).strftime("%Y-%m-%d")
    return f"fdcu:{division}:{day}:{home}:{away}"


def _all_fixtures() -> pd.DataFrame:
    main = _memo("fixtures", lambda: parse_main(_fetch(FIXTURES_URL)))
    extra = _memo("new_fixtures", lambda: parse_extra(_fetch(NEW_FIXTURES_URL)))
    return pd.concat([main, extra], ignore_index=True)


def upcoming(days: int, now: pd.Timestamp | None = None) -> pd.DataFrame:
    """New-league matches with now <= kick-off < start of today + days."""
    now = now or pd.Timestamp.now(tz="UTC")
    df = _all_fixtures()
    df = df[(df["kickoff"] >= now) & (df["kickoff"] < now.normalize() + pd.Timedelta(days=days))].copy()
    df["key"] = [match_key(r.division, r.kickoff, r.home, r.away) for r in df.itertuples()]
    df["dataset"] = df["division"].map(data.DIVISION_COUNTRY)
    df["competition"] = df["division"].map(NEW_DIVISIONS)
    return df.sort_values("kickoff").reset_index(drop=True)


def results(division: str) -> pd.DataFrame:
    """A division's results (data.COLUMNS), current season freshly downloaded."""
    return _memo(f"results:{division}",
                 lambda: data.load_club_league(division, n_seasons=1, refresh_current=True))


def match_result(res: pd.DataFrame, home: str, away: str, kickoff: pd.Timestamp) -> tuple[int, int] | None:
    """The result of home v away played closest to `kickoff`, within 30 days
    (so a match moved to another day still finds its result)."""
    k = pd.Timestamp(kickoff)
    k = k.tz_convert(None) if k.tzinfo else k
    games = res[(res["home"] == home) & (res["away"] == away)]
    if games.empty:
        return None
    gap = (pd.to_datetime(games["date"]) - k.normalize()).abs()
    best = gap.idxmin()
    if gap[best] > RESULT_WINDOW:
        return None
    g = games.loc[best]
    return int(g["hg"]), int(g["ag"])
