import warnings

import numpy as np
import pandas as pd
import pytest

with warnings.catch_warnings():  # starlette's httpx deprecation notice on import
    warnings.simplefilter("ignore")
    from fastapi.testclient import TestClient

from ninty_five import app as app_mod
from ninty_five import service
from ninty_five.history import HistoryStore
from ninty_five.model import CLUB_PARAMS, DixonColes
from ninty_five.store import BetStore


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
    assert r == {"fixtures": [], "fetched_at": None, "stale": True}


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
