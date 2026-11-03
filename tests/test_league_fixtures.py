from pathlib import Path

import pandas as pd
import pytest

from ninty_five import league_fixtures

DATA = Path(__file__).parent / "data"
SAMPLE_MAIN = (DATA / "fdcu_fixtures.csv").read_bytes()
SAMPLE_EXTRA = (DATA / "fdcu_new_league_fixtures.csv").read_bytes()


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


def test_parse_extra_only_danish_superliga():
    df = league_fixtures.parse_extra(SAMPLE_EXTRA)
    assert list(df["division"]) == ["DNK"]
    r = df.iloc[0]
    assert (r.home, r.away) == ("Brondby", "FC Copenhagen") and r.kickoff == uk("2026-11-09 18:00")


def test_match_key_uses_uk_date():
    # 23:30 UTC on 1 Aug is 00:30 on 2 Aug in the UK
    key = league_fixtures.match_key("E2", pd.Timestamp("2026-08-01 23:30", tz="UTC"), "Wigan", "Bolton")
    assert key == "fdcu:E2:2026-08-02:Wigan:Bolton"


def test_upcoming_window_and_labels(monkeypatch):
    files = {league_fixtures.FIXTURES_URL: SAMPLE_MAIN, league_fixtures.NEW_FIXTURES_URL: SAMPLE_EXTRA}
    monkeypatch.setattr(league_fixtures, "_fetch", lambda url: files[url])
    league_fixtures._cache.clear()
    df = league_fixtures.upcoming(7, now=pd.Timestamp("2026-11-03 12:00", tz="UTC"))
    assert "Hertha" not in set(df["home"])  # in the past
    dk = df[df.division == "DNK"].iloc[0]
    assert dk.competition == "Denmark 1" and dk.dataset == "DEN"
    assert dk.key == "fdcu:DNK:2026-11-09:Brondby:FC Copenhagen"
    e3 = df[df.division == "E3"].iloc[0]
    assert e3.competition == "England 4" and e3.dataset == "ENG"
    later = league_fixtures.upcoming(1, now=pd.Timestamp("2026-11-03 12:00", tz="UTC"))
    assert "DNK" not in set(later["division"])  # beyond the window


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
    assert seen == {"code": "E2", "n_seasons": 1, "refresh_current": True} and len(df) == 1
    league_fixtures.results("E2")  # cached: no second download
    assert seen["code"] == "E2"
