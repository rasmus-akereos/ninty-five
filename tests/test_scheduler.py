import threading

import pandas as pd

from ninety_five import scheduler, service

CPH = "Europe/Copenhagen"


def T(s):
    return pd.Timestamp(s, tz=CPH).tz_convert("UTC")


def test_due_first_start_without_any_fetch():
    assert scheduler.due(None, None, T("2026-11-03 12:00"))


def test_due_catch_up_after_24h():
    assert scheduler.due(T("2026-11-02 06:00"), T("2026-11-02 07:00"), T("2026-11-03 06:30"))


def test_not_due_on_restart_after_recent_fetch_before_7():
    assert not scheduler.due(T("2026-11-03 01:00"), T("2026-11-02 07:00"), T("2026-11-03 06:30"))


def test_due_at_seven_copenhagen():
    assert scheduler.due(T("2026-11-03 01:00"), T("2026-11-02 07:00"), T("2026-11-03 07:05"))


def test_not_due_twice_same_day():
    assert not scheduler.due(T("2026-11-03 07:01"), T("2026-11-03 07:01"), T("2026-11-03 09:00"))


def test_manual_fetch_after_seven_does_not_replace_auto():
    assert scheduler.due(T("2026-11-03 07:30"), T("2026-11-02 07:00"), T("2026-11-03 08:00"))


def test_seven_is_copenhagen_across_dst():
    # 07:00 CEST on 1 July is 05:00 UTC: due at 05:10 UTC, not at 04:50 UTC
    last = pd.Timestamp("2026-06-30 05:00", tz="UTC")
    assert not scheduler.due(last, last, pd.Timestamp("2026-07-01 04:50", tz="UTC"))
    assert scheduler.due(last, last, pd.Timestamp("2026-07-01 05:10", tz="UTC"))


def test_start_disabled_by_env(monkeypatch):
    monkeypatch.setenv("NINETY_FIVE_SCHEDULER", "0")
    assert scheduler.start(None, None) is None


class FakeHistory:
    def last_success(self, kind=None):
        return None


def test_loop_survives_failing_run(monkeypatch):
    monkeypatch.setenv("NINETY_FIVE_SCHEDULER", "1")
    calls = []
    done = threading.Event()

    def failing(history, bets, days, kind, now=None, refit_countries=False):
        calls.append((kind, refit_countries))
        if len(calls) >= 3:
            done.set()
        raise RuntimeError("all down")

    monkeypatch.setattr(service, "run_refresh", failing)
    stop = threading.Event()
    t = scheduler.start(FakeHistory(), None, wake=0.01, stop=stop)
    try:
        assert done.wait(5) and t.daemon
        assert calls[0] == ("auto", True)
    finally:
        stop.set()
        t.join(2)
    assert not t.is_alive()


def test_tuning_runs_after_auto_refresh(monkeypatch):
    from ninety_five import tuning
    ran = []
    monkeypatch.setattr(scheduler, "due", lambda *a: True)
    monkeypatch.setattr(service, "run_refresh", lambda *a, **k: {"matches": 3})
    monkeypatch.setattr(tuning, "run_all", lambda history, now: ran.append(now) or [])

    class H:
        def last_success(self, kind=None):
            return None

    scheduler.tick(H(), None, 7, T("2026-11-04 07:05"))
    assert len(ran) == 1
    monkeypatch.setattr(service, "run_refresh", lambda *a, **k: None)  # another refresh was running
    scheduler.tick(H(), None, 7, T("2026-11-04 07:05"))
    assert len(ran) == 1
