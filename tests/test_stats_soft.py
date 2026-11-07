import pytest

from ninety_five import stats


def _r(comp="England", market="1", gap=-0.05, basis="sharp"):
    return {"competition": comp, "market": market, "gap": gap, "basis": basis}


def test_soft_prices_groups_by_league_and_market():
    rows = [_r(gap=-0.06), _r(gap=0.02), _r(comp="Denmark 1", market="BTTS No", gap=0.03, basis="implied")]
    out = stats.soft_prices(rows)
    assert out["n"] == 3 and out["above"] == pytest.approx(2 / 3, abs=1e-3)
    eng = next(g for g in out["by_league"] if g["label"] == "England")
    assert eng["n"] == 2 and eng["avg_gap"] == pytest.approx(-0.02) and eng["above"] == pytest.approx(0.5)
    assert eng["best"] == pytest.approx(0.02) and eng["thin"]
    btts = next(g for g in out["by_market"] if g["label"] == "BTTS")
    assert btts["n"] == 1 and btts["estimated"] == 1  # priced from implied goals, not Pinnacle's own price
    # Softest first.
    assert [g["label"] for g in out["by_league"]] == ["Denmark 1", "England"]


def test_soft_prices_empty():
    out = stats.soft_prices([])
    assert out == {"n": 0, "avg_gap": None, "above": None, "by_league": [], "by_market": [], "top": [], "excluded": 0}


def test_far_off_prices_are_left_out_as_likely_typos():
    out = stats.soft_prices([_r(gap=-0.05), _r(gap=1.11), _r(gap=-0.45)])
    assert out["n"] == 1 and out["excluded"] == 2
