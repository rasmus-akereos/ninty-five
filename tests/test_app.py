import warnings

import numpy as np
import pandas as pd
import pytest

with warnings.catch_warnings():  # starlette's httpx deprecation notice on import
    warnings.simplefilter("ignore")
    from fastapi.testclient import TestClient

from ninety_five import app as app_mod
from ninety_five import service
from ninety_five.history import HistoryStore
from ninety_five.model import CLUB_PARAMS, DixonColes
from ninety_five.store import BetStore


def _model():
    rng = np.random.default_rng(0)
    teams = ["A", "B", "C", "D"]
    rows = []
    for i in range(120):
        h, a = rng.choice(teams, 2, replace=False)
        rows.append((pd.Timestamp("2026-01-01") + pd.Timedelta(days=i), h, a, rng.poisson(1.5), rng.poisson(1.1), False, "L"))
    df = pd.DataFrame(rows, columns=["date", "home", "away", "hg", "ag", "neutral", "competition"])
    return DixonColes(**CLUB_PARAMS).fit(df)


MODEL = _model()


def _row():
    kickoff = (pd.Timestamp.now(tz="UTC") + pd.Timedelta(days=1)).isoformat()
    return {
        "key": "fd:1", "dataset": "EUROPE", "match_id": 1, "kickoff": kickoff, "competition": "England 2",
        "detail": "", "stage": "", "home": "A", "away": "B", "neutral": False, "rated": True,
        "xg": [1.2, 1.0], "data_matches": [100, 100], "leagues": ["", ""], "model_as_of": "2026-11-02",
        "probabilities": {m: 0.5 for m in service.BOARD_MARKETS}, "form": {"home": [], "away": []},
    }


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(app_mod, "history", HistoryStore(tmp_path / "h.sqlite"))
    monkeypatch.setattr(app_mod, "store", BetStore(tmp_path / "h.sqlite"))
    monkeypatch.setattr(service, "get_model", lambda key, refresh=False: MODEL)
    monkeypatch.setattr(service, "update_results", lambda history, bets, now: [])
    return TestClient(app_mod.app)


def test_get_board_empty_db(client):
    r = client.get("/api/fixtures").json()
    assert r == {"fixtures": [], "sharp": {"enabled": False}, "fetched_at": None, "stale": True, "last_auto": None}


def test_refresh_then_get_serves_stored(client, monkeypatch):
    monkeypatch.setattr(service, "build_board", lambda days: ([_row()], ["ESPN slow"]))
    r = client.post("/api/fixtures/refresh").json()
    assert r["fixtures"][0]["key"] == "fd:1" and r["warnings"] == ["ESPN slow"] and r["auto_settled"] == []

    def no_fetch(days):
        raise AssertionError("GET must not fetch")

    monkeypatch.setattr(service, "build_board", no_fetch)
    r = client.get("/api/fixtures").json()
    assert r["fixtures"][0]["key"] == "fd:1" and r["stale"] is False and r["fetched_at"]


def test_refresh_fails_when_both_sources_fail(client, monkeypatch):
    def broken(days):
        raise RuntimeError("all down")

    monkeypatch.setattr(service, "build_board", broken)
    assert client.post("/api/fixtures/refresh").status_code == 502


def test_stored_board_min_odds_follow_edge(client, monkeypatch):
    monkeypatch.setattr(service, "build_board", lambda days: ([_row()], []))
    client.post("/api/fixtures/refresh")
    a = client.get("/api/fixtures?min_edge=0.08").json()["fixtures"][0]["min_odds"]["1"]
    b = client.get("/api/fixtures?min_edge=0.0").json()["fixtures"][0]["min_odds"]["1"]
    assert a == pytest.approx(b * 1.08)


def test_evaluate_records_odds_and_returns_previous(client):
    body = {"dataset": "EUROPE", "home": "A", "away": "B", "match_key": "fd:1", "odds": {"1": 2.7}}
    assert client.post("/api/evaluate", json=body).json()["last_odds"] == {}
    assert client.post("/api/evaluate", json=body).json()["last_odds"]["1"]["odds"] == 2.7


