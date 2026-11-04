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


def alpha_for(tuned: dict, competition: str | None) -> tuple[float, str]:
    """The model weight to use for a match: its league's tuned value, else the
    overall tuned value, else the default. Returns (value, scope used)."""
    if competition and ("alpha", competition) in tuned:
        return tuned[("alpha", competition)], competition
    if ("alpha", "all") in tuned:
        return tuned[("alpha", "all")], "all"
    return ALPHA_DEFAULT, "default"


def _iso(now) -> str:
    return now.isoformat(timespec="seconds")


def _apply(history, d: Decision, now) -> None:
    if d.action == "waiting":
        return
    if d.action == "changed":
        history.set_tuned(d.setting, d.scope, d.new, _iso(now))
    history.log_tuning(d.to_log(_iso(now)))


def _awaiting_evidence(history, setting: str, scope: str, n: int) -> bool:
    """After a change, wait for CONFIRM_SHARE more matches before testing again,
    so the next step is confirmed on new matches, not the ones that justified
    the last step."""
    last = history.last_tuning(setting, scope, action="changed")
    return bool(last) and not last["reverted_at"] and n < int(np.ceil((last["n"] or 0) * (1 + CONFIRM_SHARE)))


def run_alpha(history, now) -> list[Decision]:
    """Test the overall weight, then each league with enough evidence of its own."""
    evidence = alpha_evidence(history.finished())
    tuned = history.tuned()
    by_league: dict[str, list[dict]] = {}
    for r in evidence:
        by_league.setdefault(r["competition"], []).append(r)
    scopes = [("all", evidence)] + [(c, rows) for c, rows in sorted(by_league.items()) if len(rows) >= ALPHA_MIN_N]
    out = []
    for scope, rows in scopes:
        if history.blocked("alpha", scope, now) or _awaiting_evidence(history, "alpha", scope, len(rows)):
            continue
        current = tuned.get(("alpha", scope), tuned.get(("alpha", "all"), ALPHA_DEFAULT))
        d = decide_alpha(rows, current, scope)
        _apply(history, d, now)
        out.append(d)
    return out


def overview(history) -> dict:
    """What the Tuning sub-tab shows: values in use, progress, the log."""
    from .model import CLUB_PARAMS

    tuned = history.tuned()
    settings = [{"setting": "alpha", "scope": "all", "value": tuned.get(("alpha", "all"), ALPHA_DEFAULT),
                 "default": ("alpha", "all") not in tuned}]
    settings += [{"setting": s, "scope": sc, "value": v, "default": False}
                 for (s, sc), v in sorted(tuned.items()) if s == "alpha" and sc != "all"]
    settings.append({"setting": "half_life", "scope": "club",
                     "value": tuned.get(("half_life", "club"), CLUB_PARAMS["half_life_days"]),
                     "default": ("half_life", "club") not in tuned})

    evidence = alpha_evidence(history.finished())
    counts: dict[str, int] = {}
    for r in evidence:
        counts[r["competition"]] = counts.get(r["competition"], 0) + 1

    def prog(setting, scope, n, needed):
        last = history.last_tuning(setting, scope)
        return {"setting": setting, "scope": scope, "n": n, "needed": needed,
                "last_test": last["at"] if last else None, "last_action": last["action"] if last else None}

    progress = [prog("alpha", "all", len(evidence), ALPHA_MIN_N)]
    progress += [prog("alpha", c, n, ALPHA_MIN_N) for c, n in sorted(counts.items(), key=lambda kv: -kv[1])]
    progress.append(prog("half_life", "club", None, None))
    return {"settings": settings, "progress": progress, "log": history.tuning_log(50),
            "deferred": ["Team corrections"]}


# --------------------------------------------------------------- half-life
HL_BOUNDS = (180.0, 1095.0)
HL_FACTOR = 1.5
HL_MONTHS = 6
HL_EVERY_DAYS = 30
TUNING_LEASE_TTL_HOURS = 3


def half_life_candidates(current: float) -> list[float]:
    """One step down, the current value, one step up — within bounds, whole days."""
    raw = (current / HL_FACTOR, current, current * HL_FACTOR)
    return sorted({float(round(min(max(h, HL_BOUNDS[0]), HL_BOUNDS[1]))) for h in raw})


