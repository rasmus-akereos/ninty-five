"""Track record: how well the model's and Danske Spil's probabilities matched
what actually happened, on finished board matches.

Log loss (lower is better) measures the probability each gave the actual
outcome. "Closer" is head to head: per match, which of the two put more
probability on what happened. Calibration bins every outcome probability and
compares the average forecast with how often those outcomes happened.
"""

from __future__ import annotations

import math

from .value import market_probabilities

GROUPS = {
    "1X2": ("1", "X", "2"),
    "OU25": ("Over 2.5", "Under 2.5"),
}
_EPS = 1e-6


def _outcome(group: str, hg: int, ag: int) -> str:
    if group == "1X2":
        return "1" if hg > ag else "X" if hg == ag else "2"
    return "Over 2.5" if hg + ag > 2.5 else "Under 2.5"


def _ll(p: float) -> float:
    return -math.log(min(max(p, _EPS), 1.0))


def _mean(xs: list[float]) -> float | None:
    return sum(xs) / len(xs) if xs else None


def _group_summary(group: str, matches: list[dict]) -> dict:
    outcomes = GROUPS[group]
    model_ll, cmp_model, cmp_market = [], [], []
    model_closer = market_closer = 0
    bins = [{"lo": i / 10, "hi": (i + 1) / 10, "preds": [], "hits": []} for i in range(10)]
    for m in matches:
        probs = m["probabilities"]
        if not all(o in probs for o in outcomes):
            continue
        actual = _outcome(group, m["hg"], m["ag"])
        model_ll.append(_ll(probs[actual]))
        for o in outcomes:
            b = bins[min(int(probs[o] * 10), 9)]
            b["preds"].append(probs[o])
            b["hits"].append(1.0 if o == actual else 0.0)
        odds = m.get("odds") or {}
        if all(o in odds for o in outcomes):
            market = market_probabilities({o: odds[o] for o in outcomes})
            cmp_model.append(_ll(probs[actual]))
            cmp_market.append(_ll(market[actual]))
            if probs[actual] > market[actual]:
                model_closer += 1
            elif market[actual] > probs[actual]:
                market_closer += 1
    n_cmp = len(cmp_model)
    return {
        "n": len(model_ll),
        "model_logloss": _mean(model_ll),
        "n_compared": n_cmp,
        "model_logloss_compared": _mean(cmp_model),
        "market_logloss_compared": _mean(cmp_market),
        "model_closer": model_closer / n_cmp if n_cmp else None,
        "market_closer": market_closer / n_cmp if n_cmp else None,
        "calibration": [
            {"lo": b["lo"], "hi": b["hi"], "n": len(b["preds"]),
             "avg_pred": _mean(b["preds"]), "freq": _mean(b["hits"])}
            for b in bins
        ],
    }


def summary(matches: list[dict]) -> dict:
    """matches: dicts with hg, ag, probabilities (model, before kick-off) and
    odds (last typed Danske Spil prices before kick-off, possibly empty)."""
    return {g: _group_summary(g, matches) for g in GROUPS}
