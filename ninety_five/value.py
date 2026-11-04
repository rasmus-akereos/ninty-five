"""Turn model probabilities and bookmaker odds into bet recommendations.

Method
------
1. Implied probability of decimal odds o is q = 1/o. Across a complete market
   (e.g. 1/X/2) the q's sum to more than 1; the excess is the bookmaker margin
   (overround).

2. Remove the margin with the *power method*: find k > 0 such that
   sum_i q_i^k = 1 and use p_i = q_i^k. Compared with simple proportional
   scaling this takes more margin off longshots than favourites, which matches
   the favourite-longshot bias seen in bookmaker pricing.

3. Shrink the model toward the market:
       p = alpha * p_model + (1 - alpha) * p_market
   A simple goals model knows nothing about injuries, line-ups, motivation or
   weather; the market does. With alpha = 1 almost every longshot looks like
   "value". alpha = 0.5 is a conservative default; only raise it once the bet
   log shows the model actually beats the closing prices.

4. Expected value per unit staked: EV = p * o - 1.

5. Stake with fractional Kelly. Full Kelly f* = (p*o - 1) / (o - 1) maximises
   long-run log growth *if p is correct*. p is never correct, and overbetting
   is far more damaging than underbetting, so we stake kelly_fraction * f*
   (default 0.25) and cap each bet at max_stake_pct of bankroll.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np
from scipy.optimize import brentq

# Markets whose outcomes are mutually exclusive and exhaustive.
COMPLETE_MARKETS = [
    ("1", "X", "2"),
    ("BTTS Yes", "BTTS No"),
    *[(f"Over {l}", f"Under {l}") for l in (0.5, 1.5, 2.5, 3.5, 4.5)],
]


@dataclass
class Settings:
    alpha: float = 0.5           # weight on model vs de-margined market
    kelly_fraction: float = 0.25
    max_stake_pct: float = 0.03  # of bankroll
    min_edge: float = 0.03       # minimum EV to recommend
    bankroll: float = 1000.0


@dataclass
class Selection:
    market: str
    odds: float
    p_model: float
    p_market: float | None
    p_blend: float
    fair_odds: float
    ev: float
    kelly: float
    stake: float
    recommended: bool

    def to_dict(self) -> dict:
        return asdict(self)


def overround(odds: list[float]) -> float:
    return float(sum(1 / o for o in odds) - 1)


def devig_power(odds: list[float]) -> list[float]:
    q = np.array([1 / o for o in odds])
    if q.sum() <= 1:  # no margin (or a mistyped price): just normalise
        return list(q / q.sum())
    k = brentq(lambda k: np.sum(q**k) - 1, 1.0, 50.0)
    return list(q**k)


def market_probabilities(odds: dict[str, float]) -> dict[str, float]:
    """De-margined probabilities for every complete market present in `odds`."""
    out: dict[str, float] = {}
    for group in COMPLETE_MARKETS:
        if all(g in odds for g in group):
            for g, p in zip(group, devig_power([odds[g] for g in group])):
                out[g] = p
    # Double chance is priced off the de-margined 1X2 market.
    if all(k in out for k in ("1", "X", "2")):
        out["1X"], out["12"], out["X2"] = out["1"] + out["X"], out["1"] + out["2"], out["X"] + out["2"]
    return out


def evaluate(model_probs: dict[str, float], odds: dict[str, float], s: Settings) -> list[Selection]:
    market = market_probabilities(odds)
    rows = []
    for name, o in odds.items():
        if name not in model_probs or o <= 1:
            continue
        pm = float(model_probs[name])
        pk = None if market.get(name) is None else float(market[name])
        # Without a complete market we cannot de-margin; fall back to model only
        # but this is flagged in the UI because the edge is then less reliable.
        p = pm if pk is None else s.alpha * pm + (1 - s.alpha) * pk
        ev = float(p * o - 1)
        kelly = max(ev / (o - 1), 0.0)
        stake_pct = min(s.kelly_fraction * kelly, s.max_stake_pct)
        rec = ev >= s.min_edge
        # Whole kroner: that's how stakes are placed. A recommended bet never
        # rounds down to nothing.
        stake = float(max(1, round(stake_pct * s.bankroll))) if rec and stake_pct > 0 else 0.0
        rows.append(
            Selection(
                market=name,
                odds=o,
                p_model=pm,
                p_market=pk,
                p_blend=p,
                fair_odds=1 / p if p > 0 else float("inf"),
                ev=ev,
                kelly=kelly,
                stake=stake,
                recommended=bool(rec),
            )
        )
    return sorted(rows, key=lambda r: r.ev, reverse=True)