def decide_half_life(losses: dict[float, np.ndarray], kickoffs: np.ndarray, current: float) -> Decision:
    """losses[h]: per-match walk-forward 1X2 losses with half-life h, aligned
    with kickoffs (NaN where a team was unknown). Same rule as alpha."""
    current = float(round(current))
    keep = ~np.any([np.isnan(v) for v in losses.values()], axis=0)
    order = np.argsort(kickoffs[keep], kind="stable")
    ls = {h: v[keep][order] for h, v in losses.items()}
    n = int(keep.sum())
    if n < 2:
        return Decision("half_life", "club", current, current, "waiting", n, note="no walk-forward matches")
    cut = int(round(n * (1 - CONFIRM_SHARE)))
    best = min(ls, key=lambda h: ls[h][:cut].mean())
    old_l = ls[current][cut:]
    if best == current:
        return Decision("half_life", "club", current, current, "kept", n, float(old_l.mean()), float(old_l.mean()),
                        0.0, note=f"best on older matches: {best:.0f} days")
    new_l = ls[best][cut:]
    ok, _, se = confirm(old_l, new_l)
    return Decision("half_life", "club", current, best if ok else current, "changed" if ok else "kept", n,
                    float(old_l.mean()), float(new_l.mean()), se, note=f"candidate {best:.0f} days")


def half_life_losses(current: float, months: int = HL_MONTHS) -> tuple[dict[float, np.ndarray], np.ndarray]:
    """Walk-forward over every club country data set for the last `months`
    monthly cuts: refit with each candidate half-life on results up to the cut
    and score the next month. Slow (minutes); runs at most monthly."""
    import pandas as pd

    from . import data
    from .model import CLUB_PARAMS, DixonColes, match_losses_1x2
    from .validate import _groups_until

    cands = half_life_candidates(current)
    losses: dict[float, list[np.ndarray]] = {h: [] for h in cands}
    kicks: list[np.ndarray] = []
    for key in data.COUNTRIES:
        df, groups = data.load_country(key)
        end = df["date"].max()
        for cut in pd.date_range(end - pd.DateOffset(months=months), end, freq="MS"):
            train = df[df["date"] <= cut]
            window = df[(df["date"] > cut) & (df["date"] <= cut + pd.DateOffset(months=1))]
            if window.empty or train.empty:
                continue
            g = _groups_until(train) if groups else None
            for h in cands:
                m = DixonColes(**{**CLUB_PARAMS, "half_life_days": h}).fit(train, as_of=cut, groups=g)
                losses[h].append(match_losses_1x2(m, window))
            kicks.append(window["date"].astype(str).to_numpy())
    if not kicks:
        return {h: np.array([]) for h in cands}, np.array([])
    return {h: np.concatenate(v) for h, v in losses.items()}, np.concatenate(kicks)


def run_half_life(history, now) -> Decision | None:
    """Monthly: test the club half-life against one step either side."""
    import pandas as pd

    from . import service

    if history.blocked("half_life", "club", now):
        return None
    last = [r for r in (history.last_tuning("half_life", "club", action=a) for a in ("changed", "kept")) if r]
    if last and now - max(pd.Timestamp(r["at"]) for r in last) < pd.Timedelta(days=HL_EVERY_DAYS):
        return None
    from .model import CLUB_PARAMS
    current = history.tuned().get(("half_life", "club"), CLUB_PARAMS["half_life_days"])
    losses, kicks = half_life_losses(current)
    d = decide_half_life(losses, kicks, current)
    _apply(history, d, now)
    if d.action == "changed":
        service.set_club_half_life(d.new)
    return d


def run_all(history, now) -> list[Decision]:
    """The daily tuning run (after the morning refresh). Never raises: a
    failure is logged and the rest of the day carries on with old settings."""
    import logging
    import uuid

    import pandas as pd

    log = logging.getLogger("ninety_five")
    holder = f"tuning-{uuid.uuid4().hex[:8]}"
    if not history.take_lease("tuning", holder, now, ttl=pd.Timedelta(hours=TUNING_LEASE_TTL_HOURS)):
        return []
    out: list[Decision] = []
    try:
        for step in (run_alpha, run_half_life):  # independent: one failing doesn't stop the other
            try:
                got = step(history, now)
            except Exception as exc:
                log.warning("tuning (%s) failed: %s", step.__name__, exc)
                continue
            out += got if isinstance(got, list) else [got] if got is not None else []
    finally:
        history.release_lease("tuning", holder)
    return out


def apply_stored(history) -> None:
    """At start-up: put a tuned club half-life back into use."""
    from . import service

    hl = history.tuned().get(("half_life", "club"))
    if hl is not None:
        service.set_club_half_life(hl)
