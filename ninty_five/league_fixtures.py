"""Lower-league fixtures and results from football-data.co.uk (no key).

Fixtures come from two files the site publishes roughly twice a week, just
before each round, holding only the next few days' matches:

* fixtures.csv            main-league format: Div, Date, Time, HomeTeam, AwayTeam, ...
* new_league_fixtures.csv extra-league format: Country, League, Date, Time, Home, Away, ...

Results come from the same season files the country models train on. The
fixtures files mostly use the same team names, but not always ("Oxford Utd"
vs "Oxford", "Sheffield Utd" vs "Sheffield United"), so each fixture name is
resolved against the division's current teams. Times are UK local time.
"""

from __future__ import annotations

import io
import time

import pandas as pd

from . import data
from .data import _fetch
from .import_odds import _tokens, same_team

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

# (division, fixtures-file name) -> results-file name, for names the matcher
# can't resolve. Extend when the board warns about an unknown team name.
ALIASES: dict[tuple[str, str], str] = {}

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


RESERVE = {"ii", "b", "2"}  # reserve sides: "Real Sociedad II", "Celta B"


def _reserve(name: str) -> bool:
    toks = _tokens(name)
    return len(toks) > 1 and toks[-1] in RESERVE


def _norm(name: str) -> str:
    toks = _tokens(name)
    if _reserve(name):
        toks = toks[:-1]
    return " ".join("united" if t == "utd" else t for t in toks)


def resolve_name(name: str, division: str, teams) -> str | None:
    """The division's team (results-file spelling) a fixtures-file name means:
    the alias table, the exact name, or a single lenient match in either
    direction. None when no team or more than one fits."""
    teams = list(teams)
    if (division, name) in ALIASES:
        return ALIASES[(division, name)]
    if name in teams:
        return name
    n, reserve = _norm(name), _reserve(name)
    hits = {t for t in teams if _reserve(t) == reserve
            and (n == _norm(t) or same_team(n, _norm(t)) or same_team(_norm(t), n))}
    return hits.pop() if len(hits) == 1 else None


def _division_teams_live(division: str) -> list[str]:
    res = results(division)
    return sorted(set(res["home"]) | set(res["away"]))


def _division_teams(division: str) -> list[str] | None:
    """The division's teams over this season and last (so newly promoted or
    relegated clubs are known before their first match). Raises when the list
    can't be loaded: fixture names are never used unchecked. (Tests replace
    this with a stub returning None, meaning "use names as they are".)"""
    return _division_teams_live(division)


def _resolve(df: pd.DataFrame) -> pd.DataFrame:
    homes, aways, unmatched = [], [], []
    teams = {d: _division_teams(d) for d in df["division"].unique()}
    for r in df.itertuples():
        names, missing = [], []
        for n in (r.home, r.away):
            t = teams[r.division]
            got = n if t is None else resolve_name(n, r.division, t)
            names.append(got or n)
            if got is None:
                missing.append(n)
        homes.append(names[0])
        aways.append(names[1])
        unmatched.append(", ".join(missing))
    return df.assign(home=homes, away=aways, unmatched=unmatched)


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
    df = df[(df["kickoff"] >= now) & (df["kickoff"] < now.normalize() + pd.Timedelta(days=days))]
    df = _resolve(df.reset_index(drop=True)) if len(df) else df.assign(unmatched=[])
    df["key"] = [match_key(r.division, r.kickoff, r.home, r.away) for r in df.itertuples()]
    df["dataset"] = df["division"].map(data.DIVISION_COUNTRY)
    df["competition"] = df["division"].map(NEW_DIVISIONS)
    return df.sort_values("kickoff").reset_index(drop=True)


def results(division: str) -> pd.DataFrame:
    """A division's results (data.COLUMNS) for this season and last, the
    current season freshly downloaded."""
    return _memo(f"results:{division}",
                 lambda: data.load_club_league(division, n_seasons=2, refresh_current=True))


def match_result(res: pd.DataFrame, home: str, away: str, kickoff: pd.Timestamp,
                 not_before: pd.Timestamp | None = None) -> tuple[int, int] | None:
    """The result of home v away played closest to `kickoff`, within 30 days
    (so a match moved to another day still finds its result), and not before
    `not_before` (the day the match was first listed as upcoming: an earlier
    meeting of the same two teams, as after the Superliga split, is a
    different match)."""
    k = pd.Timestamp(kickoff)
    k = k.tz_convert(None) if k.tzinfo else k
    games = res[(res["home"] == home) & (res["away"] == away)]
    if not_before is not None:
        nb = pd.Timestamp(not_before)
        nb = nb.tz_convert(UK).tz_localize(None) if nb.tzinfo else nb
        games = games[pd.to_datetime(games["date"]) >= nb.normalize()]
    if games.empty:
        return None
    gap = (pd.to_datetime(games["date"]) - k.normalize()).abs()
    best = gap.idxmin()
    if gap[best] > RESULT_WINDOW:
        return None
    g = games.loc[best]
    return int(g["hg"]), int(g["ag"])
