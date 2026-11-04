import pandas as pd
import pytest

from ninety_five.history import HistoryStore

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


# ------------------------------------------------------------- lease / runs
def _t(s):
    return pd.Timestamp(s, tz="UTC")


def test_lease_exclusive(tmp_path):
    h = HistoryStore(tmp_path / "h.sqlite")
    assert h.take_lease("refresh", "auto-1", _t("2026-11-03 07:00"))
    assert not h.take_lease("refresh", "manual-1", _t("2026-11-03 07:05"))
    h.release_lease("refresh", "auto-1")
    assert h.take_lease("refresh", "manual-1", _t("2026-11-03 07:06"))


def test_lease_abandoned_after_30_minutes(tmp_path):
    h = HistoryStore(tmp_path / "h.sqlite")
    assert h.take_lease("refresh", "crashed", _t("2026-11-03 07:00"))
    assert not h.take_lease("refresh", "next", _t("2026-11-03 07:29"))
    assert h.take_lease("refresh", "next", _t("2026-11-03 07:31"))


def test_release_only_by_holder(tmp_path):
    h = HistoryStore(tmp_path / "h.sqlite")
    h.take_lease("refresh", "auto-1", _t("2026-11-03 07:00"))
    h.release_lease("refresh", "someone-else")
    assert not h.take_lease("refresh", "manual-1", _t("2026-11-03 07:01"))


def test_runs_recorded(tmp_path):
    h = HistoryStore(tmp_path / "h.sqlite")
    assert h.last_run("auto") is None and h.last_success() is None
    h.record_run("auto", "2026-11-02T07:00:00+00:00", "2026-11-02T07:01:00+00:00", 40, [], None)
    h.record_run("manual", "2026-11-03T09:00:00+00:00", "2026-11-03T09:01:00+00:00", 41, ["x"], None)
    h.record_run("auto", "2026-11-03T07:00:00+00:00", "2026-11-03T07:00:30+00:00", 0, [], "boom")
    assert h.last_run("auto")["error"] == "boom"
    assert h.last_success("auto") == "2026-11-02T07:01:00+00:00"
    assert h.last_success() == "2026-11-03T09:01:00+00:00"
    assert h.last_run("manual")["warnings"] == ["x"] and h.last_run("manual")["matches"] == 41


def _fdcu(key, kickoff):
    return {"key": key, "dataset": "ENG", "match_id": None, "kickoff": kickoff, "competition": "England 3",
            "detail": "", "stage": "", "home": "Oxford", "away": "Reading", "neutral": False, "rated": False}


def test_moved_lower_league_match_keeps_its_key(tmp_path):
    h = HistoryStore(tmp_path / "h.sqlite")
    h.save_board([_fdcu("fdcu:E2:2026-11-04:Oxford:Reading", "2026-11-04T19:45:00+00:00")], "2026-11-02T07:00:00+00:00")
    moved = _fdcu("fdcu:E2:2026-11-11:Oxford:Reading", "2026-11-11T19:45:00+00:00")
    h.save_board([moved], "2026-11-05T07:00:00+00:00")
    rows = h.board(days=30, now=pd.Timestamp("2026-11-03T12:00Z"))
    assert [(r["key"], r["kickoff"]) for r in rows] == [("fdcu:E2:2026-11-04:Oxford:Reading", "2026-11-11T19:45:00+00:00")]
    assert moved["key"] == "fdcu:E2:2026-11-04:Oxford:Reading"  # callers see the stored key


def test_finished_meeting_is_not_reused(tmp_path):
    h = HistoryStore(tmp_path / "h.sqlite")
    h.save_board([_fdcu("fdcu:E2:2026-11-04:Oxford:Reading", "2026-11-04T19:45:00+00:00")], "2026-11-02T07:00:00+00:00")
    h.set_result("fdcu:E2:2026-11-04:Oxford:Reading", 1, 0, "FINISHED")
    h.save_board([_fdcu("fdcu:E2:2026-11-25:Oxford:Reading", "2026-11-25T19:45:00+00:00")], "2026-11-22T07:00:00+00:00")
    assert h.match("fdcu:E2:2026-11-25:Oxford:Reading") is not None


def test_finished_includes_xg(tmp_path):
    h = HistoryStore(tmp_path / "h.sqlite")
    h.save_board([_row(kickoff="2026-11-03T20:00:00+00:00")], "2026-11-03T10:00:00+00:00")
    h.set_result("fd:1", 1, 0, "FINISHED")
    assert h.finished()[0]["xg"] == [1.2, 1.0]


def test_revert_restores_and_blocks(tmp_path):
    h = HistoryStore(tmp_path / "h.sqlite")
    h.set_tuned("alpha", "all", 0.6, "2026-11-04T07:00:00+00:00")
    log_id = h.log_tuning({"at": "2026-11-04T07:00:00+00:00", "setting": "alpha", "scope": "all", "old": 0.5,
                           "new": 0.6, "action": "changed", "n": 150, "loss_old": 1.0, "loss_new": 0.98,
                           "se": 0.005, "note": ""})
    assert h.tuned() == {("alpha", "all"): 0.6}
    now = pd.Timestamp("2026-11-04T08:00Z")
    h.revert(log_id, now.isoformat())
    assert ("alpha", "all") not in h.tuned()  # first change undone: back to the default
    assert h.blocked("alpha", "all", now + pd.Timedelta(days=29))
    assert not h.blocked("alpha", "all", now + pd.Timedelta(days=31))
    assert not h.blocked("alpha", "England 2", now)
    with pytest.raises(ValueError):
        h.revert(log_id, now.isoformat())
    log = h.tuning_log()
    assert [r["action"] for r in log] == ["reverted", "changed"] and log[1]["reverted_at"]
    assert h.last_tuning("alpha", "all")["action"] == "reverted"
    assert h.last_tuning("alpha", "all", action="changed")["id"] == log_id


def _change(h, old, new, scope="all", at="2026-11-04T07:00:00+00:00"):
    h.set_tuned("alpha", scope, new, at)
    return h.log_tuning({"at": at, "setting": "alpha", "scope": scope, "old": old, "new": new, "action": "changed"})


def test_only_latest_change_can_be_reverted(tmp_path):
    h = HistoryStore(tmp_path / "h.sqlite")
    first = _change(h, 0.5, 0.6)
    second = _change(h, 0.6, 0.7, at="2026-11-05T07:00:00+00:00")
    with pytest.raises(ValueError):
        h.revert(first, "2026-11-06T07:00:00+00:00")
    assert h.tuned()[("alpha", "all")] == 0.7
    h.revert(second, "2026-11-06T07:00:00+00:00")
    assert h.tuned()[("alpha", "all")] == 0.6
    flags = {r["id"]: r["revertable"] for r in h.tuning_log() if r["action"] == "changed"}
    assert flags == {first: False, second: False}  # first is no longer the live value's change


def test_reverting_first_league_change_restores_fallback(tmp_path):
    h = HistoryStore(tmp_path / "h.sqlite")
    rid = _change(h, 0.5, 0.6, scope="England 2")  # 0.5 was inherited from the overall value
    assert [r["revertable"] for r in h.tuning_log()] == [True]
    h.revert(rid, "2026-11-06T07:00:00+00:00")
    assert ("alpha", "England 2") not in h.tuned()
