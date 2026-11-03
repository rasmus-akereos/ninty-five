"""Shared glue: fit/cache models per data set and build the fixture board."""

from __future__ import annotations

import threading
import time

import pandas as pd

from . import data as data_mod
from . import fixtures, intl_fixtures
from .model import CLUB_PARAMS, INTL_PARAMS, DixonColes, markets

BOARD_MARKETS = ["1", "X", "2", "Over 2.5", "Under 2.5", "BTTS Yes", "BTTS No"]
MODEL_TTL_SECONDS = 6 * 3600

_models: dict[str, tuple[float, DixonColes]] = {}
_lock = threading.Lock()


def datasets() -> dict[str, str]:
    return {"EUROPE": "Europe: Champions League + top leagues (auto fixtures)", **data_mod.available_datasets()}


def fit_dataset(key: str, refresh: bool = False) -> DixonColes:
    key = key.upper()
    if key == "EUROPE":
        df, groups = fixtures.load_europe(refresh=refresh)
        return DixonColes(**CLUB_PARAMS).fit(df, groups=groups)
    df = data_mod.load_dataset(key, refresh=refresh)
    return DixonColes(**(INTL_PARAMS if key == "INTL" else CLUB_PARAMS)).fit(df)


def get_model(key: str, refresh: bool = False) -> DixonColes:
    key = key.upper()
    with _lock:
        cached = _models.get(key)
        # EUROPE data updates during the season, so refit periodically.
        expired = cached and key == "EUROPE" and time.time() - cached[0] > MODEL_TTL_SECONDS
        if refresh or cached is None or expired:
            _models[key] = (time.time(), fit_dataset(key, refresh=refresh))
        return _models[key][1]


def _priced(model: DixonColes, row: dict) -> dict:
    """Add model prices and minimum odds to a fixture row (or mark it unrated)."""
    home, away, neutral, min_edge = row["home"], row["away"], row["neutral"], row.pop("_min_edge")
    try:
        lh, la = model.expected_goals(home, away, neutral)
    except KeyError:
        return {**row, "rated": False}
    probs = markets(model.score_matrix(home, away, neutral))
    return {
        **row,
        "rated": True,
        "xg": [lh, la],
        "data_matches": [model.n_matches.get(home, 0), model.n_matches.get(away, 0)],
        "leagues": [model.groups.get(home, ""), model.groups.get(away, "")],
        "probabilities": {k: probs[k] for k in BOARD_MARKETS},
        "min_odds": {k: (1 + min_edge) / probs[k] for k in BOARD_MARKETS},
    }


def club_board(days: int, min_edge: float) -> list[dict]:
    model = get_model("EUROPE")
    rows = []
    for f in fixtures.upcoming(days).itertuples():
        row = {
            "dataset": "EUROPE",
            "match_id": int(f.match_id),
            "kickoff": f.kickoff.isoformat(),
            "competition": f.competition,
            "detail": "",
            "stage": f.stage,
            "home": f.home,
            "away": f.away,
            "neutral": f.stage == "FINAL",
            "_min_edge": min_edge,
        }
        rows.append(_priced(model, row))
    return rows


def intl_board(days: int, min_edge: float) -> list[dict]:
    """National-team matches. They have no football-data.org match id, so bets
    on them are settled by hand (match_id None)."""
    model = get_model("INTL")
    rows = []
    for f in intl_fixtures.upcoming(days).itertuples():
        row = {
            "dataset": "INTL",
            "match_id": None,
            "kickoff": f.kickoff.isoformat(),
            "competition": "International",
            "detail": f.league,
            "stage": "",
            "home": f.home,
            "away": f.away,
            "neutral": bool(f.neutral),
            "_min_edge": min_edge,
        }
        rows.append(_priced(model, row))
    return rows


def board(days: int = 7, min_edge: float = 0.08) -> tuple[list[dict], list[str]]:
    """Upcoming club and national-team fixtures with model prices and the
    minimum odds worth betting at:  min_odds = (1 + min_edge) / p_model.

    Returns (rows, warnings). A source that fails becomes a warning so the
    other still shows; if both fail, the first error is raised."""
    rows: list[dict] = []
    warnings: list[str] = []
    errors: list[Exception] = []
    for name, build in (("Club fixtures", club_board), ("National-team fixtures", intl_board)):
        try:
            rows += build(days, min_edge)
        except Exception as exc:
            errors.append(exc)
            warnings.append(f"{name} unavailable: {exc}")
    if len(errors) == 2:
        raise errors[0]
    rows.sort(key=lambda r: r["kickoff"])
    return rows, warnings


def results_frame(days: int = 7) -> pd.DataFrame:
    return fixtures.recent_results(days)
