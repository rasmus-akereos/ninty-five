import pandas as pd
import pytest

from ninty_five.history import HistoryStore

NOW = pd.Timestamp("2026-11-03T12:00Z")


def _row(key="fd:1", kickoff="2026-11-10T20:00:00+00:00", p1=0.4, form=None, rated=True):
    return {
        "key": key, "dataset": "EUROPE", "match_id": 1, "kickoff": kickoff, "competition": "England 2",
        "detail": "", "stage": "", "home": "A", "away": "B", "neutral": False, "rated": rated,
        "xg": [1.2, 1.0], "data_matches": [100, 100], "leagues": ["", ""], "model_as_of": "2026-11-02",
        "probabilities": {"1": p1, "X": 0.3, "2": 0.7 - p1}, "form": form or {"home": [], "away": []},
    }


def test_save_board_dedupes_snapshots(tmp_path):
    h = HistoryStore(tmp_path / "h.sqlite")
    assert h.save_board([_row()], "2026-11-03T10:00:00+00:00") == 1
    assert h.save_board([_row(p1=0.40000001)], "2026-11-03T11:00:00+00:00") == 0  # same at 4 dp
    assert h.save_board([_row(p1=0.41)], "2026-11-03T12:00:00+00:00") == 1
    assert h.fetched_at() == "2026-11-03T12:00:00+00:00"


def test_new_form_alone_makes_a_snapshot(tmp_path):
    h = HistoryStore(tmp_path / "h.sqlite")
    h.save_board([_row()], "t1")
    form = {"home": [{"date": "2026-11-01", "opponent": "C", "venue": "H", "gf": 1, "ga": 0, "result": "W"}], "away": []}
    assert h.save_board([_row(form=form)], "t2") == 1


def test_board_returns_latest_snapshot(tmp_path):
    h = HistoryStore(tmp_path / "h.sqlite")
    h.save_board([_row(p1=0.40)], "t1")
    h.save_board([_row(p1=0.45)], "t2")
    [r] = h.board(days=14, now=NOW)
    assert r["probabilities"]["1"] == pytest.approx(0.45) and r["rated"] and r["key"] == "fd:1"
    assert r["home"] == "A" and r["neutral"] is False and r["xg"] == [1.2, 1.0]


def test_board_hides_past_kickoffs(tmp_path):
    h = HistoryStore(tmp_path / "h.sqlite")
    h.save_board([_row(key="fd:1", kickoff="2026-11-01T20:00:00+00:00"), _row(key="fd:2")], "t")
    assert [r["key"] for r in h.board(days=14, now=NOW)] == ["fd:2"]
    assert h.board(days=3, now=NOW) == []  # kick-off on the 10th is outside a 3-day window


def test_unrated_row_has_no_snapshot(tmp_path):
    h = HistoryStore(tmp_path / "h.sqlite")
    assert h.save_board([_row(rated=False)], "t") == 0
    [r] = h.board(days=14, now=NOW)
    assert r["rated"] is False


def test_fetched_at_empty(tmp_path):
    assert HistoryStore(tmp_path / "h.sqlite").fetched_at() is None