def test_track_record_endpoint(client):
    h = app_mod.history
    row = {**_row(), "kickoff": "2026-11-01T20:00:00+00:00"}
    h.save_board([row], "2026-11-01T10:00:00+00:00")
    h.record_odds("fd:1", "EUROPE", "A", "B",
                  [{"market": m, "odds": o} for m, o in (("1", 2.5), ("X", 3.2), ("2", 3.0))],
                  "2026-11-01T12:00:00+00:00")
    h.set_result("fd:1", 2, 1, "FINISHED")
    app_mod.store.add({"dataset": "EUROPE", "home": "A", "away": "B", "market": "1", "odds": 2.5, "stake": 10,
                       "match_key": "fd:1"})
    r = client.get("/api/track-record").json()
    assert r["summary"]["1X2"]["n"] == 1 and r["summary"]["1X2"]["n_compared"] == 1
    [m] = r["matches"]
    assert m["result"] == "2-1" and m["bets"] == ["1 @ 2.50"]
    assert m["model"]["1"] == 0.5 and m["market"]["1"] == pytest.approx(0.38, abs=0.02)
    assert client.get("/api/track-record?competition=Spain").json()["matches"] == []


def test_import_odds_finds_board_match(client, monkeypatch):
    row = {**_row(), "home": "Blackburn Rovers FC", "away": "Stoke City FC", "key": "fd:7"}
    monkeypatch.setattr(service, "build_board", lambda days: ([row], []))
    client.post("/api/fixtures/refresh")
    text = "Kampvinder\n1\n2,70\nX\n3,40\n2\n2,60"
    r = client.post("/api/import-odds", json={"text": text, "title": "Blackburn - Stoke | England Championship Odds"}).json()
    assert r["match_key"] == "fd:7" and r["odds"] == {"1": 2.7, "X": 3.4, "2": 2.6}
    assert (r["home"], r["away"]) == ("Blackburn", "Stoke")
    miss = client.post("/api/import-odds", json={"text": text, "title": "Leeds - Hull | x"}).json()
    assert miss["match_key"] is None and miss["odds"]["1"] == 2.7


def test_page_is_not_cached(client):
    r = client.get("/")
    assert r.status_code == 200 and "no-cache" in r.headers.get("cache-control", "")


def _league_row(key, competition):
    return {**_row(), "key": key, "dataset": "ESP", "match_id": None, "competition": competition}


def test_board_marks_unproven_division(client, monkeypatch):
    rows = [_league_row("fdcu:SP2:x", "Spain 2"), _league_row("fdcu:I2:x", "Italy 2"), _row()]
    monkeypatch.setattr(service, "build_board", lambda days: (rows, []))
    app_mod.history.set_validation({"Spain 2": {"model": 1.02, "baseline": 1.01, "matches": 300},
                                    "Italy 2": {"model": 1.00, "baseline": 1.01, "matches": 280}}, as_of="2026-11-03")
    board = {f["key"]: f for f in client.post("/api/fixtures/refresh").json()["fixtures"]}
    assert board["fdcu:SP2:x"]["unproven"] == {"model": 1.02, "baseline": 1.01}
    assert "unproven" not in board["fdcu:I2:x"]
    assert "unproven" not in board["fd:1"]  # existing leagues never carry it


def test_board_unvalidated_new_division_is_unproven(client, monkeypatch):
    monkeypatch.setattr(service, "build_board", lambda days: ([_league_row("fdcu:F2:x", "France 2")], []))
    f = client.post("/api/fixtures/refresh").json()["fixtures"][0]
    assert f["unproven"] == {"model": None, "baseline": None}


def test_refresh_while_leased(client, monkeypatch):
    app_mod.history.take_lease("refresh", "auto-1", pd.Timestamp.now(tz="UTC"))
    monkeypatch.setattr(service, "build_board", lambda days: pytest.fail("must not fetch"))
    r = client.post("/api/fixtures/refresh")
    assert r.status_code == 200 and r.json()["warnings"] == ["Refresh already running"]


