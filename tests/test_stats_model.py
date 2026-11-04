import math

import pytest

from ninety_five import stats


def _m(home="H", away="A", p=(0.5, 0.3, 0.2), hg=1, ag=0, xg=(1.5, 1.0), comp="England 2", probs=None):
    return {"home": home, "away": away, "hg": hg, "ag": ag, "competition": comp,
            "xg": list(xg) if xg else None, "odds": {},
            "probabilities": probs if probs is not None else {"1": p[0], "X": p[1], "2": p[2]}}


def _team(out, name):
    return next(t for t in out["teams"] if t["team"] == name)


def test_expected_points_and_sd():
    out = stats.model_insight([_m()])
    h, a = _team(out, "H"), _team(out, "A")
    assert h["exp_pts"] == pytest.approx(1.8) and h["pts"] == 3
    assert h["sd"] == pytest.approx(math.sqrt(0.5 * 9 + 0.3 - 1.8 ** 2), abs=1e-3)
    assert a["exp_pts"] == pytest.approx(0.9) and a["pts"] == 0
    assert h["xg_for"] == 1.5 and h["goals_for"] == 1 and h["xg_against"] == 1.0 and h["goals_against"] == 0


def test_verdicts():
    lose = [_m(home="T", away=f"O{i}", p=(0.6, 0.2, 0.2), hg=0, ag=1) for i in range(15)]
    assert _team(stats.model_insight(lose), "T")["verdict"] == "overrates"
    assert _team(stats.model_insight(lose[:14]), "T")["verdict"] == "too few"
    results = [(1, 0)] * 9 + [(1, 1)] * 3 + [(0, 1)] * 3
    mixed = [_m(home="T", away=f"O{i}", p=(0.6, 0.2, 0.2), hg=hg, ag=ag) for i, (hg, ag) in enumerate(results)]
    assert _team(stats.model_insight(mixed), "T")["verdict"] == "in line"
    win = [_m(home="T", away=f"O{i}", p=(0.2, 0.2, 0.6), hg=1, ag=0) for i in range(15)]
    assert _team(stats.model_insight(win), "T")["verdict"] == "underrates"


def test_skips_partial_probabilities_and_missing_xg():
    out = stats.model_insight([_m(), _m(probs={"1": 0.5, "2": 0.2}), _m(xg=None, hg=2)])
    h = _team(out, "H")
    assert h["n"] == 2 and h["pts"] == 6
    assert h["xg_for"] == 1.5 and h["goals_for"] == 1


def test_leagues_grouped_by_competition():
    out = stats.model_insight([_m(), _m(home="X", away="Y", comp="Spain"), _m(home="X", away="Z", comp="Spain")])
    leagues = {l["competition"]: l for l in out["leagues"]}
    assert leagues["Spain"]["n"] == 2 and leagues["England 2"]["n"] == 1
    assert leagues["Spain"]["track"]["n"] == 2
    assert leagues["England 2"]["exp_pts_home"] == pytest.approx(1.8) and leagues["England 2"]["pts_home"] == 3


def test_empty():
    assert stats.model_insight([]) == {"teams": [], "leagues": []}
