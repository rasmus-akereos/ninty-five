from pathlib import Path

import pandas as pd
import pytest

from ninety_five import league_fixtures

DATA = Path(__file__).parent / "data"
SAMPLE_MAIN = (DATA / "fdcu_fixtures.csv").read_bytes()
REAL_DIVISION_TEAMS = league_fixtures._division_teams  # conftest replaces it per test


def uk(s):
    return pd.Timestamp(s, tz="Europe/London").tz_convert("UTC")


def _res(rows):
    return pd.DataFrame(
        [(pd.Timestamp(d), h, a, hg, ag, False, "England 3") for d, h, a, hg, ag in rows],
        columns=["date", "home", "away", "hg", "ag", "neutral", "competition"],
    )


def test_parse_main_keeps_new_divisions_and_converts_uk_time():
    df = league_fixtures.parse_main(SAMPLE_MAIN)
    assert set(df["division"]) == {"E2", "E3", "D2"}  # E1 (Championship) and B1 dropped
    r = df[df.home == "Wigan"].iloc[0]
    assert r.away == "Mansfield" and r.division == "E2"
    assert r.kickoff == uk("2026-11-03 19:45")
    assert str(r.kickoff.tz) == "UTC"


def test_parse_main_summer_time():
    r = league_fixtures.parse_main(SAMPLE_MAIN).query("home == 'Hertha'").iloc[0]
    assert r.kickoff == pd.Timestamp("2026-08-15 14:00", tz="UTC")  # 15:00 BST


def test_match_key_uses_uk_date():
    # 23:30 UTC on 1 Aug is 00:30 on 2 Aug in the UK
    key = league_fixtures.match_key("E2", pd.Timestamp("2026-08-01 23:30", tz="UTC"), "Wigan", "Bolton")
    assert key == "fdcu:E2:2026-08-02:Wigan:Bolton"


def test_upcoming_window_and_labels(monkeypatch):
    fetched = []
    monkeypatch.setattr(league_fixtures, "_fetch", lambda url: fetched.append(url) or SAMPLE_MAIN)
    league_fixtures._cache.clear()
    df = league_fixtures.upcoming(7, now=pd.Timestamp("2026-11-03 12:00", tz="UTC"))
    assert "Hertha" not in set(df["home"])  # in the past
    assert fetched == [league_fixtures.FIXTURES_URL]  # Danish fixtures come from ESPN
    assert "DNK" not in set(df["division"])
    e3 = df[df.division == "E3"].iloc[0]
    assert e3.competition == "England 4" and e3.dataset == "ENG"
    later = league_fixtures.upcoming(1, now=pd.Timestamp("2026-11-03 12:00", tz="UTC"))
    assert "Oxford Utd" not in set(later["home"])  # 4 Nov is beyond a 1-day window


def test_match_result_moved_match():
    res = _res([("2026-11-14", "Wigan", "Bolton", 2, 1)])
    assert league_fixtures.match_result(res, "Wigan", "Bolton", pd.Timestamp("2026-11-08T15:00Z")) == (2, 1)


def test_match_result_none_beyond_30_days_or_other_pair():
    res = _res([("2026-12-20", "Wigan", "Bolton", 2, 1), ("2026-11-08", "Bolton", "Wigan", 0, 0)])
    assert league_fixtures.match_result(res, "Wigan", "Bolton", pd.Timestamp("2026-11-08T15:00Z")) is None


def test_match_result_picks_closest_of_two_meetings():
    res = _res([("2026-09-01", "Brondby", "Aarhus", 1, 0), ("2026-10-05", "Brondby", "Aarhus", 3, 3)])
    assert league_fixtures.match_result(res, "Brondby", "Aarhus", pd.Timestamp("2026-10-04T16:00Z")) == (3, 3)
    assert league_fixtures.match_result(res, "Brondby", "Aarhus", pd.Timestamp("2026-09-03T16:00Z")) == (1, 0)


