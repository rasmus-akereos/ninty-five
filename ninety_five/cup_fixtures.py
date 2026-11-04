"""Domestic cups and the Danish Superliga from ESPN, priced by the country models.

Only matches between two clubs the model knows are listed: a team counts as
known when it appears in ESPN's own league scoreboards for that country (the
divisions the country model covers) and maps to a model team. Non-league and
lower-division clubs ("Oxford City", a 3. Liga side) are skipped without a
warning. A league club whose ESPN name can't be mapped is skipped with a
warning naming it, so `ALIASES` can be extended. Results come from ESPN and
settle on the 90-minute score (see intl_fixtures.goals_90).
"""

from __future__ import annotations

import pandas as pd

from . import intl_fixtures
from .league_fixtures import resolve_name

# ESPN slug -> (board label, country model)
CUPS = {
    "eng.fa": ("FA Cup", "ENG"),
    "eng.league_cup": ("Carabao Cup", "ENG"),
    "ger.dfb_pokal": ("DFB-Pokal", "GER"),
    "esp.copa_del_rey": ("Copa del Rey", "ESP"),
    "ita.coppa_italia": ("Coppa Italia", "ITA"),
    "fra.coupe_de_france": ("Coupe de France", "FRA"),
}
# Leagues whose fixtures come from ESPN rather than football-data.co.uk
# (its Danish fixtures arrive too late to bet on).
ESPN_LEAGUES = {"den.1": ("Denmark 1", "DEN")}
ESPN_SLUGS = {**CUPS, **ESPN_LEAGUES}
# The ESPN leagues each country model covers (its known clubs).
LEAGUE_SLUGS = {
    "DEN": ["den.1"],
    "ENG": ["eng.1", "eng.2", "eng.3", "eng.4"],
    "GER": ["ger.1", "ger.2"],
    "ESP": ["esp.1", "esp.2"],
    "ITA": ["ita.1", "ita.2"],
    "FRA": ["fra.1", "fra.2"],
}
# (country, ESPN name) -> model (football-data.co.uk) name, where the matcher
# can't tell. Checked 2026-11-03 against every ESPN league club.
ALIASES = {
    ("ENG", "Bristol Rovers"): "Bristol Rvs",
    ("ENG", "Nottingham Forest"): "Nott'm Forest",
    ("ENG", "Sheffield Wednesday"): "Sheffield Weds",
    ("ENG", "Wolverhampton Wanderers"): "Wolves",
    ("GER", "Borussia Mönchengladbach"): "M'gladbach",
    ("GER", "FC Cologne"): "FC Koln",
    ("ESP", "Athletic Club"): "Ath Bilbao",
    ("ESP", "Atlético Madrid"): "Ath Madrid",
    ("ESP", "Celta Vigo"): "Celta",
    ("ESP", "RC Celta Fortuna"): "Celta B",
    ("ESP", "Deportivo"): "La Coruna",
    ("FRA", "Paris Saint-Germain"): "Paris SG",
    ("FRA", "Saint-Étienne"): "St Etienne",
    ("FRA", "Stade Rennais"): "Rennes",
    ("DEN", "AGF"): "Aarhus",
    ("DEN", "F.C. København"): "FC Copenhagen",
}


def model_teams(country: str) -> list[str]:
    """Teams the country model has seen in the last year."""
    from . import service  # service imports this module

    df = service.training_data(country)
    recent = df[pd.to_datetime(df["date"]) >= pd.to_datetime(df["date"]).max() - pd.DateOffset(years=1)]
    return sorted(set(recent["home"]) | set(recent["away"]))


def league_clubs(country: str, now: pd.Timestamp) -> set[str]:
    """ESPN names of the clubs in the country's covered leagues (this month
    and last, so a month without league games still knows everyone)."""
    names: set[str] = set()
    for slug in LEAGUE_SLUGS[country]:
        for month in intl_fixtures._months(now - pd.DateOffset(months=1), now):
            df = intl_fixtures.parse_scoreboard(intl_fixtures._get(slug, month), club=True)
            names |= set(df["home"]) | set(df["away"])
    return names


def model_name(name: str, country: str, teams: list[str], clubs: set[str]) -> str | None:
    """The model team for an ESPN club name, or None (not a covered league club,
    or no single match)."""
    if name not in clubs:
        return None
    if (country, name) in ALIASES:
        return ALIASES[(country, name)]
    return resolve_name(name, country, teams)


def upcoming(days: int = 7, now: pd.Timestamp | None = None) -> tuple[pd.DataFrame, list[str]]:
    """Cup matches between known clubs in the window, with model names, plus
    warnings for covered league clubs the model can't be matched to."""
    now = now or pd.Timestamp.now(tz="UTC")
    fx = intl_fixtures.upcoming(days, now, slugs=list(ESPN_SLUGS), club=True)
    rows, warnings = [], []
    known: dict[str, tuple[list[str], set[str]]] = {}
    for f in fx.itertuples():
        label, country = ESPN_SLUGS[f.slug]
        if country not in known:
            known[country] = (model_teams(country), league_clubs(country, now))
        teams, clubs = known[country]
        home, away = (model_name(n, country, teams, clubs) for n in (f.home, f.away))
        for espn_name, got in ((f.home, home), (f.away, away)):
            msg = f"{label}: no model team for {espn_name} (add it to cup_fixtures.ALIASES)"
            if got is None and espn_name in clubs and msg not in warnings:
                warnings.append(msg)
        if home and away:
            rows.append({"key": f"espn:{f.event_id}", "dataset": country, "competition": label,
                         "kickoff": f.kickoff, "home": home, "away": away, "neutral": bool(f.neutral)})
    cols = ["key", "dataset", "competition", "kickoff", "home", "away", "neutral"]
    return pd.DataFrame(rows, columns=cols), warnings


REAL_UPCOMING = upcoming  # tests replace `upcoming` with an offline stub


def results(since: pd.Timestamp, now: pd.Timestamp | None = None) -> pd.DataFrame:
    """90-minute cup and Superliga results (event_id, hg, ag, status) from ESPN."""
    return intl_fixtures.results(since, now, slugs=list(ESPN_SLUGS))
