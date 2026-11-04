import pandas as pd
import pytest

from ninty_five import service


def _df(rows):
    return pd.DataFrame(rows, columns=["date", "home", "away", "hg", "ag", "neutral", "competition"])


def test_form_last_five_before_kickoff_most_recent_first():
    df = _df([(pd.Timestamp(f"2026-10-{d:02d}"), "A", "B", 2, 1, False, "L") for d in range(1, 8)]
             + [(pd.Timestamp("2026-10-20"), "C", "A", 0, 0, True, "L"),
                (pd.Timestamp("2026-11-05"), "A", "D", 9, 0, False, "L")])  # after kick-off: excluded
    f = service.form(df, "A", before=pd.Timestamp("2026-11-01T20:00Z"))
    assert len(f) == 5
    assert f[0] == {"date": "2026-10-20", "opponent": "C", "venue": "N", "gf": 0, "ga": 0, "result": "D"}
    assert f[1] == {"date": "2026-10-07", "opponent": "B", "venue": "H", "gf": 2, "ga": 1, "result": "W"}
    g = service.form(df, "B", before=pd.Timestamp("2026-11-01T20:00Z"))
    assert g[0]["venue"] == "A" and g[0]["result"] == "L" and (g[0]["gf"], g[0]["ga"]) == (1, 2)


def test_form_unknown_team_is_empty():
    assert service.form(_df([]), "Nobody", before=pd.Timestamp("2026-11-01T00:00Z")) == []


def test_with_min_odds():
    rows = service.with_min_odds([{"rated": True, "probabilities": {m: 0.5 for m in service.BOARD_MARKETS}},
                                  {"rated": False}], 0.08)
    assert rows[0]["min_odds"]["1"] == pytest.approx(2.16) and "min_odds" not in rows[1]


# ------------------------------------------------------------ lower leagues
from ninty_five import fixtures, intl_fixtures, league_fixtures  # noqa: E402
from ninty_five.history import HistoryStore  # noqa: E402
from ninty_five.model import CLUB_PARAMS, DixonColes  # noqa: E402
from ninty_five.store import BetStore  # noqa: E402


def _eng_model():
    rows = []
    teams = ["Wigan", "Mansfield", "Bolton", "Luton"]
    for d in range(1, 29):
        h, a = teams[d % 4], teams[(d + 1) % 4]
        rows.append((pd.Timestamp(f"2026-10-{d:02d}"), h, a, d % 3, (d + 1) % 2, False, "England 3"))
    df = _df(rows)
    return DixonColes(**CLUB_PARAMS).fit(df), df


def _upcoming(rows):
    out = pd.DataFrame(rows, columns=["division", "kickoff", "home", "away"])
    out["key"] = [league_fixtures.match_key(r.division, r.kickoff, r.home, r.away) for r in out.itertuples()]
    out["dataset"] = "ENG"
    out["competition"] = "England 3"
    return out


def test_league_board_prices_with_country_model(monkeypatch):
    model, df = _eng_model()
    used = []
    monkeypatch.setattr(service, "_cached", lambda key, refresh=False: used.append(key) or (0, model, df))
    ko = pd.Timestamp("2026-11-03 19:45", tz="UTC")
    monkeypatch.setattr(league_fixtures, "upcoming", lambda days, now=None: _upcoming([("E2", ko, "Wigan", "Mansfield")]))
    rows = service.league_board(7)
    r = rows[0]
    assert r["key"] == "fdcu:E2:2026-11-03:Wigan:Mansfield" and r["dataset"] == "ENG" and r["rated"]
    assert r["competition"] == "England 3" and r["match_id"] is None and r["neutral"] is False
    assert used == ["ENG"] and r["form"]["home"]


def test_league_board_unknown_team_unrated(monkeypatch):
    model, df = _eng_model()
    monkeypatch.setattr(service, "_cached", lambda key, refresh=False: (0, model, df))
    ko = pd.Timestamp("2026-11-03 19:45", tz="UTC")
    monkeypatch.setattr(league_fixtures, "upcoming", lambda days, now=None: _upcoming([("E2", ko, "Wigan", "Newcomers")]))
    assert service.league_board(7)[0]["rated"] is False


def _ok(name):
    return lambda days: [{"key": name, "kickoff": "2026-11-04T19:00:00+00:00"}]


def _fail(days):
    raise RuntimeError("down")