def test_board_shows_last_auto_run(client):
    app_mod.history.record_run("auto", "2026-11-03T06:00:00+00:00", "2026-11-03T06:01:00+00:00", 40, [], None)
    assert client.get("/api/fixtures").json()["last_auto"]["finished"] == "2026-11-03T06:01:00+00:00"


def test_took_profit_endpoint(client):
    bid = client.post("/api/bets", json={"dataset": "EUROPE", "home": "A", "away": "B", "market": "1",
                                         "odds": 1.37, "stake": 25}).json()["id"]
    r = client.post(f"/api/bets/{bid}/settle", json={"cash_out": 20}).json()
    assert r["status"] == "took profit" and r["pnl"] == pytest.approx(-5)
    assert client.post(f"/api/bets/{bid}/settle", json={"cash_out": -1}).status_code == 422


def test_settle_with_nothing_to_do_is_rejected(client):
    bid = client.post("/api/bets", json={"dataset": "EUROPE", "home": "A", "away": "B", "market": "1",
                                         "odds": 1.37, "stake": 25}).json()["id"]
    r = client.post(f"/api/bets/{bid}/settle", json={"unknown_field": 20})
    assert r.status_code == 400
    assert client.get("/api/bets").json()["bets"][0]["status"] == "open"


def test_board_keeps_european_only_flag(client, monkeypatch):
    euro = {**_row(), "key": "espn:2", "dataset": "UEFA", "match_id": None, "competition": "Europa League",
            "european_only": True, "leagues": ["European only", "England 1"]}
    dom = {**_row(), "key": "espn:1", "dataset": "UEFA", "match_id": None, "competition": "Europa League",
           "european_only": False, "leagues": ["Scotland 1", "Turkey 1"]}
    monkeypatch.setattr(service, "build_board", lambda days: ([euro, dom], []))
    client.post("/api/fixtures/refresh")
    board = {f["key"]: f for f in client.get("/api/fixtures").json()["fixtures"]}
    assert board["espn:2"]["european_only"] is True
    assert not board["espn:1"].get("european_only")


def test_page_is_named_ninety_five(client):
    html = client.get("/").text
    assert "<title>ninety-five</title>" in html and "ninty" not in html


def test_duplicate_bet_is_409(client):
    bet = {"dataset": "E0", "home": "Watford", "away": "Lincoln", "market": "1", "odds": 2.0, "stake": 10,
           "match_key": "fd:9"}
    assert client.post("/api/bets", json=bet).status_code == 200
    r = client.post("/api/bets", json=bet)
    assert r.status_code == 409 and "Already logged: 1 on" in r.json()["detail"]


def test_stats_betting_endpoint(client):
    r = client.get("/api/stats/betting")
    assert r.status_code == 200
    body = r.json()
    assert body["tiles"]["n_settled"] == 0 and body["curve"] == []


def test_stats_betting_uses_competition(client):
    app_mod.history.save_board([_row()], "2026-11-03T08:00:00+00:00")
    app_mod.store.add({"dataset": "EUROPE", "home": "A", "away": "B", "market": "1", "odds": 2.0, "stake": 10,
                       "match_key": "fd:1"})
    open_ = client.get("/api/stats/betting").json()
    assert open_["tiles"]["n_open"] == 1
    assert app_mod.history.competitions() == {"fd:1": "England 2"}


def test_stats_model_endpoint(client):
    assert client.get("/api/stats/model").json() == {"teams": [], "leagues": []}


FULL_1X2 = {"1": 2.2, "X": 3.4, "2": 3.3}


def test_evaluate_uses_league_alpha(client):
    app_mod.history.save_board([_row()], "2026-11-03T08:00:00+00:00")
    app_mod.history.set_tuned("alpha", "England 2", 0.7, "2026-11-04T07:00:00+00:00")
    body = {"dataset": "EUROPE", "home": "A", "away": "B", "match_key": "fd:1", "odds": FULL_1X2}
    r = client.post("/api/evaluate", json=body).json()
    assert r["alpha"] == {"value": 0.7, "scope": "England 2"}
    s = next(x for x in r["selections"] if x["market"] == "1")
    assert s["p_blend"] == pytest.approx(0.7 * s["p_model"] + 0.3 * s["p_market"])


