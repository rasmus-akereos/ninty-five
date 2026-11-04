"""Automatic tuning of the model's settings from its own track record.

Each daily run asks, per setting, "would a different value have predicted the
finished matches better?" A change goes live only when it is clearly better
on matches it was not chosen on:

* evidence sorted by kick-off; the best candidate is chosen on the older 70%,
* then confirmed on the newest 30%: the mean per-match log-loss gain over the
  current value must exceed its standard error,
* a change moves the setting one small step at most and stays within bounds,
* every test is logged ('changed' or 'kept'); a revert blocks that setting
  for 30 days (HistoryStore.blocked).

Settings: the model weight alpha (overall, then per league) and the club
half-life. Team corrections are deliberately not here yet.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np

from .value import market_probabilities

ALPHA_DEFAULT = 0.5
ALPHA_BOUNDS = (0.2, 0.8)
ALPHA_STEP = 0.10
ALPHA_GRID = np.round(np.arange(0.20, 0.8001, 0.05), 2)
ALPHA_MIN_N = 100
CONFIRM_SHARE = 0.3
_EPS = 1e-6


@dataclass
class Decision:
    setting: str
    scope: str
    old: float
    new: float
    action: str            # 'changed' | 'kept' | 'waiting'
    n: int
    loss_old: float | None = None
    loss_new: float | None = None
    se: float | None = None
    note: str = ""

    def to_log(self, at: str) -> dict:
        return {"at": at, **asdict(self)}


def _outcome(hg: int, ag: int) -> str:
    return "1" if hg > ag else "X" if hg == ag else "2"


def alpha_evidence(matches: list[dict]) -> list[dict]:
    """Finished board matches with the model's 1X2 and a full typed 1X2 price:
    the probability each gave the actual outcome."""
    out = []
    for m in matches:
        probs, odds = m["probabilities"], m.get("odds") or {}
        if not all(k in probs for k in ("1", "X", "2")) or not all(k in odds for k in ("1", "X", "2")):
            continue
        market = market_probabilities({k: odds[k] for k in ("1", "X", "2")})
        k = _outcome(m["hg"], m["ag"])
        out.append({"kickoff": m["kickoff"], "competition": m["competition"], "pm": probs[k], "pk": market[k]})
    return out


def _losses(rows: list[dict], alpha: float) -> np.ndarray:
    p = np.array([alpha * r["pm"] + (1 - alpha) * r["pk"] for r in rows])
    return -np.log(np.clip(p, _EPS, 1.0))


def confirm(old: np.ndarray, new: np.ndarray) -> tuple[bool, float, float]:
    """Paired test on held-out matches: (adopt?, mean gain, its standard error)."""
    gain = old - new
    mean = float(gain.mean())
    se = float(gain.std(ddof=1) / np.sqrt(len(gain))) if len(gain) > 1 else float("inf")
    return mean > 0 and mean > se, mean, se


def decide_alpha(evidence: list[dict], current: float, scope: str) -> Decision:
    rows = sorted(evidence, key=lambda r: r["kickoff"])
    n = len(rows)
    if n < ALPHA_MIN_N:
        return Decision("alpha", scope, current, current, "waiting", n, note=f"{n} of {ALPHA_MIN_N} matches")
    cut = int(round(n * (1 - CONFIRM_SHARE)))
    older, newer = rows[:cut], rows[cut:]
    best = min(ALPHA_GRID, key=lambda a: _losses(older, a).mean())
    lo, hi = max(ALPHA_BOUNDS[0], current - ALPHA_STEP), min(ALPHA_BOUNDS[1], current + ALPHA_STEP)
    cand = round(float(min(max(best, lo), hi)), 2)
    old_l = _losses(newer, current)
    if cand == round(current, 2):
        return Decision("alpha", scope, current, current, "kept", n, float(old_l.mean()), float(old_l.mean()), 0.0,
                        note=f"best on older matches: {best:.2f}")
    new_l = _losses(newer, cand)
    ok, _, se = confirm(old_l, new_l)
    return Decision("alpha", scope, current, cand if ok else current, "changed" if ok else "kept", n,
                    float(old_l.mean()), float(new_l.mean()), se, note=f"candidate {cand:.2f}")
