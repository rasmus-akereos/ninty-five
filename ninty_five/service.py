"""Shared glue: fit/cache models per data set and build the fixture board."""

from __future__ import annotations

import threading
import time

import pandas as pd

from . import data as data_mod
from . import fixtures
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


def board(days: int = 7, min_edge: float = 0.08) -> list[dict]:
    """Upcoming EUROPE fixtures with model prices and the minimum odds worth
    betting at:  min_odds = (1 + min_edge) / p_model."""
    model = get_model("EUROPE")
    rows = []
    for f in fixtures.upcoming(days).itertuples():
        neutral = f.stage == "FINAL"
        row = {
            "match_id": int(f.match_id),
            "kickoff": f.kickoff.isoformat(),
            "competition": f.competition,
            "stage": f.stage,
            "home": f.home,
            "away": f.away,
            "neutral": neutral,
        }
        try:
            lh, la = model.expected_goals(f.home, f.away, neutral)
        except KeyError:
            rows.append({**row, "rated": False})
            continue
        probs = markets(model.score_matrix(f.home, f.away, neutral))
        rows.append(
            {
                **row,
                "rated": True,
                "xg": [lh, la],
                "data_matches": [model.n_matches.get(f.home, 0), model.n_matches.get(f.away, 0)],
                "leagues": [model.groups.get(f.home, ""), model.groups.get(f.away, "")],
                "probabilities": {k: probs[k] for k in BOARD_MARKETS},
                "min_odds": {k: (1 + min_edge) / probs[k] for k in BOARD_MARKETS},
            }
        )
    return rows


def results_frame(days: int = 7) -> pd.DataFrame:
    return fixtures.recent_results(days)