def test_evaluate_manual_falls_back_to_overall(client):
    body = {"dataset": "EUROPE", "home": "A", "away": "B", "odds": FULL_1X2}
    assert client.post("/api/evaluate", json=body).json()["alpha"] == {"value": 0.5, "scope": "default"}
    app_mod.history.set_tuned("alpha", "all", 0.4, "2026-11-04T07:00:00+00:00")
    assert client.post("/api/evaluate", json=body).json()["alpha"] == {"value": 0.4, "scope": "all"}


def test_client_alpha_ignored(client):
    body = {"dataset": "EUROPE", "home": "A", "away": "B", "odds": FULL_1X2, "settings": {"alpha": 1.0}}
    r = client.post("/api/evaluate", json=body).json()
    assert r["alpha"]["value"] == 0.5
    s = next(x for x in r["selections"] if x["market"] == "1")
    assert s["p_blend"] == pytest.approx(0.5 * s["p_model"] + 0.5 * s["p_market"])


def test_tuning_endpoint_and_revert(client):
    h = app_mod.history
    h.set_tuned("alpha", "all", 0.6, "2026-11-04T07:00:00+00:00")
    log_id = h.log_tuning({"at": "2026-11-04T07:00:00+00:00", "setting": "alpha", "scope": "all", "old": 0.5,
                           "new": 0.6, "action": "changed", "n": 150, "loss_old": 1.0, "loss_new": 0.98, "se": 0.005})
    t = client.get("/api/tuning").json()
    assert {"setting": "alpha", "scope": "all", "value": 0.6, "default": False} in t["settings"]
    assert any(s["setting"] == "half_life" and s["default"] for s in t["settings"])
    prog = next(p for p in t["progress"] if p["setting"] == "alpha" and p["scope"] == "all")
    assert prog["needed"] == 100 and prog["last_action"] == "changed"
    assert t["log"][0]["id"] == log_id and t["deferred"] == ["Team corrections"]
    r = client.post(f"/api/tuning/{log_id}/revert")
    assert r.status_code == 200 and r.json()["action"] == "reverted"
    assert client.post(f"/api/tuning/{log_id}/revert").status_code == 400
    assert ("alpha", "all") not in h.tuned()  # first change undone: back to the default


def _page(client):
    return client.get("/").text


def test_page_has_statistics_tab(client):
    html = _page(client)
    for s in ('data-tab="stats"', 'data-sub="betting"', 'data-sub="model"', 'data-sub="tuning"', 'id="tab-stats"'):
        assert s in html


def test_page_has_no_alpha_input(client):
    html = _page(client)
    assert 'id="alpha"' not in html and "tuned automatically" in html


def test_track_record_lives_in_statistics(client):
    html = _page(client)
    assert html.index('id="tab-stats"') < html.index('id="track"')
    assert html.index('id="tab-bets"') < html.index('id="tab-stats"')


def test_evaluate_counts_open_bets_on_the_match(client):
    app_mod.history.save_board([_row()], "2026-11-03T08:00:00+00:00")
    app_mod.store.add({"dataset": "EUROPE", "home": "A", "away": "B", "market": "X", "odds": 3.2, "stake": 45,
                       "match_key": "fd:1"})
    voided = app_mod.store.add({"dataset": "EUROPE", "home": "A", "away": "B", "market": "2", "odds": 3.0,
                                "stake": 30, "match_key": "fd:1"})
    app_mod.store.settle(voided, void=True)
    app_mod.store.add({"dataset": "EUROPE", "home": "C", "away": "D", "market": "1", "odds": 2.0, "stake": 30,
                       "match_key": "fd:2"})
    body = {"dataset": "EUROPE", "home": "A", "away": "B", "match_key": "fd:1", "odds": FULL_1X2,
            "settings": {"bankroll": 1000, "match_limit_pct": 0.05}}
    r = client.post("/api/evaluate", json=body).json()
    assert r["match_exposure"] == {"staked": 45, "limit": 50}
    assert all(s["stake"] <= 5 for s in r["selections"])


