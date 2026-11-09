"""Statistics for the Statistics tab: how the user's betting went (from the
bet log) and where the model misjudges teams and leagues (from the board
history). Pure functions over rows; the endpoints in app.py feed them.

Every group carries its n, and small groups are marked `thin` so the page
can fade them instead of letting five bets look like a pattern."""

from __future__ import annotations

import math
from datetime import datetime
from zoneinfo import ZoneInfo

from . import track

TZ = ZoneInfo("Europe/Copenhagen")
SETTLED = ("won", "lost", "took profit")
THIN_BETS = 10
THIN_TEAM = 15  # matches before a team can be flagged


def _day(iso: str) -> str:
    return datetime.fromisoformat(iso).astimezone(TZ).date().isoformat()


def market_group(market: str) -> str:
    if " + " in market:
        return "Bet Builder"
    if market in ("1", "X", "2"):
        return "1X2"
    if market.startswith(("Over ", "Under ")):
        return "Over/Under"
    if market.startswith("BTTS"):
        return "BTTS"
    if market in ("1X", "12", "X2"):
        return "Double chance"
    if market.startswith("ML "):
        return "Moneyline"
    if market.startswith("Spread "):
        return "Spread"
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


def betting(bets: list[dict], competitions: dict[str, str],
            sharp_at_kickoff: dict[int, float] | None = None) -> dict:
    """bets: rows from BetStore.list(); competitions: match_key -> competition;
    sharp_at_kickoff: bet id -> Pinnacle's fair probability for the bet's
    market from the last fetch before kick-off. A bet's figure is odds * p - 1:
    how much better its price was than the sharp market's fair price."""
    sharp_at_kickoff = sharp_at_kickoff or {}
    vs_sharp = [b["odds"] * sharp_at_kickoff[b["id"]] - 1 for b in bets
                if b["status"] != "void" and b["id"] in sharp_at_kickoff]
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
            "avg_vs_pinnacle": round(sum(vs_sharp) / len(vs_sharp), 4) if vs_sharp else None,
            "n_vs_pinnacle": len(vs_sharp),
        },
        "days": day_rows,
        "curve": curve,
        "by_league": _groups(settled, league),
        "by_market": _groups(settled, lambda b: market_group(b["market"])),
        "by_odds": _groups(settled, lambda b: odds_band(b["odds"])),
    }


# ------------------------------------------------------------ model insight
def _points(p_win: float, p_draw: float) -> tuple[float, float]:
    """Expected points and their variance for one match (win 3, draw 1)."""
    e = 3 * p_win + p_draw
    return e, 9 * p_win + p_draw - e * e


def model_insight(matches: list[dict]) -> dict:
    """matches: HistoryStore.finished() rows. Per team: expected points (from
    the model's pre-kick-off 1X2) against actual, and xG against goals; a team
    is flagged only when the gap is beyond two standard deviations and it has
    at least THIN_TEAM matches. Per league: the same for home sides, plus the
    track-record comparison with Danske Spil."""
    teams: dict[str, dict] = {}
    leagues: dict[str, list[dict]] = {}

    def team(name: str) -> dict:
        return teams.setdefault(name, {"team": name, "n": 0, "exp_pts": 0.0, "pts": 0, "var": 0.0,
                                       "xg_for": 0.0, "goals_for": 0, "xg_against": 0.0, "goals_against": 0})

    for m in matches:
        p = m["probabilities"]
        if not all(k in p for k in ("1", "X", "2")):
            continue
        leagues.setdefault(m["competition"], []).append(m)
        hg, ag = m["hg"], m["ag"]
        for side, pw, gf, ga, i in (("home", p["1"], hg, ag, 0), ("away", p["2"], ag, hg, 1)):
            t = team(m[side])
            e, v = _points(pw, p["X"])
            t["n"] += 1
            t["exp_pts"] += e
            t["var"] += v
            t["pts"] += 3 if gf > ga else 1 if gf == ga else 0
            if m.get("xg"):
                t["xg_for"] += m["xg"][i]
                t["xg_against"] += m["xg"][1 - i]
                t["goals_for"] += gf
                t["goals_against"] += ga

    rows = []
    for t in teams.values():
        sd = math.sqrt(max(t.pop("var"), 0.0))
        gap = t["pts"] - t["exp_pts"]
        if t["n"] < THIN_TEAM:
            verdict = "too few"
        elif gap < -2 * sd:
            verdict = "overrates"
        elif gap > 2 * sd:
            verdict = "underrates"
        else:
            verdict = "in line"
        rows.append({**t, "exp_pts": round(t["exp_pts"], 2), "sd": round(sd, 3),
                     "xg_for": round(t["xg_for"], 2), "xg_against": round(t["xg_against"], 2),
                     "verdict": verdict, "_z": abs(gap) / sd if sd else 0.0})
    rows.sort(key=lambda r: (-r["_z"], r["team"]))
    for r in rows:
        del r["_z"]

    league_rows = []
    for comp, ms in leagues.items():
        exp = sum(_points(m["probabilities"]["1"], m["probabilities"]["X"])[0] for m in ms)
        pts = sum(3 if m["hg"] > m["ag"] else 1 if m["hg"] == m["ag"] else 0 for m in ms)
        league_rows.append({"competition": comp, "n": len(ms), "exp_pts_home": round(exp, 2), "pts_home": pts,
                            "track": track._group_summary("1X2", ms)})
    league_rows.sort(key=lambda r: (-r["n"], r["competition"]))
    return {"teams": rows, "leagues": league_rows}


# ------------------------------------------------- where Danske Spil is soft
def _soft_group(label: str, rows: list[dict]) -> dict:
    gaps = [r["gap"] for r in rows]
    return {"label": label, "n": len(rows), "avg_gap": round(sum(gaps) / len(gaps), 4),
            "above": round(sum(g > 0 for g in gaps) / len(gaps), 4), "best": round(max(gaps), 4),
            "estimated": sum(r["basis"] == "implied" for r in rows), "thin": len(rows) < THIN_BETS}


SOFT_OUTLIER = 0.35  # a typed price this far from fair is almost always a typo


def soft_prices(rows: list[dict]) -> dict:
    """How typed Danske Spil prices compare with Pinnacle's fair prices.
    rows: {competition, market, gap (= odds * fair probability - 1), basis
    ("sharp": Pinnacle's own price, "implied": from Pinnacle-implied goals)}.
    Prices more than SOFT_OUTLIER from fair are left out (counted as
    "excluded"). Groups are sorted softest (highest average gap) first."""
    kept = [r for r in rows if abs(r["gap"]) <= SOFT_OUTLIER]
    excluded, rows = len(rows) - len(kept), kept
    def grouped(label):
        by: dict[str, list[dict]] = {}
        for r in rows:
            by.setdefault(label(r), []).append(r)
        return sorted((_soft_group(k, v) for k, v in by.items()), key=lambda g: -g["avg_gap"])
    gaps = [r["gap"] for r in rows]
    return {
        "n": len(rows),
        "avg_gap": round(sum(gaps) / len(gaps), 4) if rows else None,
        "above": round(sum(g > 0 for g in gaps) / len(gaps), 4) if rows else None,
        "by_league": grouped(lambda r: r["competition"]),
        "by_market": grouped(lambda r: market_group(r["market"])),
        "top": sorted(rows, key=lambda r: -r["gap"])[:10],
        "excluded": excluded,
    }
