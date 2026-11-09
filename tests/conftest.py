import os

import pytest

import pandas as pd

from ninety_five import cup_fixtures, league_fixtures, uefa

os.environ.setdefault("NINETY_FIVE_SCHEDULER", "0")  # no daily-fetch thread in tests

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


@pytest.fixture(autouse=True)
def offline_sharp_odds(monkeypatch):
    """Tests never call The Odds API: no key unless a test sets one, and any
    request fails loudly."""
    from ninety_five import sharp_odds

    monkeypatch.setattr(sharp_odds, "api_key", lambda: None)

    def no_network(path, params):
        raise AssertionError("test tried to call The Odds API")

    monkeypatch.setattr(sharp_odds, "_get", no_network)


@pytest.fixture(autouse=True)
def imports_in_tmp(tmp_path, monkeypatch):
    """Imported Danske Spil pages are kept in a temporary folder in tests."""
    from ninety_five import import_odds

    monkeypatch.setattr(import_odds, "IMPORT_DIR", tmp_path / "imports")
