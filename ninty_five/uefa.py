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
from collections.abc import Iterable
from dataclasses import dataclass, field

import pandas as pd

from . import cup_fixtures, data, intl_fixtures
from .import_odds import COMMON_WORDS, _tokens
from .league_fixtures import resolve_name

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


# ------------------------------------------------------------ club names
# Country -> ESPN league scoreboard listing its clubs. SUI, ROU, FIN and POL
# have no usable ESPN scoreboard (checked 2026-11-04): their clubs resolve by
# the team lists alone.
LEAGUE_SLUGS = {
    "ENG": "eng.1", "GER": "ger.1", "ESP": "esp.1", "ITA": "ita.1", "FRA": "fra.1", "NED": "ned.1",
    "POR": "por.1", "BEL": "bel.1", "SCO": "sco.1", "TUR": "tur.1", "GRE": "gre.1", "AUT": "aut.1",
    "DEN": "den.1", "NOR": "nor.1", "SWE": "swe.1", "IRL": "irl.1",
}
# (country, ESPN name) -> football-data.co.uk name, where the matcher can't
# tell. Check with `ninty-five uefa-names`.
ALIASES: dict[tuple[str, str], str] = {
    **{k: v for k, v in cup_fixtures.ALIASES.items() if k[0] in {"ENG", "GER", "ESP", "FRA", "DEN"}},
    # Found with `ninty-five uefa-names` on 2026-11-04.
    ("ENG", "West Ham United"): "West Ham",
    ("GER", "1. FC Heidenheim 1846"): "Heidenheim",
    ("ESP", "Girona"): "Girona",
    ("POR", "Sporting CP"): "Sp Lisbon",
    ("BEL", "Cercle Brugge KSV"): "Cercle Brugge",
    ("BEL", "Sint-Truidense"): "St Truiden",
    ("TUR", "Istanbul Basaksehir"): "Buyuksehyr",
    ("IRL", "St Patrick's Athletic"): "St. Patricks",
    ("ROU", "CSU Craiova"): "Univ. Craiova",
    ("ROU", "Universitatea Cluj"): "U. Cluj",
}


@dataclass
class Resolution:
    mapping: dict[str, str] = field(default_factory=dict)  # ESPN name -> model name
    excluded: set[str] = field(default_factory=set)        # covered-country clubs not matched
    european_only: set[str] = field(default_factory=set)
    warnings: list[str] = field(default_factory=list)


def _resolve(name: str, country: str, teams: list[str]) -> str | None:
    """resolve_name, but a name made only of words many clubs share
    ("Dinamo", "Real", "City") must match a team exactly."""
    if not set(_tokens(name)) - COMMON_WORDS:
        return name if name in teams else None
    return resolve_name(name, country, teams)


def _words(name: str) -> set[str]:
    return {"united" if t == "utd" else t for t in _tokens(name.replace(".", " "))}


def _strict(name: str, teams: list[str]) -> str | None:
    """The one team all of whose words appear, exactly, in `name` (and not
    only words many clubs share), or None."""
    words = _words(name)
    hits = [t for t in teams if _words(t) <= words and _words(t) - COMMON_WORDS]
    return hits[0] if len(hits) == 1 else None


def _alias(name: str) -> str | None:
    return next((v for (_, n), v in ALIASES.items() if n == name), None)


def resolve_clubs(names: Iterable[str], teams: dict[str, list[str]],
                  league_clubs: dict[str, set[str]]) -> Resolution:
    """Map ESPN European club names to domestic team names. A club listed in
    a covered country's ESPN league resolves within that country only; any
    other club must match exactly one team in exactly one country's list.
    Ambiguous or unmatched league clubs are excluded (with a warning); clubs
    matching nothing are European-only and keep their ESPN name."""
    out = Resolution()
    for name in sorted(set(names)):
        alias = _alias(name)
        if alias is not None:
            out.mapping[name] = alias
            continue
        homes = [c for c, clubs in league_clubs.items() if name in clubs]
        if homes:
            got = _resolve(name, homes[0], teams.get(homes[0], [])) if len(homes) == 1 else None
        else:
            # Not in any ESPN league list: only countries ESPN doesn't list
            # can claim it, and only strictly (short domestic names are too
            # often part of a foreign club's name: "AEK" in "AEK Larnaca").
            hits = {c: t for c in teams if c not in league_clubs and (t := _strict(name, teams[c])) is not None}
            if not hits:
                out.mapping[name] = name
                out.european_only.add(name)
                continue
            got = next(iter(hits.values())) if len(hits) == 1 else None
        if got is None:
            out.excluded.add(name)
            out.warnings.append(f"UEFA: no domestic team for {name} (add it to uefa.ALIASES)")
        else:
            out.mapping[name] = got
    return out


def espn_league_clubs(now: pd.Timestamp) -> dict[str, set[str]]:
    """ESPN names of each covered league's clubs (this month and last). A
    league whose scoreboard fails or is empty is left out."""
    out: dict[str, set[str]] = {}
    for country, slug in LEAGUE_SLUGS.items():
        names: set[str] = set()
        for month in intl_fixtures._months(now - pd.DateOffset(months=1), now):
            try:
                df = intl_fixtures.parse_scoreboard(intl_fixtures._get(slug, month), club=True)
            except Exception as exc:
                log.warning("UEFA: %s %s unavailable: %s", slug, month, exc)
                continue
            names |= set(df["home"]) | set(df["away"])
        if names:
            out[country] = names
    return out


# ------------------------------------------------------------ the data set
last_resolution: Resolution | None = None
last_inputs: tuple[dict[str, list[str]], dict[str, set[str]]] | None = None


def load_uefa(refresh_current: bool = False, now: pd.Timestamp | None = None,
              start: pd.Timestamp | None = None) -> tuple[pd.DataFrame, dict[str, str], Resolution]:
    """Training data for the UEFA data set: domestic and European results
    (European names mapped to domestic ones; matches of excluded clubs
    dropped), team -> group, and the name resolution."""
    global last_resolution, last_inputs
    now = pd.Timestamp(now) if now is not None else pd.Timestamp.now(tz="UTC")
    now = now if now.tzinfo else now.tz_localize("UTC")
    dom, groups, _ = load_domestic(refresh_current=refresh_current, start=start, now=now)
    # Resolve against every team of the window (a club relegated since still counts).
    label_country = {label: country for country, label in UEFA_LEAGUES.values()}
    teams: dict[str, list[str]] = {}
    for label, part in dom.groupby("competition"):
        teams[label_country[label]] = sorted(set(part["home"]) | set(part["away"]))
    first = _window_start(now.tz_convert(None), start, N_SEASONS)
    eu, warnings = european_results(first, now, refresh_current=refresh_current)
    clubs = espn_league_clubs(now)
    res = resolve_clubs(set(eu["home"]) | set(eu["away"]), teams, clubs)
    res.warnings = warnings + res.warnings
    eu = eu[~eu["home"].isin(res.excluded) & ~eu["away"].isin(res.excluded)]
    eu = eu.assign(home=eu["home"].map(res.mapping), away=eu["away"].map(res.mapping))
    groups = {**groups, **{c: EUROPEAN_ONLY for c in res.european_only if c not in groups}}
    df = pd.concat([dom, eu[data.COLUMNS]], ignore_index=True).sort_values("date", kind="stable")
    last_resolution, last_inputs = res, (teams, clubs)
    return df.reset_index(drop=True)[data.COLUMNS], groups, res
