import pytest

from ninty_five.store import BetStore, open_outlook, take_profit_above


def _bet(stake, odds, p, home="A", away="B", market="1"):
    return {"stake": stake, "odds": odds, "p_blend": p, "p_model": p, "ev": p * odds - 1,
            "home": home, "away": away, "market": market, "status": "open"}


def test_take_profit_above_is_stake_times_odds_times_chance():
    assert take_profit_above(_bet(12, 3.05, 0.36)) == pytest.approx(13.176)
    assert take_profit_above({**_bet(10, 2.0, 0.5), "p_blend": None}) == pytest.approx(10)  # falls back to model
    assert take_profit_above({**_bet(10, 2.0, 0.5), "p_blend": None, "p_model": None}) is None


def test_outlook_numbers():
    bets = [_bet(10, 2.0, 0.6), _bet(20, 1.5, 0.7, home="C", away="D")]
    o = open_outlook(bets, n=20000, seed=0)
    assert o["staked"] == 30 and o["n"] == 2
    assert o["expected_pnl"] == pytest.approx(10 * 0.2 + 20 * 0.05)
    assert o["expected_return"] == pytest.approx(3 / 30)
    assert o["worst"] == -30 and o["best"] == pytest.approx(20)
    assert o["p_profit"] == pytest.approx(0.6 * 0.7 + 0.6 * 0.3 * 0 + 0.4 * 0.7 * 0, abs=0.02)  # only both-win is +
    assert o["p10"] <= o["expected_pnl"] <= o["p90"]


def test_outlook_groups_exposure_by_match():
    bets = [_bet(25, 1.5, 0.7, market="2"), _bet(9, 2.4, 0.4, market="BTTS No"), _bet(12, 3.0, 0.4, home="C", away="D")]
    o = open_outlook(bets)
    assert o["by_match"][0] == {"match": "A – B", "stake": 34, "n": 2}
    assert o["by_match"][1]["match"] == "C – D"


def test_outlook_empty():
    assert open_outlook([]) is None


def test_list_and_summary_carry_outlook(tmp_path):
    st = BetStore(tmp_path / "b.sqlite")
    st.add({"dataset": "EUROPE", "home": "A", "away": "B", "market": "1", "odds": 3.05, "stake": 12,
            "p_model": 0.4, "p_blend": 0.36, "ev": 0.098})
    [b] = st.list()
    assert b["take_profit_above"] == pytest.approx(13.176)
    assert st.summary()["outlook"]["staked"] == 12
