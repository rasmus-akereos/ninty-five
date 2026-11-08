"""Expected goals implied by a sharp bookmaker's prices.

Pinnacle (via The Odds API) gives us 1X2 and usually one over/under line
(often a quarter line such as 2.75, which is half on 2.5 and half on 3),
but not BTTS, the other goal lines or same-match combinations. Find the home
and away expected goals whose Dixon-Coles score grid reproduces Pinnacle's
de-margined 1X2 and over/under probabilities, then price everything else
from that grid. This is an estimate, not Pinnacle's own price for those
markets, but it starts from the market's view of the match rather than from
our goals model.

Without any goals line the total goals rest on the 1X2 alone, which says
little about them (has_goal_line); goal bets priced that way get a caution.

RHO is a typical Dixon-Coles low-score correction for club football; it
can't be pinned down from three prices.
"""

from __future__ import annotations

import numpy as np
from scipy.optimize import minimize

from .model import markets, score_matrix
from .value import devig_power, market_probabilities

RHO = -0.05
GOALS_SIDES = ("Over ", "Under ")


def _line_halves(point: float) -> tuple[float, float]:
    """A quarter line (2.25, 2.75) is half the stake on each neighbouring
    line; half and whole lines are one line."""
    return (point - 0.25, point + 0.25) if (point * 4) % 2 == 1 else (point, point)


def line_probability(m: np.ndarray, side: str, point: float) -> float:
    """The probability equivalent of an over/under line's fair price (1 / fair
    odds) from a score grid. Whole lines push (stake back) on the exact total;
    quarter lines split the stake over the two neighbouring lines."""
    i, j = np.indices(m.shape)
    total = i + j
    win = push = 0.0
    for line in _line_halves(point):
        win += float(m[total > line].sum() if side == "Over" else m[total < line].sum())
        push += float(m[total == line].sum())
    return win / (2 - push)


def _goal_lines(prices: dict[str, float]) -> dict[tuple[str, float], float]:
    """{(side, point): de-margined probability} for every over/under line in
    `prices` (any multiple of 0.25) quoted on both sides."""
    out = {}
    for k in prices:
        if not k.startswith("Over "):
            continue
        try:
            point = float(k[5:])
        except ValueError:
            continue
        under = f"Under {k[5:]}"
        if (point * 4) % 1 or under not in prices:
            continue
        p_over, p_under = devig_power([prices[k], prices[under]])
        out[("Over", point)], out[("Under", point)] = p_over, p_under
    return out


def has_goal_line(prices: dict[str, float]) -> bool:
    """True when the prices include a complete over/under line, so the fitted
    total goals rests on a goals price rather than on the 1X2 alone."""
    return bool(_goal_lines(prices))


def fit_goals(prices: dict[str, float]) -> tuple[float, float] | None:
    """(home, away) expected goals fitting the de-margined 1X2 (required)
    and every complete over/under line in `prices` ({market: odds}): half,
    whole and quarter lines."""
    target = {m: p for m, p in market_probabilities(prices).items() if m in ("1", "X", "2")}
    if len(target) < 3:
        return None
    lines = _goal_lines(prices)

    def loss(x):
        m = score_matrix(np.exp(x[0]), np.exp(x[1]), RHO)
        mk = markets(m)
        return (sum((mk[k] - p) ** 2 for k, p in target.items())
                + sum((line_probability(m, side, point) - p) ** 2 for (side, point), p in lines.items()))

    x = minimize(loss, [0.3, 0.1], method="Nelder-Mead", options={"xatol": 1e-5, "fatol": 1e-10}).x
    return float(np.exp(x[0])), float(np.exp(x[1]))


def grid(prices: dict[str, float]) -> tuple[tuple[float, float], np.ndarray] | None:
    """The fitted expected goals and their score grid, or None without a 1X2."""
    goals = fit_goals(prices)
    return None if goals is None else (goals, score_matrix(goals[0], goals[1], RHO))