def test_build_board_lower_league_failure_is_warning(monkeypatch):
    monkeypatch.setattr(service, "club_board", _ok("fd:1"))
    monkeypatch.setattr(service, "intl_board", _ok("espn:1"))
    monkeypatch.setattr(service, "league_board", _fail)
    rows, warnings = service.build_board(7)
    assert [r["key"] for r in rows] == ["fd:1", "espn:1"]
    assert warnings == ["Lower-league fixtures unavailable: down"]


def test_build_board_raises_only_when_all_sources_fail(monkeypatch):
    monkeypatch.setattr(service, "club_board", _fail)
    monkeypatch.setattr(service, "intl_board", _fail)
    monkeypatch.setattr(service, "cup_board", _fail)
    monkeypatch.setattr(service, "uefa_board", _fail)
    monkeypatch.setattr(service, "league_board", _ok("fdcu:x"))
    rows, warnings = service.build_board(7)
    assert [r["key"] for r in rows] == ["fdcu:x"] and len(warnings) == 4
    monkeypatch.setattr(service, "league_board", _fail)
    with pytest.raises(RuntimeError):
        service.build_board(7)


def _store_fdcu(tmp_path, kickoff):
    db = tmp_path / "b.sqlite"
    h, bets = HistoryStore(db), BetStore(db)
    key = league_fixtures.match_key("E2", pd.Timestamp(kickoff), "Wigan", "Bolton")
    h.save_board([{"key": key, "dataset": "ENG", "match_id": None, "kickoff": kickoff, "competition": "England 3",
                   "detail": "", "stage": "", "home": "Wigan", "away": "Bolton", "neutral": False, "rated": False}], "t")
    bid = bets.add({"dataset": "ENG", "home": "Wigan", "away": "Bolton", "market": "1", "odds": 2.0, "stake": 10,
                    "match_key": key})
    return h, bets, key, bid


def _no_other_sources(monkeypatch):
    monkeypatch.setattr(fixtures, "recent_results", lambda days=7: pd.DataFrame(columns=["match_id", "hg", "ag"]))
    monkeypatch.setattr(intl_fixtures, "results", lambda since, now=None, slugs=None: pd.DataFrame(
        columns=["event_id", "hg", "ag", "status"]))


def test_update_results_fdcu(tmp_path, monkeypatch):
    h, bets, key, bid = _store_fdcu(tmp_path, "2026-11-08T15:00:00+00:00")
    _no_other_sources(monkeypatch)
    asked = []
    monkeypatch.setattr(league_fixtures, "results", lambda div: asked.append(div) or pd.DataFrame(
        [(pd.Timestamp("2026-11-14"), "Wigan", "Bolton", 2, 1, False, "x")],
        columns=["date", "home", "away", "hg", "ag", "neutral", "competition"]))
    now = pd.Timestamp("2026-11-30T12:00Z")  # 22 days after kick-off: beyond the 14-day club window
    assert service.update_results(h, bets, now) == [bid]
    assert asked == ["E2"] and bets.list()[0]["status"] == "won"
    assert h.pending_results(now, lookback_days=31) == []


def test_update_results_fdcu_no_result_after_30_days(tmp_path, monkeypatch):
    h, bets, key, bid = _store_fdcu(tmp_path, "2026-10-01T15:00:00+00:00")
    _no_other_sources(monkeypatch)
    empty = pd.DataFrame(columns=["date", "home", "away", "hg", "ag", "neutral", "competition"])
    monkeypatch.setattr(league_fixtures, "results", lambda div: empty)
    service.update_results(h, bets, pd.Timestamp("2026-10-20T12:00Z"))  # 19 days: still waiting
    assert [p["key"] for p in h.pending_results(pd.Timestamp("2026-10-20T12:00Z"), lookback_days=31)] == [key]
    service.update_results(h, bets, pd.Timestamp("2026-11-01T12:00Z"))  # 31 days: give up
    assert h.pending_results(pd.Timestamp("2026-11-01T12:00Z"), lookback_days=31) == []
    assert bets.list()[0]["status"] == "open"


def test_build_board_warns_about_unknown_team_names(monkeypatch):
    monkeypatch.setattr(service, "club_board", lambda days: [])
    monkeypatch.setattr(service, "intl_board", lambda days: [])
    monkeypatch.setattr(service, "league_board", lambda days: [
        {"key": "fdcu:a", "kickoff": "2026-11-04T19:00:00+00:00", "competition": "England 3", "unmatched": "Newcomers FC"},
        {"key": "fdcu:b", "kickoff": "2026-11-04T19:00:00+00:00", "competition": "England 3", "unmatched": ""}])
    _, warnings = service.build_board(7)
    assert warnings == ["England 3: unknown team name Newcomers FC (match shown unrated)"]