def test_evaluate_uses_stored_sharp_prices(client):
    app_mod.history.save_board([_row()], "2026-11-03T08:00:00+00:00")
    app_mod.history.save_sharp("fd:1", "2026-11-03T09:00:00+00:00", {
        "1": {"odds": 2.0, "book": "pinnacle"}, "X": {"odds": 3.6, "book": "pinnacle"},
        "2": {"odds": 4.2, "book": "pinnacle"}})
    body = {"dataset": "EUROPE", "home": "A", "away": "B", "match_key": "fd:1", "odds": FULL_1X2}
    r = client.post("/api/evaluate", json=body).json()
    assert r["sharp"]["books"] == ["Pinnacle"] and r["sharp"]["odds"]["1"] == 2.0
    sel = {s["market"]: s for s in r["selections"]}
    assert sel["1"]["basis"] == "sharp" and sel["1"]["p_blend"] == pytest.approx(r["sharp"]["probabilities"]["1"])
    # Without a board match there are no sharp prices.
    r = client.post("/api/evaluate", json={**body, "match_key": None}).json()
    assert r["sharp"] is None and all(s["basis"] != "sharp" for s in r["selections"])


def test_stats_betting_compares_bets_with_pinnacle_at_kickoff(client):
    app_mod.history.save_board([_row()], "2026-11-03T08:00:00+00:00")
    kickoff = app_mod.history.match("fd:1")["kickoff"]
    before = (pd.Timestamp(kickoff) - pd.Timedelta(hours=2)).isoformat(timespec="seconds")
    app_mod.history.save_sharp("fd:1", before, {"1": {"odds": 2.0, "book": "pinnacle"},
                                                "X": {"odds": 4.0, "book": "pinnacle"},
                                                "2": {"odds": 4.0, "book": "pinnacle"}})  # no margin: 1 = 50%
    app_mod.store.add({"dataset": "EUROPE", "home": "A", "away": "B", "market": "1", "odds": 2.2, "stake": 10,
                       "match_key": "fd:1", "kickoff": kickoff})
    t = client.get("/api/stats/betting").json()["tiles"]
    assert t["n_vs_pinnacle"] == 1 and t["avg_vs_pinnacle"] == pytest.approx(0.1)


def test_bet_detail_shows_the_prices_around_the_bet(client):
    app_mod.history.save_board([_row()], "2026-11-03T08:00:00+00:00")
    kickoff = app_mod.history.match("fd:1")["kickoff"]
    k = pd.Timestamp(kickoff)
    app_mod.history.save_sharp("fd:1", (k - pd.Timedelta(hours=30)).isoformat(timespec="seconds"),
                               {"1": {"odds": 2.0, "book": "pinnacle"}, "X": {"odds": 4.0, "book": "pinnacle"},
                                "2": {"odds": 4.0, "book": "pinnacle"}})
    app_mod.history.save_sharp("fd:1", (k - pd.Timedelta(hours=1)).isoformat(timespec="seconds"),
                               {"1": {"odds": 2.5, "book": "pinnacle"}, "X": {"odds": 2.5, "book": "pinnacle"},
                                "2": {"odds": 5.0, "book": "pinnacle"}})
    # Typed Danske Spil prices: one before the bet, one long after it.
    app_mod.history.record_odds("fd:1", "EUROPE", "A", "B", [{"market": "1", "odds": 2.2}, {"market": "X", "odds": 3.5}],
                                "2026-11-03T09:00:00.000+00:00")
    bet_id = app_mod.store.add({"dataset": "EUROPE", "home": "A", "away": "B", "market": "1", "odds": 2.2, "stake": 10,
                                "match_key": "fd:1", "kickoff": kickoff, "p_model": 0.5, "p_blend": 0.5, "ev": 0.1})
    app_mod.history.record_odds("fd:1", "EUROPE", "A", "B", [{"market": "1", "odds": 1.9}],
                                (pd.Timestamp.now(tz="UTC") + pd.Timedelta(hours=1)).isoformat())
    d = client.get(f"/api/bets/{bet_id}/detail").json()
    assert d["bet"]["id"] == bet_id
    assert d["danske_spil"] == {"1": 2.2, "X": 3.5}           # as typed when the bet was logged
    assert d["model"]["1"] == 0.5                               # board snapshot
    assert d["pinnacle_kickoff"]["odds"]["1"] == 2.5            # last price before kick-off
    assert d["pinnacle_kickoff"]["vs"] == pytest.approx(2.2 * 0.4 - 1)
    assert d["pinnacle_logged"]["odds"]["1"] == 2.0             # the price when the bet was logged
    assert d["pinnacle_logged"]["vs"] == pytest.approx(0.1)