def test_results_uses_current_season_refresh(monkeypatch):
    seen = {}

    def fake(code, **kw):
        seen.update(code=code, **kw)
        return _res([("2026-11-01", "A", "B", 1, 0)])

    monkeypatch.setattr(league_fixtures.data, "load_club_league", fake)
    league_fixtures._cache.clear()
    df = league_fixtures.results("E2")
    assert seen == {"code": "E2", "n_seasons": 2, "refresh_current": True} and len(df) == 1
    league_fixtures.results("E2")  # cached: no second download
    assert seen["code"] == "E2"


ENG3 = ["Oxford", "Reading", "Sheffield Weds", "Wigan", "Mansfield"]
ENG2 = ["Sheffield United", "Sheffield Weds", "Stoke"]


def test_resolve_name_exact_and_short_forms():
    assert league_fixtures.resolve_name("Wigan", "E2", ENG3) == "Wigan"
    assert league_fixtures.resolve_name("Oxford Utd", "E2", ENG3) == "Oxford"
    assert league_fixtures.resolve_name("Sheffield Utd", "E1", ENG2) == "Sheffield United"
    assert league_fixtures.resolve_name("KV Mechelen", "B1", ["Mechelen", "Genk"]) == "Mechelen"


def test_resolve_name_ambiguous_or_unknown_is_none():
    assert league_fixtures.resolve_name("Sheffield", "E1", ENG2) is None  # United or Weds
    assert league_fixtures.resolve_name("Newcomers FC", "E2", ENG3) is None


def test_resolve_name_alias_table(monkeypatch):
    monkeypatch.setitem(league_fixtures.ALIASES, ("E2", "Peterborough United"), "Peterboro")
    assert league_fixtures.resolve_name("Peterborough United", "E2", ["Peterboro", "Reading"]) == "Peterboro"


def test_upcoming_resolves_names_and_flags_unknown(monkeypatch):
    main = b"Div,Date,Time,HomeTeam,AwayTeam\nE2,04/11/2026,19:45,Oxford Utd,Reading\nE2,04/11/2026,19:45,Newcomers FC,Wigan\n"
    monkeypatch.setattr(league_fixtures, "_fetch", lambda url: main)
    monkeypatch.setattr(league_fixtures, "_division_teams", lambda div: ENG3)
    df = league_fixtures.upcoming(7, now=pd.Timestamp("2026-11-03 12:00", tz="UTC"))
    ox = df[df.away == "Reading"].iloc[0]
    assert ox.home == "Oxford" and ox.key == "fdcu:E2:2026-11-04:Oxford:Reading" and ox.unmatched == ""
    nc = df[df.away == "Wigan"].iloc[0]
    assert nc.home == "Newcomers FC" and nc.unmatched == "Newcomers FC"


def test_division_teams_from_current_results(monkeypatch):
    monkeypatch.setattr(league_fixtures, "results", lambda div: pd.DataFrame({"home": ["A", "B"], "away": ["C", "A"]}))
    assert sorted(league_fixtures._division_teams_live("E2")) == ["A", "B", "C"]


def test_match_result_ignores_meetings_before_first_listed():
    # Superliga: same home v away twice in a season. Only the earlier game is in
    # the file yet; it must not settle the later one.
    res = _res([("2027-03-10", "Midtjylland", "Brondby", 3, 0)])
    ko = pd.Timestamp("2027-03-31T17:00Z")
    assert league_fixtures.match_result(res, "Midtjylland", "Brondby", ko, not_before=pd.Timestamp("2027-03-28")) is None
    # a match moved to a later day still finds its result
    res2 = _res([("2027-04-07", "Midtjylland", "Brondby", 1, 1)])
    assert league_fixtures.match_result(res2, "Midtjylland", "Brondby", ko, not_before=pd.Timestamp("2027-03-28")) == (1, 1)


def test_division_teams_unavailable_fails_the_source(monkeypatch):
    def down(div):
        raise OSError("site down")

    monkeypatch.setattr(league_fixtures, "results", down)
    monkeypatch.setattr(league_fixtures, "_division_teams", REAL_DIVISION_TEAMS)
    monkeypatch.setattr(league_fixtures, "_fetch", lambda url: SAMPLE_MAIN)
    with pytest.raises(OSError):
        league_fixtures.upcoming(7, now=pd.Timestamp("2026-11-03 00:00", tz="UTC"))
