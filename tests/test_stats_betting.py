import pytest

from ninety_five import stats


def _b(**kw):
    b = {"id": 1, "dataset": "EUROPE", "home": "A", "away": "B", "market": "1", "odds": 2.0, "stake": 10.0,
         "p_model": 0.5, "p_blend": 0.5, "ev": 0.05, "closing_odds": None, "home_goals": None,
         "away_goals": None, "status": "open", "pnl": None, "match_id": None, "kickoff": None,
         "match_key": None, "created": "2026-11-01T08:00:00+00:00"}
    b.update(kw)
    return b


def test_tiles_count_settled_only():
    bets = [_b(status="won", pnl=10.0), _b(status="lost", pnl=-10.0), _b(status="took profit", pnl=4.0),
            _b(status="void", pnl=0.0), _b(status="open")]
    t = stats.betting(bets, {})["tiles"]
    assert t["n_settled"] == 3 and t["n_open"] == 1
    assert t["staked"] == 30 and t["pnl"] == 4
    assert t["roi"] == pytest.approx(4 / 30, abs=1e-4)
    assert t["avg_return"] == pytest.approx(4 / 3, abs=0.01)


def test_manual_bet_without_ev_or_kickoff():
    s = stats.betting([_b(status="won", pnl=10.0, ev=None)], {})
    assert s["tiles"]["expected_pnl"] == 0
    assert s["curve"][0]["date"] == "2026-11-01"
    assert s["by_league"][0]["label"] == "EUROPE"


def test_days_use_copenhagen_date():
    s = stats.betting([_b(created="2026-11-01T23:30:00+00:00")], {})
    assert s["days"][0]["date"] == "2026-11-02"


def test_per_day_averages_over_active_days():
    a = "2026-11-01T08:00:00+00:00"
    bets = [_b(created=a), _b(created=a), _b(created=a), _b(created="2026-11-03T08:00:00+00:00", stake=20.0)]
    t = stats.betting(bets, {})["tiles"]
    assert t["bets_per_day"] == 2.0
    assert t["stake_per_day"] == 25.0


def test_curve_cumulative_and_expected():
    bets = [_b(status="lost", pnl=-5.0, ev=0.1, kickoff="2026-11-02T15:00:00+00:00"),
            _b(status="won", pnl=10.0, ev=0.1, kickoff="2026-11-01T15:00:00+00:00")]
    c = stats.betting(bets, {})["curve"]
    assert [x["pnl_cum"] for x in c] == [10, 5]
    assert [x["expected_cum"] for x in c] == pytest.approx([1.0, 2.0])


def test_groups_market_odds_league():
    w = {"status": "won", "pnl": 10.0}  # tables cover settled bets
    bets = [_b(market="1", odds=1.79, **w), _b(market="Over 2.5", odds=1.80, **w), _b(market="BTTS No", odds=2.50, **w),
            _b(market="X2", odds=4.00, **w), _b(market="Handicap +1", match_key="fd:1", **w), _b(market="2")]
    s = stats.betting(bets, {"fd:1": "England 2"})
    assert {g["label"] for g in s["by_market"]} == {"1X2", "Over/Under", "BTTS", "Double chance", "Other"}
    assert {g["label"] for g in s["by_odds"]} == {"< 1.80", "1.80–2.49", "2.50–3.99", "4.00+"}
    leagues = {g["label"]: g for g in s["by_league"]}
    assert leagues["England 2"]["n"] == 1 and leagues["EUROPE"]["n"] == 4
    assert all(g["thin"] for g in s["by_league"])
    assert s["by_league"][0]["label"] == "EUROPE"  # sorted by n, largest first


def test_vs_pinnacle_averages_bets_with_a_price_before_kickoff():
    bets = [_b(id=1, odds=2.2, status="won", pnl=12.0), _b(id=2, odds=1.8, status="lost", pnl=-10.0),
            _b(id=3, odds=3.0), _b(id=4, status="void", pnl=0.0)]
    # Pinnacle's fair probability at kick-off for bets 1 and 2 (3 has none, 4 is void).
    t = stats.betting(bets, {}, sharp_at_kickoff={1: 0.5, 2: 0.5, 4: 0.5})["tiles"]
    assert t["n_vs_pinnacle"] == 2
    assert t["avg_vs_pinnacle"] == pytest.approx(((2.2 * 0.5 - 1) + (1.8 * 0.5 - 1)) / 2)


def test_vs_pinnacle_empty_without_prices():
    t = stats.betting([_b()], {})["tiles"]
    assert t["avg_vs_pinnacle"] is None and t["n_vs_pinnacle"] == 0


def test_combos_are_their_own_market_group():
    assert stats.market_group("X + Under 2.5") == "Bet Builder"