def test_league_board_carries_unmatched(monkeypatch):
    model, df = _eng_model()
    monkeypatch.setattr(service, "_cached", lambda key, refresh=False: (0, model, df))
    up = _upcoming([("E2", pd.Timestamp("2026-11-03 19:45", tz="UTC"), "Wigan", "Newcomers")])
    up["unmatched"] = ["Newcomers"]
    monkeypatch.setattr(league_fixtures, "upcoming", lambda days, now=None: up)
    assert service.league_board(7)[0]["unmatched"] == "Newcomers"


def test_run_refresh_records_success_and_failure(tmp_path, monkeypatch):
    db = tmp_path / "b.sqlite"
    h, bets = HistoryStore(db), BetStore(db)
    monkeypatch.setattr(service, "refresh_board", lambda history, bets, days, now=None: {
        "warnings": ["w"], "auto_settled": [], "snapshots": 3, "matches": 12})
    out = service.run_refresh(h, bets, 7, "auto", now=pd.Timestamp("2026-11-03T06:00Z"))
    assert out["matches"] == 12
    run = h.last_run("auto")
    assert run["matches"] == 12 and run["warnings"] == ["w"] and run["error"] is None

    def broken(history, bets, days, now=None):
        raise RuntimeError("all down")

    monkeypatch.setattr(service, "refresh_board", broken)
    with pytest.raises(RuntimeError):
        service.run_refresh(h, bets, 7, "auto", now=pd.Timestamp("2026-11-03T07:00Z"))
    assert h.last_run("auto")["error"] == "all down"
    # the lease was released despite the failure
    assert h.take_lease("refresh", "x", pd.Timestamp("2026-11-03T07:01Z"))


def test_run_refresh_skips_when_leased(tmp_path, monkeypatch):
    db = tmp_path / "b.sqlite"
    h, bets = HistoryStore(db), BetStore(db)
    h.take_lease("refresh", "other", pd.Timestamp.now(tz="UTC"))
    monkeypatch.setattr(service, "refresh_board", lambda *a, **k: pytest.fail("must not fetch"))
    assert service.run_refresh(h, bets, 7, "manual") is None


def test_run_refresh_refits_country_models(tmp_path, monkeypatch):
    db = tmp_path / "b.sqlite"
    h, bets = HistoryStore(db), BetStore(db)
    refits = []
    monkeypatch.setattr(service, "_cached", lambda key, refresh=False: refits.append((key, refresh)))
    monkeypatch.setattr(service, "refresh_board", lambda history, bets, days, now=None: {
        "warnings": [], "auto_settled": [], "snapshots": 0, "matches": 0})
    service.run_refresh(h, bets, 7, "auto", refit_countries=True)
    assert refits == [(k, "current") for k in ["ENG", "GER", "ESP", "ITA", "FRA", "DEN", "UEFA"]]


def test_uefa_is_a_dataset_and_refits_daily(tmp_path, monkeypatch):
    from ninty_five import uefa

    db = tmp_path / "b.sqlite"
    h, bets = HistoryStore(db), BetStore(db)
    calls = []
    tiny = _df([(pd.Timestamp("2026-10-01"), "A", "B", 1, 0, False, "Belgium 1"),
                (pd.Timestamp("2026-10-08"), "B", "A", 2, 2, False, "Belgium 1")])

    def fake_load(refresh_current=False, now=None, start=None):
        calls.append(refresh_current)
        uefa.last_resolution = uefa.Resolution(warnings=["UEFA: no domestic team for X (add it to uefa.ALIASES)"])
        return tiny, {"A": "Belgium 1", "B": "Belgium 1"}, uefa.last_resolution

    monkeypatch.setattr(uefa, "load_uefa", fake_load)
    monkeypatch.setattr(service, "_models", {})
    assert "UEFA" in service.datasets()
    monkeypatch.setattr(service, "refresh_board", lambda history, bets, days, now=None: {
        "warnings": [], "auto_settled": [], "snapshots": 0, "matches": 0})
    monkeypatch.setattr(service.data_mod, "COUNTRIES", {})
    out = service.run_refresh(h, bets, 7, "auto", refit_countries=True)
    assert calls == [True]
    assert out["warnings"] == ["UEFA: no domestic team for X (add it to uefa.ALIASES)"]