def test_bet_detail_unknown_bet_is_404(client):
    assert client.get("/api/bets/999/detail").status_code == 404


def _pinnacle(key="fd:1"):
    app_mod.history.save_sharp(key, "2026-11-03T09:00:00+00:00", {
        "1": {"odds": 2.0, "book": "pinnacle"}, "X": {"odds": 3.6, "book": "pinnacle"},
        "2": {"odds": 4.2, "book": "pinnacle"}, "Over 2.5": {"odds": 1.95, "book": "pinnacle"},
        "Under 2.5": {"odds": 1.95, "book": "pinnacle"}})


def test_evaluate_prices_btts_from_pinnacle_implied_goals(client):
    app_mod.history.save_board([_row()], "2026-11-03T08:00:00+00:00")
    _pinnacle()
    body = {"dataset": "EUROPE", "home": "A", "away": "B", "match_key": "fd:1",
            "odds": {"BTTS Yes": 1.9, "BTTS No": 1.9, "1": 2.1, "X": 3.4, "2": 3.9}}
    r = client.post("/api/evaluate", json=body).json()
    sel = {s["market"]: s for s in r["selections"]}
    assert sel["1"]["basis"] == "sharp" and sel["BTTS Yes"]["basis"] == "implied"
    assert len(r["sharp"]["implied"]["xg"]) == 2
    assert sel["BTTS Yes"]["p_blend"] == pytest.approx(r["sharp"]["implied"]["probabilities"]["BTTS Yes"])


def test_combo_is_priced_from_pinnacle_when_known(client):
    app_mod.history.save_board([_row()], "2026-11-03T08:00:00+00:00")
    _pinnacle()
    body = {"dataset": "EUROPE", "home": "A", "away": "B", "match_key": "fd:1", "legs": ["X", "Under 2.5"], "odds": 6.0}
    r = client.post("/api/combo", json=body).json()
    s = r["selection"]
    assert s["market"] == "X + Under 2.5" and s["basis"] == "implied"
    assert s["ev"] == pytest.approx(s["p_blend"] * 6.0 - 1)
    assert s["p_blend"] > r["independent"]  # the legs go together
    # Without Pinnacle prices the model prices it.
    r = client.post("/api/combo", json={**body, "match_key": None}).json()
    assert r["selection"]["basis"] == "model"


def test_combo_rejects_unknown_or_too_few_legs(client):
    body = {"dataset": "EUROPE", "home": "A", "away": "B", "legs": ["X", "Corners 9.5"], "odds": 6.0}
    assert client.post("/api/combo", json=body).status_code == 400
    assert client.post("/api/combo", json={**body, "legs": ["X"]}).status_code == 422  # request validation
    assert client.post("/api/combo", json={**body, "legs": ["1", "2"]}).status_code == 400  # can't both win


def test_stats_soft_compares_typed_prices_with_pinnacle(client):
    app_mod.history.save_board([_row()], "2026-11-03T08:00:00+00:00")
    _pinnacle()
    app_mod.history.record_odds("fd:1", "EUROPE", "A", "B", [{"market": "1", "odds": 2.1}, {"market": "BTTS No", "odds": 2.0}],
                                "2026-11-03T10:00:00.000+00:00")
    r = client.get("/api/stats/soft").json()
    assert r["n"] == 2
    assert {g["label"] for g in r["by_market"]} == {"1X2", "BTTS"}
    assert r["by_league"][0]["label"] == "England 2"


