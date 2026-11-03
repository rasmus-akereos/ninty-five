import pandas as pd
import pytest

from ninty_five.store import BetStore
from ninty_five.value import Settings, devig_power, evaluate, overround


def test_devig_sums_to_one_and_shades_longshots():
    odds = [1.5, 4.0, 7.0]
    p = devig_power(odds)
    assert sum(p) == pytest.approx(1)
    prop = [(1 / o) / sum(1 / x for x in odds) for o in odds]
    assert p[0] > prop[0] and p[2] < prop[2]


def test_overround():
    assert overround([1.9, 1.9]) == pytest.approx(2 / 1.9 - 1)


def test_evaluate_blend_ev_and_stake():
    probs = {"1": 0.55, "X": 0.25, "2": 0.20}
    odds = {"1": 2.10, "X": 3.40, "2": 3.80}
    s = Settings(alpha=0.5, bankroll=1000, kelly_fraction=0.25, max_stake_pct=0.03, min_edge=0.03)
    rows = {r.market: r for r in evaluate(probs, odds, s)}
    home = rows["1"]
    assert home.p_blend == pytest.approx(0.5 * 0.55 + 0.5 * home.p_market)
    assert home.ev == pytest.approx(home.p_blend * 2.10 - 1)
    assert home.recommended
    assert 0 < home.stake <= 30
    assert home.stake == int(home.stake)  # whole kroner
    assert not rows["2"].recommended and rows["2"].stake == 0


def test_store_roundtrip(tmp_path):
    st = BetStore(tmp_path / "b.sqlite")
    bid = st.add({"dataset": "INTL", "home": "A", "away": "B", "market": "Over 2.5", "odds": 2.0, "stake": 10, "ev": 0.05})
    assert st.summary()["open_staked"] == 10  # open bets count before they settle
    assert st.summary()["open_expected_pnl"] == pytest.approx(0.5)
    st.settle(bid, 2, 1, closing_odds=1.8)
    s = st.summary()
    assert s["staked"] == 10 and s["open_staked"] == 0
    assert s["pnl"] == pytest.approx(10)
    assert s["avg_clv"] == pytest.approx(2.0 / 1.8 - 1, abs=1e-4)


def test_took_profit_counts_payout_minus_stake(tmp_path):
    st = BetStore(tmp_path / "b.sqlite")
    bid = st.add({"dataset": "EUROPE", "home": "A", "away": "B", "market": "Over 2.5", "odds": 1.33, "stake": 25,
                  "match_id": 7})
    b = st.settle(bid, cash_out=30.5)
    assert b["status"] == "took profit" and b["pnl"] == pytest.approx(5.5)
    s = st.summary()
    assert s["staked"] == 25 and s["pnl"] == pytest.approx(5.5) and s["open_staked"] == 0
    # auto-settlement never overrides a closed bet
    assert st.auto_settle(pd.DataFrame([{"match_id": 7, "hg": 0, "ag": 0}])) == []
    assert st.list()[0]["status"] == "took profit"
