import pytest

from ninty_five import league_fixtures

EMPTY = {
    league_fixtures.FIXTURES_URL: b"Div,Date,Time,HomeTeam,AwayTeam\n",
    league_fixtures.NEW_FIXTURES_URL: b"Country,League,Date,Time,Home,Away\n",
}


@pytest.fixture(autouse=True)
def offline_lower_league_fixtures(monkeypatch):
    """Tests never download the lower-league fixtures files: by default both are empty."""
    monkeypatch.setattr(league_fixtures, "_fetch", lambda url: EMPTY[url])
    monkeypatch.setattr(league_fixtures, "_division_teams", lambda division: None)  # exact names only
    league_fixtures._cache.clear()
    yield
    league_fixtures._cache.clear()
