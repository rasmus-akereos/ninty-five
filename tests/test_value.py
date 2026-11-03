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
    st.settle(bid, 2, 1, closing_odds=1.8)
    s = st.summary()
    assert s["pnl"] == pytest.approx(10)
    assert s["avg_clv"] == pytest.approx(2.0 / 1.8 - 1, abs=1e-4)
