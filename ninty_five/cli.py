"""Command line entry points: serve the web app, show ratings, run a backtest."""

from __future__ import annotations

import argparse

import numpy as np
import pandas as pd

from .data import load_dataset
from .model import CLUB_PARAMS, INTL_PARAMS, DixonColes, log_loss_1x2


def backtest(dataset: str, start: str, step_months: int) -> None:
    """Walk-forward test: refit at each cut-off, score the next window.

    Reports multiclass 1X2 log loss vs a naive baseline that predicts the
    historical home/draw/away frequencies. Lower is better; uniform guessing
    scores ln 3 = 1.0986.
    """
    df = load_dataset(dataset)
    params = INTL_PARAMS if dataset.upper() == "INTL" else CLUB_PARAMS
    cuts = pd.date_range(start, df["date"].max(), freq=f"{step_months}MS")
    rows = []
    for cut in cuts:
        train = df[df["date"] <= cut]
        test = df[(df["date"] > cut) & (df["date"] <= cut + pd.DateOffset(months=step_months))]
        if dataset.upper() == "INTL":
            test = test[test["competition"] != "Friendly"]
        if test.empty:
            continue
        model = DixonColes(**params).fit(train, as_of=cut)
        recent = train[train["date"] > cut - pd.DateOffset(years=3)]
        freq = np.array(
            [(recent.hg > recent.ag).mean(), (recent.hg == recent.ag).mean(), (recent.hg < recent.ag).mean()]
        )
        outcome = np.where(test.hg > test.ag, 0, np.where(test.hg == test.ag, 1, 2))
        base = float(-np.log(freq[outcome]).mean())
        rows.append((cut.date(), len(test), log_loss_1x2(model, test), base))
    out = pd.DataFrame(rows, columns=["cutoff", "matches", "model_logloss", "baseline_logloss"])
    print(out.to_string(index=False, float_format="%.4f"))
    w = out["matches"]
    print(
        f"\nweighted mean  model {np.average(out.model_logloss, weights=w):.4f}"
        f"  baseline {np.average(out.baseline_logloss, weights=w):.4f}"
    )


def main() -> None:
    p = argparse.ArgumentParser(prog="ninty-five")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("serve", help="run the web app")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8000)
    r = sub.add_parser("ratings", help="print team ratings")
    r.add_argument("--dataset", default="INTL")
    r.add_argument("--top", type=int, default=30)
    b = sub.add_parser("backtest", help="walk-forward log-loss test")
    b.add_argument("--dataset", default="INTL")
    b.add_argument("--start", default="2023-01-01")
    b.add_argument("--step-months", type=int, default=6)
    args = p.parse_args()

    if args.cmd == "serve":
        import uvicorn

        uvicorn.run("ninty_five.app:app", host=args.host, port=args.port)
    elif args.cmd == "ratings":
        params = INTL_PARAMS if args.dataset.upper() == "INTL" else CLUB_PARAMS
        m = DixonColes(**params).fit(load_dataset(args.dataset))
        print(m.ratings().head(args.top).to_string(float_format="%.3f"))
    elif args.cmd == "backtest":
        backtest(args.dataset, args.start, args.step_months)


if __name__ == "__main__":
    main()
