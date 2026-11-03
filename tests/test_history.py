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


def _sel(market, odds):
    return {"market": market, "odds": odds, "p_model": 0.4, "p_market": 0.35, "p_blend": 0.375}


def test_record_odds_skips_unchanged_and_invalid(tmp_path):
    h = HistoryStore(tmp_path / "h.sqlite")
    assert h.record_odds("fd:1", "EUROPE", "A", "B", [_sel("1", 2.0)], "t1") == 1
    assert h.record_odds("fd:1", "EUROPE", "A", "B", [_sel("1", 2.0), _sel("X", 1.0)], "t2") == 0
    assert h.record_odds("fd:1", "EUROPE", "A", "B", [_sel("1", 2.7)], "t3") == 1
    assert h.last_odds("fd:1", "EUROPE", "A", "B") == {"1": {"odds": 2.7, "entered_at": "t3"}}


def test_manual_match_odds_keyed_by_teams(tmp_path):
    h = HistoryStore(tmp_path / "h.sqlite")
    h.record_odds(None, "INTL", "Denmark", "Scotland", [_sel("Over 2.5", 2.1)], "t1")
    assert h.last_odds(None, "INTL", "Denmark", "Scotland")["Over 2.5"]["odds"] == 2.1
    assert h.last_odds(None, "INTL", "Scotland", "Denmark") == {}
    assert h.last_odds("fd:1", "INTL", "Denmark", "Scotland") == {}  # keyed lookups ignore manual rows


def test_results_ignores_cancelled(tmp_path):
    h = HistoryStore(tmp_path / "h.sqlite")
    h.save_board([_row(key="espn:9", kickoff="2026-11-01T20:00:00+00:00"),
                  _row(key="fd:2", kickoff="2026-11-02T20:00:00+00:00"),
                  _row(key="fd:3", kickoff="2026-10-01T20:00:00+00:00")], "t")   # older than 14 days
    h.set_result("espn:9", None, None, "STATUS_CANCELED")
    assert [p["key"] for p in h.pending_results(NOW)] == ["fd:2"]
    h.set_result("fd:2", 2, 1, "FINISHED")
    assert h.pending_results(NOW) == []


def test_finished_uses_last_snapshot_and_odds_before_kickoff(tmp_path):
    h = HistoryStore(tmp_path / "h.sqlite")
    ko = "2026-11-01T20:00:00+00:00"
    h.save_board([_row(key="fd:1", kickoff=ko, p1=0.40)], "2026-11-01T10:00:00+00:00")
    h.save_board([_row(key="fd:1", kickoff=ko, p1=0.60)], "2026-11-01T21:00:00+00:00")   # after kick-off
    h.record_odds("fd:1", "EUROPE", "A", "B", [_sel("1", 2.5)], "2026-11-01T12:00:00+00:00")
    h.record_odds("fd:1", "EUROPE", "A", "B", [_sel("1", 2.9)], "2026-11-01T20:30:00+00:00")
    h.record_odds(None, "EUROPE", "A", "B", [_sel("X", 3.3)], "2026-11-01T12:00:00+00:00")  # manual: excluded
    assert h.finished() == []  # no result yet
    h.set_result("fd:1", 1, 0, "FINISHED")
    [m] = h.finished()
    assert m["probabilities"]["1"] == pytest.approx(0.40) and m["odds"] == {"1": 2.5}
    assert (m["hg"], m["ag"]) == (1, 0) and m["competition"] == "England 2"
    assert h.finished(competition="Spain") == []


def test_rescheduled_match_is_pending_again(tmp_path):
    h = HistoryStore(tmp_path / "h.sqlite")
    h.save_board([_row(key="espn:9", kickoff="2026-10-30T20:00:00+00:00")], "t1")
    h.set_result("espn:9", None, None, "STATUS_POSTPONED")
    h.save_board([_row(key="espn:9", kickoff="2026-11-02T20:00:00+00:00")], "t2")   # listed again, new date
    assert [p["key"] for p in h.pending_results(NOW)] == ["espn:9"]


def test_late_arriving_older_price_does_not_overwrite(tmp_path):
    h = HistoryStore(tmp_path / "h.sqlite")
    h.record_odds("fd:1", "EUROPE", "A", "B", [_sel("1", 2.7)], "2026-11-03T12:00:05+00:00")
    assert h.record_odds("fd:1", "EUROPE", "A", "B", [_sel("1", 2.0)], "2026-11-03T12:00:04+00:00") == 0
    assert h.last_odds("fd:1", "EUROPE", "A", "B")["1"]["odds"] == 2.7
