import numpy as np
import pandas as pd
import pytest

from ninety_five import service, tuning
from ninety_five.history import HistoryStore

NOW = pd.Timestamp("2026-11-04T07:00Z")


def _losses(current, better=None, n=600, seed=3):
    """Per-match losses per candidate; `better` gets clearly lower losses."""
    rng = np.random.default_rng(seed)
    base = rng.normal(1.0, 0.3, n)
    cands = tuning.half_life_candidates(current)
    out = {h: base + rng.normal(0, 0.01, n) for h in cands}
    if better is not None:
        out[better] = out[better] - 0.05
    kick = np.array([(NOW - pd.Timedelta(hours=n - i)).isoformat() for i in range(n)])
    return out, kick


def test_candidates_and_bounds():
    assert tuning.half_life_candidates(365) == [243.0, 365.0, 548.0]
    assert max(tuning.half_life_candidates(1095)) == 1095
    assert min(tuning.half_life_candidates(180)) == 180


def test_decide_half_life_moves_one_step():
    losses, kick = _losses(365, better=548.0)
    d = tuning.decide_half_life(losses, kick, 365)
    assert d.action == "changed" and d.new == 548 and d.setting == "half_life" and d.scope == "club"


def test_decide_half_life_keeps_on_tie():
    losses, kick = _losses(365)
    d = tuning.decide_half_life(losses, kick, 365)
    assert d.action == "kept" and d.new == 365


def test_run_half_life_monthly(tmp_path, monkeypatch):
    h = HistoryStore(tmp_path / "h.sqlite")
    calls = []

    def fake(current, months=tuning.HL_MONTHS):
        calls.append(current)
        return _losses(current, better=548.0)

    monkeypatch.setattr(tuning, "half_life_losses", fake)
    applied = []
    monkeypatch.setattr(service, "set_club_half_life", lambda d: applied.append(d))
    d = tuning.run_half_life(h, NOW)
    assert d.action == "changed" and h.tuned()[("half_life", "club")] == 548 and applied == [548]
    assert tuning.run_half_life(h, NOW + pd.Timedelta(days=10)) is None
    assert tuning.run_half_life(h, NOW + pd.Timedelta(days=31)) is not None
    assert calls == [365.0, 548.0]


def test_run_half_life_respects_revert_block(tmp_path, monkeypatch):
    h = HistoryStore(tmp_path / "h.sqlite")
    monkeypatch.setattr(tuning, "half_life_losses", lambda current, months=6: _losses(current, better=548.0))
    monkeypatch.setattr(service, "set_club_half_life", lambda d: None)
    tuning.run_half_life(h, NOW)
    reverted = NOW + pd.Timedelta(days=15)
    h.revert(h.last_tuning("half_life", "club", action="changed")["id"], reverted.isoformat())
    assert tuning.run_half_life(h, NOW + pd.Timedelta(days=31)) is None   # a month since the test, but blocked
    assert tuning.run_half_life(h, reverted + pd.Timedelta(days=31)) is not None


def test_run_all_swallows_errors(tmp_path, monkeypatch):
    h = HistoryStore(tmp_path / "h.sqlite")

    def boom(history, now):
        raise RuntimeError("bad data")

    monkeypatch.setattr(tuning, "run_alpha", boom)
    monkeypatch.setattr(tuning, "run_half_life", lambda history, now: None)
    assert tuning.run_all(h, NOW) == []


def test_run_all_skips_when_lease_held(tmp_path, monkeypatch):
    h = HistoryStore(tmp_path / "h.sqlite")
    h.take_lease("tuning", "someone-else", NOW)
    monkeypatch.setattr(tuning, "run_alpha", lambda history, now: pytest.fail("ran without the lease"))
    assert tuning.run_all(h, NOW) == []


def test_set_club_half_life_clears_cache(monkeypatch):
    monkeypatch.setitem(service._models, "ENG", (0.0, None, None))
    old = service.club_params()["half_life_days"]
    try:
        service.set_club_half_life(500)
        assert service.club_params()["half_life_days"] == 500 and "ENG" not in service._models
    finally:
        service.set_club_half_life(old)


def test_match_losses_mean_is_log_loss():
    from ninety_five.model import CLUB_PARAMS, DixonColes, log_loss_1x2, match_losses_1x2
    rng = np.random.default_rng(0)
    rows = [(pd.Timestamp("2026-01-01") + pd.Timedelta(days=i), *rng.choice(list("ABCD"), 2, replace=False),
             rng.poisson(1.4), rng.poisson(1.1), False, "L") for i in range(80)]
    df = pd.DataFrame(rows, columns=["date", "home", "away", "hg", "ag", "neutral", "competition"])
    m = DixonColes(**CLUB_PARAMS).fit(df)
    ls = match_losses_1x2(m, df)
    assert len(ls) == len(df) and log_loss_1x2(m, df) == pytest.approx(float(np.nanmean(ls)))


def test_run_all_keeps_going_after_one_step_fails(tmp_path, monkeypatch):
    h = HistoryStore(tmp_path / "h.sqlite")
    monkeypatch.setattr(tuning, "run_alpha", lambda history, now: (_ for _ in ()).throw(RuntimeError("x")))
    d = tuning.Decision("half_life", "club", 365, 365, "kept", 10)
    monkeypatch.setattr(tuning, "run_half_life", lambda history, now: d)
    assert tuning.run_all(h, NOW) == [d]
    assert h.take_lease("tuning", "next", NOW)  # released afterwards


def test_apply_stored_sets_half_life(tmp_path, monkeypatch):
    h = HistoryStore(tmp_path / "h.sqlite")
    applied = []
    monkeypatch.setattr(service, "set_club_half_life", lambda d: applied.append(d))
    tuning.apply_stored(h)
    assert applied == []
    h.set_tuned("half_life", "club", 548, NOW.isoformat())
    tuning.apply_stored(h)
    assert applied == [548]
