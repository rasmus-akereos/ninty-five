"""Expected goals implied by a sharp bookmaker's prices.

Pinnacle (via The Odds API) gives us 1X2 and usually one over/under line,
but not BTTS, the other goal lines or same-match combinations. Find the home
and away expected goals whose Dixon-Coles score grid reproduces Pinnacle's
de-margined 1X2 and over/under probabilities, then price everything else
from that grid. This is an estimate, not Pinnacle's own price for those
markets, but it starts from the market's view of the match rather than from
our goals model.

RHO is a typical Dixon-Coles low-score correction for club football; it
can't be pinned down from three prices.
"""

from __future__ import annotations

import numpy as np
from scipy.optimize import minimize

from .model import markets, score_matrix
from .value import market_probabilities

RHO = -0.05


def fit_goals(prices: dict[str, float]) -> tuple[float, float] | None:
    """(home, away) expected goals fitting the de-margined 1X2 (required)
    and any complete half-goal over/under line in `prices` ({market: odds})."""
    target = {m: p for m, p in market_probabilities(prices).items()
              if m in ("1", "X", "2") or m.startswith(("Over ", "Under "))}
    if not all(k in target for k in ("1", "X", "2")):
        return None

    def loss(x):
        mk = markets(score_matrix(np.exp(x[0]), np.exp(x[1]), RHO))
        return sum((mk[m] - p) ** 2 for m, p in target.items() if m in mk)

    x = minimize(loss, [0.3, 0.1], method="Nelder-Mead", options={"xatol": 1e-5, "fatol": 1e-10}).x
    return float(np.exp(x[0])), float(np.exp(x[1]))


def grid(prices: dict[str, float]) -> tuple[tuple[float, float], np.ndarray] | None:
    """The fitted expected goals and their score grid, or None without a 1X2."""
    goals = fit_goals(prices)
    return None if goals is None else (goals, score_matrix(goals[0], goals[1], RHO))