def test_update_results_fdcu_ignores_earlier_meeting(tmp_path, monkeypatch):
    h, bets, key, bid = _store_fdcu(tmp_path, "2026-11-08T15:00:00+00:00")  # first seen "t" → set below
    with h._conn() as c:
        c.execute("UPDATE matches SET first_seen = '2026-11-05T07:00:00+00:00' WHERE key = ?", (key,))
    _no_other_sources(monkeypatch)
    monkeypatch.setattr(league_fixtures, "results", lambda div: pd.DataFrame(
        [(pd.Timestamp("2026-10-20"), "Wigan", "Bolton", 3, 0, False, "x")],
        columns=["date", "home", "away", "hg", "ag", "neutral", "competition"]))
    assert service.update_results(h, bets, pd.Timestamp("2026-11-09T07:00Z")) == []
    assert bets.list()[0]["status"] == "open"


# --------------------------------------------------------------------- cups
from ninty_five import cup_fixtures  # noqa: E402


def test_cup_board_prices_with_country_model_and_passes_warnings(monkeypatch):
    model, df = _eng_model()
    monkeypatch.setattr(service, "_cached", lambda key, refresh=False: (0, model, df))
    fx = pd.DataFrame([{"key": "espn:1", "dataset": "ENG", "competition": "FA Cup",
                        "kickoff": pd.Timestamp("2026-11-07 15:00", tz="UTC"), "home": "Wigan", "away": "Bolton",
                        "neutral": True}])
    monkeypatch.setattr(cup_fixtures, "upcoming", lambda days, now=None: (fx, ["FA Cup: no model team for X"]))
    rows, warnings = service.cup_board(7)
    r = rows[0]
    assert r["key"] == "espn:1" and r["competition"] == "FA Cup" and r["rated"] and r["neutral"] is True
    assert warnings == ["FA Cup: no model team for X"]


def test_build_board_includes_cup_source_and_its_warnings(monkeypatch):
    monkeypatch.setattr(service, "club_board", lambda days: [])
    monkeypatch.setattr(service, "intl_board", lambda days: [])
    monkeypatch.setattr(service, "league_board", lambda days: [])
    monkeypatch.setattr(service, "cup_board", lambda days: (
        [{"key": "espn:1", "kickoff": "2026-11-07T15:00:00+00:00", "competition": "FA Cup"}], ["cup warn"]))
    rows, warnings = service.build_board(7)
    assert [r["key"] for r in rows] == ["espn:1"] and warnings == ["cup warn"]


def test_update_results_settles_cup_bets_from_espn(tmp_path, monkeypatch):
    db = tmp_path / "b.sqlite"
    h, bets = HistoryStore(db), BetStore(db)
    h.save_board([{"key": "espn:77", "dataset": "ENG", "match_id": None, "kickoff": "2026-11-07T15:00:00+00:00",
                   "competition": "FA Cup", "detail": "", "stage": "", "home": "Wigan", "away": "Bolton",
                   "neutral": False, "rated": False}], "t")
    bid = bets.add({"dataset": "ENG", "home": "Wigan", "away": "Bolton", "market": "X", "odds": 3.4, "stake": 10,
                    "match_key": "espn:77"})
    monkeypatch.setattr(fixtures, "recent_results", lambda days=7: pd.DataFrame(columns=["match_id", "hg", "ag"]))
    asked = []

    def fake_results(since, now=None, slugs=None):
        asked.append(slugs)
        return pd.DataFrame([{"event_id": "77", "hg": 1, "ag": 1, "status": "STATUS_FINAL_AET"}])

    monkeypatch.setattr(intl_fixtures, "results", fake_results)
    assert service.update_results(h, bets, pd.Timestamp("2026-11-08T12:00Z")) == [bid]
    assert bets.list()[0]["status"] == "won"
    assert set(cup_fixtures.CUPS) <= set(asked[0])  # cup competitions are searched too


def test_run_refresh_failed_uefa_refit_warns_without_stale_name_warnings(tmp_path, monkeypatch):
    from ninty_five import uefa

    db = tmp_path / "b.sqlite"
    h, bets = HistoryStore(db), BetStore(db)
    monkeypatch.setattr(uefa, "last_resolution", uefa.Resolution(warnings=["UEFA: old warning"]))

    def cached(key, refresh=False):
        if key == "UEFA":
            raise RuntimeError("ESPN down")

    monkeypatch.setattr(service, "_cached", cached)
    monkeypatch.setattr(service.data_mod, "COUNTRIES", {})
    monkeypatch.setattr(service, "refresh_board", lambda history, bets, days, now=None: {
        "warnings": [], "auto_settled": [], "snapshots": 0, "matches": 0})
    out = service.run_refresh(h, bets, 7, "auto", refit_countries=True)
    assert out["warnings"] == ["Could not update UEFA data: ESPN down"]
