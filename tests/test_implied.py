import numpy as np
import pytest

from ninety_five import implied
from ninety_five.model import combo_probability, markets, overlap, score_matrix, settle
from ninety_five.value import Settings, devig_power, evaluate

PINNACLE = {"1": 3.72, "X": 3.72, "2": 2.03, "Over 2.5": 1.83, "Under 2.5": 2.07}


def test_fit_reproduces_pinnacles_fair_prices():
    lh, la = implied.fit_goals(PINNACLE)
    mk = markets(score_matrix(lh, la, implied.RHO))
    fair = dict(zip("1X2", devig_power([3.72, 3.72, 2.03])))
    over = devig_power([1.83, 2.07])[0]
    for m in "1X2":
        assert mk[m] == pytest.approx(fair[m], abs=0.01)
    assert mk["Over 2.5"] == pytest.approx(over, abs=0.01)
    assert la > lh  # Chelsea are the favourites


def test_fit_uses_any_half_goal_line_or_1x2_alone():
    assert implied.fit_goals({"1": 2.0, "X": 3.5, "2": 4.0, "Over 3.5": 3.0, "Under 3.5": 1.4}) is not None
    lh, la = implied.fit_goals({"1": 2.0, "X": 3.5, "2": 4.0})
    assert lh > la
    assert implied.fit_goals({"Over 2.5": 1.9, "Under 2.5": 1.9}) is None  # no 1X2: no fit


def test_combo_probability_counts_the_scores_where_every_leg_wins():
    m = score_matrix(1.15, 1.65, -0.05)
    i, j = np.indices(m.shape)
    assert combo_probability(m, ["X", "Under 2.5"]) == pytest.approx(m[(i == j) & (i + j < 3)].sum())
    mk = markets(m)
    # Draws and few goals go together: more likely than the product of the singles.
    assert combo_probability(m, ["X", "Under 2.5"]) > mk["X"] * mk["Under 2.5"]
    assert combo_probability(m, ["1", "2"]) == 0
    assert combo_probability(m, ["BTTS Yes"]) == pytest.approx(mk["BTTS Yes"])
    with pytest.raises(ValueError):
        combo_probability(m, ["Corners 9.5"])


def test_settle_combos_all_legs_must_win():
    assert settle("X + Under 2.5", 1, 1) is True
    assert settle("X + Under 2.5", 2, 2) is False
    assert settle("2 + BTTS No + Over 1.5", 0, 2) is True
    assert settle("2 + Corners 9.5", 0, 2) is None


def test_implied_probability_is_used_when_pinnacle_lacks_the_market():
    model = {"BTTS Yes": 0.60, "BTTS No": 0.40, "1": 0.3, "X": 0.3, "2": 0.4}
    sel = {s.market: s for s in evaluate(model, {"BTTS Yes": 1.9, "1": 3.0}, Settings(),
                                         sharp={"1": 0.26}, implied={"1": 0.27, "BTTS Yes": 0.56})}
    assert sel["1"].basis == "sharp" and sel["1"].p_blend == pytest.approx(0.26)  # Pinnacle's own price wins
    assert sel["BTTS Yes"].basis == "implied" and sel["BTTS Yes"].p_blend == pytest.approx(0.56)
    assert sel["BTTS Yes"].p_sharp == pytest.approx(0.56)


# ------------------------------------------------- quarter and whole goal lines
def test_line_probability_half_whole_and_quarter_lines():
    m = score_matrix(1.4, 1.1, implied.RHO)
    i, j = np.indices(m.shape)
    t = i + j
    P = lambda mask: float(m[mask].sum())
    assert implied.line_probability(m, "Over", 2.5) == pytest.approx(P(t > 2.5))
    # Whole line: a total of exactly 3 is a push (stake back).
    assert implied.line_probability(m, "Over", 3) == pytest.approx(P(t > 3) / (1 - P(t == 3)))
    # 2.75 = half on 2.5, half on 3.
    assert implied.line_probability(m, "Over", 2.75) == pytest.approx((P(t >= 3) + P(t >= 4)) / (2 - P(t == 3)))
    # 2.25 = half on 2, half on 2.5.
    assert implied.line_probability(m, "Under", 2.25) == pytest.approx((P(t < 2) + P(t < 3)) / (2 - P(t == 2)))
    # Both sides of a line add up to one.
    assert implied.line_probability(m, "Over", 2.75) + implied.line_probability(m, "Under", 2.75) == pytest.approx(1)


