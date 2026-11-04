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
    assert r == {"fixtures": [], "fetched_at": None, "stale": True, "last_auto": None}


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
