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


def print_fixtures(days: int, min_edge: float) -> None:
    """Minimum odds list, e.g. for a morning cron job. Also auto-settles bets."""
    from .service import board, results_frame
    from .store import BetStore

    settled = BetStore().auto_settle(results_frame())
    if settled:
        print(f"Auto-settled bets: {settled}\n")
    cols = ["1", "X", "2", "Over 2.5", "Under 2.5", "BTTS Yes"]
    rows = []
    fixtures_, warnings = board(days=days, min_edge=min_edge)
    for w in warnings:
        print(f"Warning: {w}")
    for f in fixtures_:
        r = {
            "kickoff": pd.Timestamp(f["kickoff"]).tz_convert("Europe/Copenhagen").strftime("%a %d %b %H:%M"),
            "comp": f["detail"] or f["competition"],
            "match": f"{f['home']} - {f['away']}",
        }
        if f["rated"]:
            r.update({c: round(f["min_odds"][c], 2) for c in cols})
        rows.append(r)
    if not rows:
        print("No scheduled matches.")
        return
    print(f"Minimum odds worth betting at (model only, edge >= {min_edge:.0%}), Danish time:\n")
    print(pd.DataFrame(rows).to_string(index=False))


def run_validation(countries: list[str], start: str) -> None:
    """Validate each country's new leagues, print the table and store it."""
    from .history import HistoryStore
    from .validate import validate_country

    history = HistoryStore()
    for key in countries:
        out = validate_country(key, start=start)
        history.set_validation(out, as_of=str(pd.Timestamp.now(tz="UTC").date()))
        for label, r in out.items():
            verdict = "beats baseline" if r["model"] < r["baseline"] else "MODEL UNPROVEN"
            print(f"{label:10s}  model {r['model']:.4f}  baseline {r['baseline']:.4f}  "
                  f"matches {r['matches']:5d}  {verdict}", flush=True)


def run_validation_uefa(start: str) -> None:
    """Validate the UEFA model, print every result and store the board's."""
    from .history import HistoryStore
    from .validate import compare_cl_with_europe, validate_uefa

    out = validate_uefa(start=start)
    HistoryStore().set_validation(out, as_of=str(pd.Timestamp.now(tz="UTC").date()))
    for label, r in out.items():
        verdict = "beats baseline" if r["model"] < r["baseline"] else "MODEL UNPROVEN"
        print(f"{label:32s}  model {r['model']:.4f}  baseline {r['baseline']:.4f}  "
              f"matches {r['matches']:5d}  {verdict}", flush=True)
    cl = compare_cl_with_europe()
    if cl is None:
        print("Champions League vs EUROPE model: unavailable")
    else:
        print(f"Champions League vs EUROPE model (from 2025-08): UEFA {cl['model']:.4f} on {cl['matches']} matches, "
              f"EUROPE {cl['europe']:.4f} on {cl['matches_europe']} matches")


def print_uefa_names() -> None:
    """Every European club of the UEFA model's window with how it resolved."""
    from . import uefa

    _, _, res = uefa.load_uefa()
    teams, _ = uefa.last_inputs
    country = {t: c for c, ts in teams.items() for t in ts}
    for name in sorted(set(res.mapping) | res.excluded):
        if name in res.excluded:
            print(f"EXCLUDED       {name}")
        elif name in res.european_only:
            print(f"European only  {name}")
        else:
            got = res.mapping[name]
            print(f"{country.get(got, '?'):3s}            {name}" + ("" if got == name else f"  ->  {got}"))
    for w in res.warnings:
        print("warning:", w)


def main() -> None:
    p = argparse.ArgumentParser(prog="ninty-five")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("serve", help="run the web app")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8000)
    r = sub.add_parser("ratings", help="print team ratings")
    r.add_argument("--dataset", default="INTL")
    r.add_argument("--top", type=int, default=30)
    f = sub.add_parser("fixtures", help="upcoming club and national-team matches with minimum odds")
    f.add_argument("--days", type=int, default=3)
    f.add_argument("--min-edge", type=float, default=0.08)
    b = sub.add_parser("backtest", help="walk-forward log-loss test")
    b.add_argument("--dataset", default="INTL")
    b.add_argument("--start", default="2023-01-01")
    b.add_argument("--step-months", type=int, default=6)
    v = sub.add_parser("validate", help="walk-forward check of the lower-league models (stored for the board)")
    v.add_argument("--country", choices=["ENG", "GER", "ESP", "ITA", "FRA", "DEN"], action="append")
    v.add_argument("--start", default="2024-08-01")
    v.add_argument("--uefa", action="store_true", help="validate the Europa/Conference League model instead")
    sub.add_parser("uefa-names", help="list how the UEFA model matched every European club")
    args = p.parse_args()

    if args.cmd == "serve":
        import uvicorn

        uvicorn.run("ninty_five.app:app", host=args.host, port=args.port)
    elif args.cmd == "ratings":
        from .service import fit_dataset

        m = fit_dataset(args.dataset)
        print(m.ratings().head(args.top).to_string(float_format="%.3f"))
    elif args.cmd == "fixtures":
        print_fixtures(args.days, args.min_edge)
    elif args.cmd == "backtest":
        backtest(args.dataset, args.start, args.step_months)
    elif args.cmd == "validate" and args.uefa:
        run_validation_uefa(args.start)
    elif args.cmd == "validate":
        run_validation(args.country or ["ENG", "GER", "ESP", "ITA", "FRA", "DEN"], args.start)
    elif args.cmd == "uefa-names":
        print_uefa_names()


if __name__ == "__main__":
    main()
