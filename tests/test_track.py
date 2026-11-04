import math

import pytest

from ninety_five import track


def _m(hg, ag, p, odds=None):
    return {"hg": hg, "ag": ag, "odds": odds or {},
            "probabilities": {"1": p[0], "X": p[1], "2": p[2], "Over 2.5": 0.5, "Under 2.5": 0.5}}


def test_summary_logloss_and_head_to_head():
    ms = [_m(1, 0, (0.5, 0.3, 0.2), {"1": 2.5, "X": 3.2, "2": 3.0}),   # market ~0.38 on home: model closer
          _m(0, 2, (0.5, 0.3, 0.2), {"1": 1.5, "X": 4.0, "2": 7.0}),   # market lower on away: model closer
          _m(1, 1, (0.4, 0.3, 0.3))]                                   # no odds: not compared
    s = track.summary(ms)["1X2"]
    assert s["n"] == 3 and s["n_compared"] == 2
    assert s["model_logloss"] == pytest.approx(-(math.log(0.5) + math.log(0.2) + math.log(0.3)) / 3)
    assert s["model_closer"] == 1.0 and s["market_closer"] == 0.0
    assert s["model_logloss_compared"] < s["market_logloss_compared"]


def test_over_under_uses_total_goals():
    s = track.summary([_m(2, 1, (0.4, 0.3, 0.3), {"Over 2.5": 1.8, "Under 2.5": 2.0})])["OU25"]
    assert s["n"] == 1 and s["n_compared"] == 1
    assert s["model_logloss"] == pytest.approx(-math.log(0.5))
    assert s["model_closer"] + s["market_closer"] == 1.0  # market (~0.53 on Over) was closer


def test_calibration_bands():
    s = track.summary([_m(1, 0, (0.55, 0.25, 0.2))])["1X2"]["calibration"]
    assert len(s) == 10
    band = next(b for b in s if b["lo"] == 0.5)
    assert band["n"] == 1 and band["freq"] == 1.0 and band["avg_pred"] == pytest.approx(0.55)
    low = next(b for b in s if b["lo"] == 0.2)
    assert low["n"] == 2 and low["freq"] == 0.0
    assert next(b for b in s if b["lo"] == 0.9)["n"] == 0


def test_summary_empty():
    s = track.summary([])
    assert s["1X2"]["n"] == 0 and s["1X2"]["model_logloss"] is None and s["OU25"]["n_compared"] == 0