def _fair_prices(lh, la, line):
    m = score_matrix(lh, la, implied.RHO)
    mk = markets(m)
    prices = {k: 1 / mk[k] for k in "1X2"}
    for side in ("Over", "Under"):
        prices[f"{side} {line:g}"] = 1 / implied.line_probability(m, side, line)
    return prices


def test_fit_uses_a_quarter_goal_line():
    lh, la = implied.fit_goals(_fair_prices(1.6, 1.5, 2.75))
    assert lh == pytest.approx(1.6, abs=0.02) and la == pytest.approx(1.5, abs=0.02)
    lh, la = implied.fit_goals(_fair_prices(0.9, 0.8, 2.25))
    assert lh == pytest.approx(0.9, abs=0.02) and la == pytest.approx(0.8, abs=0.02)


def test_has_goal_line():
    assert implied.has_goal_line({"1": 2, "X": 3, "2": 4, "Over 2.75": 1.9, "Under 2.75": 1.9})
    assert not implied.has_goal_line({"1": 2, "X": 3, "2": 4, "Over 2.75": 1.9})  # one side only
    assert not implied.has_goal_line({"1": 2, "X": 3, "2": 4})


def test_goal_markets_priced_from_1x2_alone_get_a_caution():
    model = {"BTTS No": 0.5, "1X": 0.6, "1": 0.3, "X + Under 2.5": 0.2, "2 + X2": 0.3}
    odds = {"BTTS No": 2.1, "1X": 1.8, "1": 3.5, "X + Under 2.5": 5.6, "2 + X2": 3.6}
    implied_p = {"BTTS No": 0.5, "1X": 0.6, "X + Under 2.5": 0.2, "2 + X2": 0.3}
    sel = {s.market: s for s in evaluate(model, odds, Settings(), sharp={"1": 0.3}, implied=implied_p,
                                         goals_from_1x2=True)}
    b = sel["BTTS No"]  # 5% edge: no caution against a real Pinnacle price
    assert b.recommended and b.caution == "amber" and b.goals_from_1x2
    plain = {s.market: s for s in evaluate(model, odds, Settings(), sharp={"1": 0.3}, implied=implied_p)}
    assert plain["BTTS No"].caution == "" and b.stake == pytest.approx(round(plain["BTTS No"].stake / 2), abs=1)
    assert sel["X + Under 2.5"].goals_from_1x2  # a combo with a goals leg
    assert not sel["1X"].goals_from_1x2 and sel["1X"].caution == ""  # the 1X2 fixes double chance exactly
    assert not sel["2 + X2"].goals_from_1x2
    assert not sel["1"].goals_from_1x2


# ------------------------------------------------------------ overlapping bets
def test_overlap_of_bets_that_win_and_lose_together():
    m = score_matrix(1.04, 1.2, implied.RHO)
    i, j = np.indices(m.shape)
    t = i + j
    o = overlap(m, ["Under 2.5", "Under 1.5", "BTTS No"])
    none_win = ~((t < 2.5) | (t < 1.5) | (i == 0) | (j == 0))
    assert o["p_all_lose"] == pytest.approx(m[none_win].sum())
    mk = markets(m)
    assert o["p_all_lose_independent"] == pytest.approx((1 - mk["Under 2.5"]) * (1 - mk["Under 1.5"]) * (1 - mk["BTTS No"]))
    assert o["together"]
    assert not overlap(m, ["1", "2"])["together"]  # opposite bets: one of them often wins
