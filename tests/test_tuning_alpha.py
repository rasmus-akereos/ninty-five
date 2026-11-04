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
