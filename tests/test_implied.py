import numpy as np
import pytest

from ninety_five import implied
from ninety_five.model import combo_probability, markets, score_matrix, settle
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
