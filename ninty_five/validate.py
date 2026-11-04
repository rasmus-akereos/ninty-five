"""Walk-forward check of the country models on the new leagues.

At each cut-off the country model is refitted on everything before it and
scored on the next window of that division's matches. The baseline predicts
the division's home/draw/away frequencies over the previous three years.
Lower log loss is better; a division where the model doesn't beat the
baseline is shown on the board as "model unproven".
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from . import data, uefa
from .league_fixtures import NEW_DIVISIONS
from .model import CLUB_PARAMS, DixonColes, log_loss_1x2


def _groups_until(df: pd.DataFrame) -> dict[str, str]:
    groups: dict[str, str] = {}
    for r in df.itertuples():
        groups[r.home] = r.competition
        groups[r.away] = r.competition
    return groups


def validate_country(key: str, start: str = "2024-08-01", step_months: int = 1) -> dict[str, dict]:
    """{board label: {"model", "baseline", "matches"}} for the country's new divisions."""
    df, groups = data.load_country(key)
    labels = [lbl for code, lbl in data.COUNTRIES[key.upper()].items() if code in NEW_DIVISIONS]
    scores: dict[str, list[tuple[int, float, float]]] = {lbl: [] for lbl in labels}
    for cut in pd.date_range(start, df["date"].max(), freq=f"{step_months}MS"):
        train = df[df["date"] <= cut]
        window = df[(df["date"] > cut) & (df["date"] <= cut + pd.DateOffset(months=step_months))]
        if window.empty or train.empty:
            continue
        model = DixonColes(**CLUB_PARAMS).fit(train, as_of=cut, groups=_groups_until(train) if groups else None)
        recent = train[train["date"] > cut - pd.DateOffset(years=3)]
        for lbl in labels:
            test, past = window[window["competition"] == lbl], recent[recent["competition"] == lbl]
            if test.empty or past.empty:
                continue
            known = test[test["home"].isin(model.teams) & test["away"].isin(model.teams)]
            if known.empty:
                continue
            freq = np.array([(past.hg > past.ag).mean(), (past.hg == past.ag).mean(), (past.hg < past.ag).mean()])
            outcome = np.where(known.hg > known.ag, 0, np.where(known.hg == known.ag, 1, 2))
            base = float(-np.log(np.maximum(freq[outcome], 1e-12)).mean())
            scores[lbl].append((len(known), log_loss_1x2(model, known), base))
    out = {}
    for lbl, s in scores.items():
        if not s:
            continue
        w = np.array([n for n, _, _ in s])
        out[lbl] = {
            "model": round(float(np.average([m for _, m, _ in s], weights=w)), 4),
            "baseline": round(float(np.average([b for _, _, b in s], weights=w)), 4),
            "matches": int(w.sum()),
        }
    return out


def unproven(row: dict, validation: dict) -> dict | None:
    """The "model unproven" note for a board row, or None. Only the new
    leagues and the Europa and Conference League are judged; one never
    validated counts as unproven."""
    if row.get("competition") not in (*NEW_DIVISIONS.values(), *uefa.BOARD_SLUGS.values()):
        return None
    v = validation.get(row["competition"])
    if v is None:
        return {"model": None, "baseline": None}
    return {"model": v["model"], "baseline": v["baseline"]} if v["model"] >= v["baseline"] else None


# ------------------------------------------------------------ the UEFA model
EUROPEAN = ["Champions League", *uefa.BOARD_SLUGS.values()]  # scored (qualifiers are not)
POOL_BELOW = 150


DOMESTIC = {label for _, label in uefa.UEFA_LEAGUES.values()}


def _uefa_groups_until(df: pd.DataFrame) -> dict[str, str]:
    """Each club's latest domestic league in `df`; clubs without one are European-only."""
    dom = df[df["competition"].isin(DOMESTIC)]
    groups = _groups_until(dom)
    for t in set(df["home"]) | set(df["away"]):
        groups.setdefault(t, uefa.EUROPEAN_ONLY)
    return groups


def _summary(s: list[tuple[int, float, float]]) -> dict | None:
    if not s:
        return None
    w = np.array([n for n, _, _ in s])
    return {"model": round(float(np.average([m for _, m, _ in s], weights=w)), 4),
            "baseline": round(float(np.average([b for _, _, b in s], weights=w)), 4),
            "matches": int(w.sum())}


