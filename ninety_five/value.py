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

6. Distrust big disagreements. When the model rates an outcome gap_warn
   (15 points) or more above the de-margined market, the market usually knows
   something the model doesn't (injuries, line-ups, form), yet the "edge" and
   so the stake grow with the gap. Such stakes are halved, and quartered from
   gap_warn + 10 points.

7. Cap the match. Markets on one match move together (Under 2.5, BTTS No and
   the draw are one opinion), so all bets on a match together stay within
   match_limit_pct of bankroll: each stake is cut to the room left.

8. Sharp prices first. When a sharp bookmaker's (Pinnacle's) price for the
   market is known (sharp_odds.py), its de-margined probability replaces the
   blend: a walk-forward test showed it beats both the model and any blend
   with it. The model is still reported. Against a sharp price a large edge
   usually means a stale or mistyped price, or news the sharp price predates,
   so stakes are halved from an edge of SHARP_AMBER and quartered from
   SHARP_RED (the model-gap caution of point 6 doesn't apply).
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
    match_limit_pct: float = 0.05  # all bets on one match together
    gap_warn: float = 0.15       # model minus market from which stakes shrink


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
    gap: float | None = None     # model minus market probability
    caution: str = ""            # "", "amber" (stake halved) or "red" (quartered)
    capped: bool = False         # stake cut by the match limit
    p_sharp: float | None = None  # de-margined sharp-book probability, when known
    basis: str = "blend"         # "sharp" (p = p_sharp) | "blend" | "model" (no market)

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


RED_STEP = 0.10  # red caution starts this far above gap_warn


def caution_for(gap: float | None, s: Settings) -> tuple[str, float]:
    """Caution level and stake multiplier for a model-minus-market gap."""
    if gap is None or gap < s.gap_warn:
        return "", 1.0
    return ("red", 0.25) if gap >= s.gap_warn + RED_STEP else ("amber", 0.5)


SHARP_AMBER, SHARP_RED = 0.10, 0.20


def sharp_caution(ev: float) -> tuple[str, float]:
    """Caution level and stake multiplier for an edge measured against a sharp price."""
    if ev >= SHARP_RED:
        return "red", 0.25
    return ("amber", 0.5) if ev >= SHARP_AMBER else ("", 1.0)


def evaluate(model_probs: dict[str, float], odds: dict[str, float], s: Settings,
             already_staked: float = 0.0, sharp: dict[str, float] | None = None) -> list[Selection]:
    """`already_staked`: kroner already bet on this match (open bets).
    `sharp`: de-margined sharp-book probabilities by market (market_probabilities of its prices)."""
    sharp = sharp or {}
    market = market_probabilities(odds)
    room = max(s.match_limit_pct * s.bankroll - already_staked, 0.0)
    rows = []
    for name, o in odds.items():
        if name not in model_probs or o <= 1:
            continue
        pm = float(model_probs[name])
        pk = None if market.get(name) is None else float(market[name])
        # Without a complete market we cannot de-margin; fall back to model only
        # but this is flagged in the UI because the edge is then less reliable.
        ps = sharp.get(name)
        if ps is not None:
            p, basis = float(ps), "sharp"
        else:
            p, basis = (pm, "model") if pk is None else (s.alpha * pm + (1 - s.alpha) * pk, "blend")
        ev = float(p * o - 1)
        kelly = max(ev / (o - 1), 0.0)
        gap = None if pk is None else pm - pk
        caution, mult = sharp_caution(ev) if basis == "sharp" else caution_for(gap, s)
        stake_pct = min(s.kelly_fraction * kelly * mult, s.max_stake_pct)
        rec = ev >= s.min_edge
        # Whole kroner: that's how stakes are placed. A recommended bet never
        # rounds down to nothing, unless the match limit is used up.
        stake = float(max(1, round(stake_pct * s.bankroll))) if rec and stake_pct > 0 else 0.0
        capped = stake > room
        if capped:
            stake = float(int(room))
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
                gap=gap,
                caution=caution,
                capped=bool(capped),
                p_sharp=None if ps is None else float(ps),
                basis=basis,
            )
        )
    return sorted(rows, key=lambda r: r.ev, reverse=True)