def test_stats_soft_counts_only_the_last_price_per_market(client):
    app_mod.history.save_board([_row()], "2026-11-03T08:00:00+00:00")
    _pinnacle()
    for t, o in (("10:00", 2.1), ("10:01", 4.0), ("10:02", 2.05)):  # a slip, corrected
        app_mod.history.record_odds("fd:1", "EUROPE", "A", "B", [{"market": "1", "odds": o}], f"2026-11-03T{t}:00.000+00:00")
    r = client.get("/api/stats/soft").json()
    assert r["n"] == 1 and r["top"][0]["odds"] == 2.05


def test_evaluate_flags_goal_markets_priced_from_1x2_only_and_overlapping_bets(client):
    app_mod.history.save_board([_row()], "2026-11-03T08:00:00+00:00")
    app_mod.history.save_sharp("fd:1", "2026-11-03T09:00:00+00:00", {
        "1": {"odds": 3.12, "book": "pinnacle"}, "X": {"odds": 3.22, "book": "pinnacle"},
        "2": {"odds": 2.5, "book": "pinnacle"}})
    body = {"dataset": "EUROPE", "home": "A", "away": "B", "match_key": "fd:1",
            "odds": {"Under 2.5": 2.2, "Under 1.5": 4.5, "BTTS No": 2.4, "1": 2.0}}
    r = client.post("/api/evaluate", json=body).json()
    sel = {s["market"]: s for s in r["selections"]}
    assert r["sharp"]["goals_from_1x2"] is True
    assert sel["Under 2.5"]["goals_from_1x2"] and sel["Under 2.5"]["caution"]
    assert not sel["1"]["goals_from_1x2"]
    o = r["overlap"]
    assert set(o["markets"]) == {s["market"] for s in r["selections"] if s["recommended"] and s["stake"] > 0}
    assert len(o["markets"]) >= 2 and o["together"] and o["p_all_lose"] > o["p_all_lose_independent"]


def test_evaluate_quarter_line_counts_as_a_goals_line(client):
    app_mod.history.save_board([_row()], "2026-11-03T08:00:00+00:00")
    app_mod.history.save_sharp("fd:1", "2026-11-03T09:00:00+00:00", {
        "1": {"odds": 3.12, "book": "pinnacle"}, "X": {"odds": 3.22, "book": "pinnacle"},
        "2": {"odds": 2.5, "book": "pinnacle"}, "Over 2.75": {"odds": 2.0, "book": "pinnacle"},
        "Under 2.75": {"odds": 1.88, "book": "pinnacle"}})
    body = {"dataset": "EUROPE", "home": "A", "away": "B", "match_key": "fd:1", "odds": {"Under 2.5": 1.77}}
    r = client.post("/api/evaluate", json=body).json()
    assert r["sharp"]["goals_from_1x2"] is False
    assert not r["selections"][0]["goals_from_1x2"]
    assert "Over 2.75" not in r["sharp"]["probabilities"]


# ------------------------------------------------------------- NFL / NBA
def _us_game():
    app_mod.history.save_us_games([{"key": "us:ev1", "sport": "NFL", "home": "Detroit Lions", "away": "Chicago Bears",
                                    "kickoff": "2099-11-09T18:00:00+00:00"}], "2026-11-09T07:00:00+00:00")
    app_mod.history.save_sharp("us:ev1", "2026-11-09T07:00:00+00:00", {
        "ML 1": {"odds": 1.4, "book": "pinnacle"}, "ML 2": {"odds": 3.1, "book": "pinnacle"},
        "Spread 1 -7.5": {"odds": 1.91, "book": "pinnacle"}, "Spread 2 +7.5": {"odds": 1.95, "book": "pinnacle"},
        "Over 47": {"odds": 1.9, "book": "pinnacle"}, "Under 47": {"odds": 1.95, "book": "pinnacle"}})


def test_us_games_lists_games_with_pinnacle_fair_prices(client):
    _us_game()
    r = client.get("/api/us/games", params={"sport": "NFL"}).json()
    (g,) = r["games"]
    assert g["key"] == "us:ev1" and g["prices"]["ML 1"] == 1.4
    assert g["fair"]["ML 1"] + g["fair"]["ML 2"] == pytest.approx(1)
    assert "credits" in r
    assert client.get("/api/us/games", params={"sport": "Cricket"}).status_code == 400


