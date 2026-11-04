import numpy as np
import pandas as pd
import pytest

from ninety_five import tuning


def _evidence(n, model_noise=0.0, market_noise=0.8, seed=1):
    """Rows {kickoff, competition, pm, pk}: outcomes drawn from the true
    probabilities; the model and the market are the truth mixed with noise."""
    rng = np.random.default_rng(seed)
    rows = []
    for i in range(n):
        true = rng.dirichlet([4, 3, 3])
        pm = (1 - model_noise) * true + model_noise * rng.dirichlet([1, 1, 1])
        pk = (1 - market_noise) * true + market_noise * rng.dirichlet([1, 1, 1])
        k = rng.choice(3, p=true)
        rows.append({"kickoff": (pd.Timestamp("2026-01-01T15:00Z") + pd.Timedelta(hours=i)).isoformat(),
                     "competition": "England 2", "pm": float(pm[k]), "pk": float(pk[k])})
    return rows


def test_waits_below_100():
    d = tuning.decide_alpha(_evidence(99), 0.5, "all")
    assert d.action == "waiting" and d.new == 0.5 and d.n == 99


def test_moves_toward_model_when_model_better():
    d = tuning.decide_alpha(_evidence(400), 0.5, "all")
    assert d.action == "changed" and d.new == pytest.approx(0.6)
    assert d.loss_new < d.loss_old


def test_keeps_when_no_clear_gain():
    rows = [{**r, "pk": r["pm"]} for r in _evidence(300)]
    d = tuning.decide_alpha(rows, 0.5, "all")
    assert d.action == "kept" and d.new == 0.5


def test_respects_bounds():
    d = tuning.decide_alpha(_evidence(400), 0.8, "all")
    assert d.new <= 0.8 and d.action == "kept"


def test_moves_toward_market_when_market_better():
    d = tuning.decide_alpha(_evidence(400, model_noise=0.8, market_noise=0.0), 0.5, "all")
    assert d.action == "changed" and d.new == pytest.approx(0.4)


def test_evidence_skips_partial_odds():
    full = {"hg": 1, "ag": 0, "kickoff": "2026-11-01T15:00:00+00:00", "competition": "England 2",
            "probabilities": {"1": 0.5, "X": 0.3, "2": 0.2}, "odds": {"1": 2.0, "X": 3.4, "2": 4.0}}
    partial = {**full, "odds": {"1": 2.0, "X": 3.4}}
    no_probs = {**full, "probabilities": {"1": 0.5}}
    ev = tuning.alpha_evidence([full, partial, no_probs])
    assert len(ev) == 1 and ev[0]["pm"] == 0.5 and 0.4 < ev[0]["pk"] < 0.5


def _finished(n, comp="England 2", seed=2):
    """HistoryStore.finished()-shaped rows where the model is the truth and the
    typed odds are a noisy market with a 5% margin."""
    rng = np.random.default_rng(seed)
    out = []
    for i in range(n):
        true = rng.dirichlet([4, 3, 3])
        mkt = 0.2 * true + 0.8 * rng.dirichlet([1, 1, 1])
        k = rng.choice(3, p=true)
        hg, ag = [(1, 0), (1, 1), (0, 1)][k]
        out.append({"kickoff": (pd.Timestamp("2026-01-01T15:00Z") + pd.Timedelta(hours=i)).isoformat(),
                    "competition": comp, "hg": hg, "ag": ag,
                    "probabilities": dict(zip("1X2", map(float, true))),
                    "odds": dict(zip("1X2", map(float, 1 / (mkt * 1.05))))})
    return out


def test_alpha_for_prefers_league_then_overall_then_default():
    tuned = {("alpha", "all"): 0.4, ("alpha", "England 2"): 0.7}
    assert tuning.alpha_for(tuned, "England 2") == (0.7, "England 2")
    assert tuning.alpha_for(tuned, "Spain") == (0.4, "all")
    assert tuning.alpha_for(tuned, None) == (0.4, "all")
    assert tuning.alpha_for({}, "Spain") == (0.5, "default")


def test_run_alpha_changes_logs_and_skips_blocked(tmp_path, monkeypatch):
    from ninety_five.history import HistoryStore
    h = HistoryStore(tmp_path / "h.sqlite")
    monkeypatch.setattr(h, "finished", lambda competition=None: _finished(400))
    now = pd.Timestamp("2026-11-04T07:00Z")
    ds = tuning.run_alpha(h, now)
    overall = next(d for d in ds if d.scope == "all")
    assert overall.action == "changed" and h.tuned()[("alpha", "all")] == pytest.approx(0.6)
    assert any(d.scope == "England 2" for d in ds)  # 400 matches in that league: tested too
    change = h.last_tuning("alpha", "all", action="changed")
    h.revert(change["id"], now.isoformat())
    later = tuning.run_alpha(h, now + pd.Timedelta(days=1))
    assert all(d.scope != "all" for d in later)
    assert h.tuned()[("alpha", "all")] == 0.5


def test_run_alpha_waiting_logs_nothing(tmp_path, monkeypatch):
    from ninety_five.history import HistoryStore
    h = HistoryStore(tmp_path / "h.sqlite")
    monkeypatch.setattr(h, "finished", lambda competition=None: _finished(40))
    assert [d.action for d in tuning.run_alpha(h, pd.Timestamp("2026-11-04T07:00Z"))] == ["waiting"]
    assert h.tuning_log() == []
