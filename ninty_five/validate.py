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

from . import data
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
    leagues are judged; one never validated counts as unproven."""
    if row.get("competition") not in NEW_DIVISIONS.values():
        return None
    v = validation.get(row["competition"])
    if v is None:
        return {"model": None, "baseline": None}
    return {"model": v["model"], "baseline": v["baseline"]} if v["model"] >= v["baseline"] else None
