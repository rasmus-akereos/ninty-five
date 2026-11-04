import os

import pytest

import pandas as pd

from ninty_five import cup_fixtures, league_fixtures, uefa

os.environ.setdefault("NINTY_FIVE_SCHEDULER", "0")  # no daily-fetch thread in tests

EMPTY = {
    league_fixtures.FIXTURES_URL: b"Div,Date,Time,HomeTeam,AwayTeam\n",
}


@pytest.fixture(autouse=True)
def offline_lower_league_fixtures(monkeypatch):
    """Tests never download the lower-league fixtures files: by default both are empty."""
    monkeypatch.setattr(league_fixtures, "_fetch", lambda url: EMPTY[url])
    monkeypatch.setattr(league_fixtures, "_division_teams", lambda division: None)  # exact names only
    # no cup matches unless a test provides them (cup tests use cup_fixtures.REAL_UPCOMING)
    monkeypatch.setattr(cup_fixtures, "upcoming", lambda days=7, now=None: (pd.DataFrame(
        columns=["key", "dataset", "competition", "kickoff", "home", "away", "neutral"]), []))
    # no European matches unless a test provides them (tests use uefa.REAL_UPCOMING)
    monkeypatch.setattr(uefa, "upcoming", lambda days=7, now=None: (pd.DataFrame(columns=uefa.BOARD_COLUMNS), []))
    league_fixtures._cache.clear()
    yield
    league_fixtures._cache.clear()
