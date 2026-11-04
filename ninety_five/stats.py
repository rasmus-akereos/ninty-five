"""Statistics for the Statistics tab: how the user's betting went (from the
bet log) and where the model misjudges teams and leagues (from the board
history). Pure functions over rows; the endpoints in app.py feed them.

Every group carries its n, and small groups are marked `thin` so the page
can fade them instead of letting five bets look like a pattern."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Europe/Copenhagen")
SETTLED = ("won", "lost", "took profit")
THIN_BETS = 10


def _day(iso: str) -> str:
    return datetime.fromisoformat(iso).astimezone(TZ).date().isoformat()


def market_group(market: str) -> str:
    if market in ("1", "X", "2"):
        return "1X2"
    if market.startswith(("Over ", "Under ")):
        return "Over/Under"
    if market.startswith("BTTS"):
        return "BTTS"
    if market in ("1X", "12", "X2"):
        return "Double chance"
    return "Other"


def odds_band(odds: float) -> str:
    if odds < 1.8:
        return "< 1.80"
    if odds < 2.5:
        return "1.80–2.49"
    if odds < 4.0:
        return "2.50–3.99"
    return "4.00+"


def _expected(b: dict) -> float:
    return b["stake"] * b["ev"] if b.get("ev") is not None else 0.0


def _groups(settled: list[dict], label) -> list[dict]:
    by: dict[str, list[dict]] = {}
    for b in settled:
        by.setdefault(label(b), []).append(b)
    out = []
    for name, bs in by.items():
        staked = sum(b["stake"] for b in bs)
        pnl = sum(b["pnl"] or 0.0 for b in bs)
        out.append({"label": name, "n": len(bs), "staked": round(staked, 2), "pnl": round(pnl, 2),
                    "roi": round(pnl / staked, 4) if staked else None,
                    "expected_pnl": round(sum(_expected(b) for b in bs), 2), "thin": len(bs) < THIN_BETS})
    return sorted(out, key=lambda g: (-g["n"], g["label"]))


def betting(bets: list[dict], competitions: dict[str, str]) -> dict:
    """bets: rows from BetStore.list(); competitions: match_key -> competition."""
    settled = [b for b in bets if b["status"] in SETTLED]
    staked = sum(b["stake"] for b in settled)
    pnl = sum(b["pnl"] or 0.0 for b in settled)
    clv = [b["odds"] / b["closing_odds"] - 1 for b in bets if b.get("closing_odds")]

    days: dict[str, dict] = {}
    for b in bets:
        if b["status"] == "void":
            continue
        d = days.setdefault(_day(b["created"]), {"bets": 0, "stake": 0.0})
        d["bets"] += 1
        d["stake"] += b["stake"]
    day_rows = [{"date": k, "bets": v["bets"], "stake": round(v["stake"], 2)} for k, v in sorted(days.items())]

    curve, cum, exp = [], 0.0, 0.0
    for i, b in enumerate(sorted(settled, key=lambda b: b.get("kickoff") or b["created"]), 1):
        cum += b["pnl"] or 0.0
        exp += _expected(b)
        curve.append({"date": _day(b.get("kickoff") or b["created"]), "pnl_cum": round(cum, 2),
                      "expected_cum": round(exp, 2), "n": i})

    league = lambda b: competitions.get(b.get("match_key") or "", b["dataset"])  # noqa: E731
    return {
        "tiles": {
            "n_settled": len(settled),
            "n_open": sum(b["status"] == "open" for b in bets),
            "staked": round(staked, 2),
            "pnl": round(pnl, 2),
            "roi": round(pnl / staked, 4) if staked else None,
            "avg_return": round(pnl / len(settled), 2) if settled else None,
            "expected_pnl": round(sum(_expected(b) for b in settled), 2),
            "bets_per_day": round(sum(d["bets"] for d in day_rows) / len(day_rows), 2) if day_rows else None,
            "stake_per_day": round(sum(d["stake"] for d in day_rows) / len(day_rows), 2) if day_rows else None,
            "avg_clv": round(sum(clv) / len(clv), 4) if clv else None,
            "n_clv": len(clv),
        },
        "days": day_rows,
        "curve": curve,
        "by_league": _groups(settled, league),
        "by_market": _groups(settled, lambda b: market_group(b["market"])),
        "by_odds": _groups(settled, lambda b: odds_band(b["odds"])),
    }
