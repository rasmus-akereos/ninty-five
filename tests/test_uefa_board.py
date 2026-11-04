from types import SimpleNamespace

import pandas as pd
import pytest

from ninty_five import fixtures, intl_fixtures, service, uefa
from ninty_five.history import HistoryStore
from ninty_five.model import CLUB_PARAMS, DixonColes
from ninty_five.store import BetStore

NOW = pd.Timestamp("2026-11-04 12:00", tz="UTC")


def ev(eid, date, home, away, status="STATUS_SCHEDULED"):
    return {"id": eid, "date": date, "status": {"type": {"name": status}},
            "competitions": [{"neutralSite": False, "competitors": [
                {"homeAway": "home", "team": {"id": "1", "displayName": home}, "score": "0"},
                {"homeAway": "away", "team": {"id": "2", "displayName": away}, "score": "0"}]}]}


EL = {"leagues": [{"name": "UEFA Europa League"}], "events": [
    ev("1", "2026-11-05T20:00Z", "Celtic", "Beşiktaş JK"),
    ev("2", "2026-11-05T17:45Z", "Sparta Prague", "AFC Bournemouth"),
    ev("3", "2026-11-05T17:45Z", "Kairat Almaty", "Atalanta"),
    ev("4", "2026-11-05T20:00Z", "Riga FC", "Jablonec"),
]}


@pytest.fixture
def setup(monkeypatch):
    asked = []

    def fake_get(slug, month):
        asked.append(slug)
        return EL if slug == "uefa.europa" else {"events": []}

    monkeypatch.setattr(intl_fixtures, "_get", fake_get)
    model = SimpleNamespace(teams=["Celtic", "Besiktas", "Sparta Prague", "Bournemouth", "Kairat Almaty", "Atalanta",
                                   "Jablonec"],
                            n_matches={"Celtic": 140, "Besiktas": 130, "Sparta Prague": 30, "Bournemouth": 110,
                                       "Kairat Almaty": 6, "Atalanta": 140, "Jablonec": 12})
    monkeypatch.setattr(service, "_cached", lambda key, refresh=False: (0, model, None))
    monkeypatch.setattr(uefa, "last_resolution", uefa.Resolution(
        mapping={"Celtic": "Celtic", "Beşiktaş JK": "Besiktas", "Sparta Prague": "Sparta Prague",
                 "AFC Bournemouth": "Bournemouth", "Kairat Almaty": "Kairat Almaty", "Atalanta": "Atalanta",
                 "Jablonec": "Jablonec"},
        excluded={"Riga FC"}, european_only={"Sparta Prague", "Kairat Almaty", "Jablonec"}))
    monkeypatch.setattr(uefa, "last_inputs", ({}, {}))
    return asked


def test_uefa_upcoming_lists_known_and_tags_european_only(setup):
    df, warnings = uefa.REAL_UPCOMING(7, now=NOW)
    got = {(r.home, r.away): r.european_only for r in df.itertuples()}
    assert got == {("Celtic", "Besiktas"): False, ("Sparta Prague", "Bournemouth"): True}
    r = df[df.home == "Celtic"].iloc[0]
    assert r.key == "espn:1" and r.dataset == "UEFA" and r.competition == "Europa League"
    assert not warnings


def test_uefa_upcoming_skips_qualifier_and_champions_slugs(setup):
    uefa.REAL_UPCOMING(7, now=NOW)
    assert set(setup) == {"uefa.europa", "uefa.europa.conf"}


def test_uefa_upcoming_resolves_new_names_and_warns(setup, monkeypatch):
    monkeypatch.setattr(uefa, "last_inputs", ({"SCO": ["Celtic", "Hearts"]}, {"SCO": {"Celtic", "Hibernian"}}))
    EL2 = {"events": [ev("9", "2026-11-05T20:00Z", "Hibernian", "Celtic")]}
    monkeypatch.setattr(intl_fixtures, "_get", lambda slug, month: EL2 if slug == "uefa.europa" else {"events": []})
    df, warnings = uefa.REAL_UPCOMING(7, now=NOW)
    assert df.empty
    assert warnings == ["UEFA: no domestic team for Hibernian (add it to uefa.ALIASES)"]


def _tiny_model():
    teams = ["Celtic", "Besiktas", "Ajax", "Lyon"]
    rows = [(pd.Timestamp(f"2026-10-{d:02d}"), teams[d % 4], teams[(d + 1) % 4], d % 3, (d + 1) % 2, False, "x")
            for d in range(1, 29)]
    df = pd.DataFrame(rows, columns=["date", "home", "away", "hg", "ag", "neutral", "competition"])
    return DixonColes(**CLUB_PARAMS).fit(df), df


def test_uefa_board_rows_priced(monkeypatch):
    model, df = _tiny_model()
    monkeypatch.setattr(service, "_cached", lambda key, refresh=False: (0, model, df))
    fx = pd.DataFrame([{"key": "espn:1", "dataset": "UEFA", "competition": "Europa League",
                        "kickoff": pd.Timestamp("2026-11-05 20:00", tz="UTC"), "home": "Celtic", "away": "Besiktas",
                        "neutral": False, "european_only": False}])
    monkeypatch.setattr(uefa, "upcoming", lambda days, now=None: (fx, ["w"]))
    rows, warnings = service.uefa_board(7)
    r = rows[0]
    assert r["rated"] and r["dataset"] == "UEFA" and r["competition"] == "Europa League"
    assert r["european_only"] is False and warnings == ["w"]
    assert ("European fixtures", "uefa_board") in service.BOARD_SOURCES


def test_update_results_settles_europa_bet_on_90_minutes(tmp_path, monkeypatch):
    db = tmp_path / "b.sqlite"
    h, bets = HistoryStore(db), BetStore(db)
    h.save_board([{"key": "espn:55", "dataset": "UEFA", "match_id": None, "kickoff": "2026-11-05T20:00:00+00:00",
                   "competition": "Europa League", "detail": "", "stage": "", "home": "Celtic", "away": "Besiktas",
                   "neutral": False, "rated": False}], "t")
    bid = bets.add({"dataset": "UEFA", "home": "Celtic", "away": "Besiktas", "market": "X", "odds": 3.4,
                    "stake": 10, "match_key": "espn:55"})
    monkeypatch.setattr(fixtures, "recent_results", lambda days=7: pd.DataFrame(columns=["match_id", "hg", "ag"]))
    asked = []

    def fake_results(since, now=None, slugs=None):
        asked.append(slugs)
        return pd.DataFrame([{"event_id": "55", "hg": 1, "ag": 1, "status": "STATUS_FINAL_AET"}])

    monkeypatch.setattr(intl_fixtures, "results", fake_results)
    assert service.update_results(h, bets, pd.Timestamp("2026-11-06T12:00Z")) == [bid]
    assert bets.list()[0]["status"] == "won"
    assert {"uefa.europa", "uefa.europa.conf"} <= set(asked[0])