def test_us_evaluate_judges_typed_prices_against_pinnacle_and_records_them(client):
    _us_game()
    body = {"key": "us:ev1", "odds": {"ML 2": 3.6, "Spread 1 -7.5": 1.8, "Over 48": 1.9}}
    r = client.post("/api/us/evaluate", json=body).json()
    sel = {s["market"]: s for s in r["selections"]}
    assert set(sel) == {"ML 2", "Spread 1 -7.5"}  # no Pinnacle price for Over 48: not judged
    assert sel["ML 2"]["basis"] == "sharp" and sel["ML 2"]["recommended"]
    assert sel["ML 2"]["ev"] == pytest.approx(3.6 * r["sharp"]["probabilities"]["ML 2"] - 1)
    assert not sel["Spread 1 -7.5"]["recommended"]
    assert r["unmatched"] == ["Over 48"]
    soft = client.get("/api/stats/soft").json()
    assert soft["n"] == 2 and {g["label"] for g in soft["by_league"]} == {"NFL"}
    assert client.post("/api/us/evaluate", json={**body, "key": "us:nope"}).status_code == 404


def test_us_bet_logs_settles_with_push_and_shows_pinnacle(client):
    _us_game()
    bet = {"dataset": "NFL", "home": "Detroit Lions", "away": "Chicago Bears", "market": "Spread 1 -7",
           "odds": 1.95, "stake": 10, "match_key": "us:ev1", "kickoff": "2099-11-09T18:00:00+00:00"}
    bid = client.post("/api/bets", json=bet).json()["id"]
    s = client.post(f"/api/bets/{bid}/settle", json={"home_goals": 24, "away_goals": 17}).json()
    assert s["status"] == "void" and s["pnl"] == 0
    bid = client.post("/api/bets", json={**bet, "market": "ML 2", "odds": 3.6}).json()["id"]
    d = client.get(f"/api/bets/{bid}/detail").json()
    assert d["pinnacle_logged"]["vs"] == pytest.approx(3.6 * d["pinnacle_logged"]["probabilities"]["ML 2"] - 1)


def test_import_keeps_the_last_pages_for_building_parsers(client):
    from ninety_five import import_odds
    for i in range(import_odds.KEEP_IMPORTS + 3):
        client.post("/api/import-odds", json={"text": f"page {i}", "title": f"t{i}"})
    files = sorted(import_odds.IMPORT_DIR.glob("*.json"))
    assert len(files) == import_odds.KEEP_IMPORTS
    import json
    assert json.loads(files[-1].read_text()) ["text"] == f"page {import_odds.KEEP_IMPORTS + 2}"


def test_import_of_an_nfl_page_finds_the_game_on_the_us_tab(client):
    _us_game()
    text = ("Sportsgrene\n/\nAmerikansk fodbold\n/\nUSA\n/\nUSA NFL\nKampvinder (Inkl. OT)\n1\n3,40\n2\n1,35\n"
            "Handicap (inkl. OT)\nChicago Bears\n+7.5\n1,90\nDetroit Lions\n-7.5\n1,90\nVis mere")
    r = client.post("/api/import-odds", json={"text": text, "title": "Chicago Bears - Detroit Lions | USA NFL Odds"}).json()
    # Danske Spil lists the Bears first; on the tab the Lions are home (1).
    assert r["us"] == {"sport": "NFL", "key": "us:ev1"}
    assert r["match_key"] is None
    assert r["odds"] == {"ML 2": 3.4, "ML 1": 1.35, "Spread 2 +7.5": 1.9, "Spread 1 -7.5": 1.9}
    other = client.post("/api/import-odds", json={"text": text, "title": "Green Bay Packers - Detroit Lions | USA NFL Odds"}).json()
    assert other["us"] == {"sport": "NFL", "key": None} and other["odds"]["ML 1"] == 3.4


def test_college_football_is_a_us_sport(client):
    assert client.get("/api/us/games", params={"sport": "NCAAF"}).status_code == 200