def validate_uefa(start: str = "2024-08-01", step_months: int = 1) -> dict[str, dict]:
    """Walk-forward check of the UEFA model on European matches (qualifiers
    excluded). Board labels are scored on matches where both clubs have
    domestic data; "(thin data)" keys on the rest; "Champions League" for
    reference. If EL or ECL has fewer than POOL_BELOW scored matches, both
    get the pooled numbers. Baseline: home/draw/away frequencies of the
    previous three years' European matches."""
    df, _, _ = uefa.load_uefa(start=pd.Timestamp(start))
    keys = [*EUROPEAN, *(f"{c} (thin data)" for c in uefa.BOARD_SLUGS.values())]
    scores: dict[str, list[tuple[int, float, float]]] = {k: [] for k in keys}
    europe = df[df["competition"].isin(EUROPEAN)]
    for cut in pd.date_range(start, df["date"].max(), freq=f"{step_months}MS"):
        window = europe[(europe["date"] > cut) & (europe["date"] <= cut + pd.DateOffset(months=step_months))]
        if window.empty:
            continue
        train = df[df["date"] <= cut]
        groups = _uefa_groups_until(train)
        model = DixonColes(**CLUB_PARAMS).fit(train, as_of=cut, groups=groups)
        past = europe[(europe["date"] <= cut) & (europe["date"] > cut - pd.DateOffset(years=3))]
        if past.empty:
            continue
        freq = np.array([(past.hg > past.ag).mean(), (past.hg == past.ag).mean(), (past.hg < past.ag).mean()])
        known = window[window["home"].isin(model.teams) & window["away"].isin(model.teams)]

        def thin(t: str) -> bool:
            return groups.get(t) == uefa.EUROPEAN_ONLY or model.n_matches.get(t, 0) < uefa.THIN_MATCHES

        is_thin = known["home"].map(thin) | known["away"].map(thin)
        for comp in EUROPEAN:
            for key, part in ((comp, known[(known.competition == comp) & ~is_thin]),
                              (f"{comp} (thin data)", known[(known.competition == comp) & is_thin])):
                if key not in scores or part.empty:
                    continue
                outcome = np.where(part.hg > part.ag, 0, np.where(part.hg == part.ag, 1, 2))
                base = float(-np.log(np.maximum(freq[outcome], 1e-12)).mean())
                scores[key].append((len(part), log_loss_1x2(model, part), base))
    out = {k: v for k in keys if (v := _summary(scores[k])) is not None}
    el, ecl = uefa.BOARD_SLUGS.values()
    if min(out.get(el, {}).get("matches", 0), out.get(ecl, {}).get("matches", 0)) < POOL_BELOW:
        pooled = _summary(scores[el] + scores[ecl])
        if pooled:
            out[el], out[ecl] = pooled, dict(pooled)
    return out


def compare_cl_with_europe(start: str = "2025-08-01", step_months: int = 1) -> dict | None:
    """Report only: 1X2 log loss on Champions League matches, walk-forward,
    of the UEFA model against the EUROPE model (each on its own data and
    names), or None if the EUROPE data can't be loaded."""
    from . import fixtures

    try:
        edf, egroups = fixtures.load_europe()
    except Exception:
        return None
    udf, _, _ = uefa.load_uefa(start=pd.Timestamp(start))
    got: dict[str, list[tuple[int, float]]] = {"model": [], "europe": []}
    for cut in pd.date_range(start, udf["date"].max(), freq=f"{step_months}MS"):
        end = cut + pd.DateOffset(months=step_months)
        for key, df, groups in (("model", udf, None), ("europe", edf, egroups)):
            test = df[(df["competition"] == "Champions League") & (df["date"] > cut) & (df["date"] <= end)]
            train = df[df["date"] <= cut]
            if test.empty or train.empty:
                continue
            m = DixonColes(**CLUB_PARAMS).fit(train, as_of=cut, groups=groups or _uefa_groups_until(train))
            known = test[test["home"].isin(m.teams) & test["away"].isin(m.teams)]
            if not known.empty:
                got[key].append((len(known), log_loss_1x2(m, known)))
    if not got["model"] or not got["europe"]:
        return None
    avg = {k: round(float(np.average([l for _, l in v], weights=[n for n, _ in v])), 4) for k, v in got.items()}
    return {**avg, "matches": sum(n for n, _ in got["model"]), "matches_europe": sum(n for n, _ in got["europe"])}
