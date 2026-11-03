"""SQLite bet log. Tracking results is the only way to learn whether the model
has an edge, so every bet placed should be logged here."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from .model import settle

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "bets.sqlite"

SCHEMA = """
CREATE TABLE IF NOT EXISTS bets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created TEXT NOT NULL,
    dataset TEXT NOT NULL,
    home TEXT NOT NULL,
    away TEXT NOT NULL,
    market TEXT NOT NULL,
    odds REAL NOT NULL,
    stake REAL NOT NULL,
    p_model REAL,
    p_blend REAL,
    ev REAL,
    closing_odds REAL,
    home_goals INTEGER,
    away_goals INTEGER,
    status TEXT NOT NULL DEFAULT 'open',  -- open | won | lost | void
    pnl REAL,
    match_id INTEGER,  -- football-data.org id, enables auto-settlement
    kickoff TEXT,
    match_key TEXT     -- board match key (fd:/espn:), enables auto-settlement
);
"""
MIGRATIONS = {"match_id": "INTEGER", "kickoff": "TEXT", "match_key": "TEXT"}


def take_profit_above(bet: dict) -> float | None:
    """The Take Profit (cash-out) offer above which taking the money beats
    keeping the bet: stake * odds * chance it wins. Uses the app's blended
    chance from when the bet was logged, so it holds before kick-off only."""
    p = bet.get("p_blend") if bet.get("p_blend") is not None else bet.get("p_model")
    return None if p is None else bet["stake"] * bet["odds"] * p


def open_outlook(bets: list[dict], n: int = 20000, seed: int = 0) -> dict | None:
    """Overview of open bets: stake, expected profit, and a simulated range of
    outcomes. Bets are treated as independent, which understates the spread
    when several bets are on the same match."""
    bets = [b for b in bets if (b.get("p_blend") if b.get("p_blend") is not None else b.get("p_model")) is not None]
    if not bets:
        return None
    p = np.array([b["p_blend"] if b.get("p_blend") is not None else b["p_model"] for b in bets])
    win = np.array([b["stake"] * (b["odds"] - 1) for b in bets])
    lose = np.array([float(b["stake"]) for b in bets])
    wins = np.random.default_rng(seed).random((n, len(bets))) < p
    pnl = (wins * win - ~wins * lose).sum(axis=1)
    staked = float(lose.sum())
    expected = float((p * win - (1 - p) * lose).sum())
    by_match: dict[str, dict] = {}
    for b in bets:
        m = by_match.setdefault(f"{b['home']} – {b['away']}", {"match": f"{b['home']} – {b['away']}", "stake": 0, "n": 0})
        m["stake"] += b["stake"]
        m["n"] += 1
    return {
        "n": len(bets),
        "staked": staked,
        "expected_pnl": expected,
        "expected_return": expected / staked,
        "p_profit": float(np.mean(pnl > 0)),
        "p10": float(np.percentile(pnl, 10)),
        "p90": float(np.percentile(pnl, 90)),
        "worst": -staked,
        "best": float(win.sum()),
        "by_match": sorted(by_match.values(), key=lambda m: -m["stake"]),
    }


class BetStore:
    def __init__(self, path: Path | str = DB_PATH):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as c:
            c.executescript(SCHEMA)
            have = {r["name"] for r in c.execute("PRAGMA table_info(bets)")}
            for col, typ in MIGRATIONS.items():
                if col not in have:
                    c.execute(f"ALTER TABLE bets ADD COLUMN {col} {typ}")

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn

    def add(self, bet: dict) -> int:
        fields = ["dataset", "home", "away", "market", "odds", "stake", "p_model", "p_blend", "ev", "match_id", "kickoff", "match_key"]
        values = [bet.get(f) for f in fields]
        with self._conn() as c:
            cur = c.execute(
                f"INSERT INTO bets (created, {', '.join(fields)}) VALUES (?, {', '.join('?' * len(fields))})",
                [datetime.now(timezone.utc).isoformat(timespec="seconds"), *values],
            )
            return int(cur.lastrowid)

    def list(self) -> list[dict]:
        with self._conn() as c:
            bets = [dict(r) for r in c.execute("SELECT * FROM bets ORDER BY id DESC")]
        for b in bets:
            b["take_profit_above"] = take_profit_above(b) if b["status"] == "open" else None
        return bets

    def delete(self, bet_id: int) -> None:
        with self._conn() as c:
            c.execute("DELETE FROM bets WHERE id = ?", (bet_id,))

    def settle(
        self,
        bet_id: int,
        home_goals: int | None = None,
        away_goals: int | None = None,
        closing_odds: float | None = None,
        void: bool = False,
    ) -> dict:
        with self._conn() as c:
            row = c.execute("SELECT * FROM bets WHERE id = ?", (bet_id,)).fetchone()
            if row is None:
                raise KeyError(bet_id)
            if void:
                status, pnl = "void", 0.0
            elif home_goals is not None and away_goals is not None:
                won = settle(row["market"], home_goals, away_goals)
                if won is None:
                    raise ValueError(f"Cannot settle market {row['market']!r}")
                status = "won" if won else "lost"
                pnl = row["stake"] * (row["odds"] - 1) if won else -row["stake"]
            else:
                status, pnl = row["status"], row["pnl"]
            c.execute(
                "UPDATE bets SET status=?, pnl=?, home_goals=?, away_goals=?, "
                "closing_odds=COALESCE(?, closing_odds) WHERE id=?",
                (status, pnl, home_goals, away_goals, closing_odds, bet_id),
            )
            return dict(c.execute("SELECT * FROM bets WHERE id = ?", (bet_id,)).fetchone())

    def auto_settle(self, results) -> list[int]:
        """Settle open bets whose match_id appears in `results` (a DataFrame
        with match_id, hg, ag holding 90-minute scores). Returns settled ids."""
        by_id = {int(r.match_id): (int(r.hg), int(r.ag)) for r in results.itertuples()}
        settled = []
        for b in self.list():
            if b["status"] == "open" and b["match_id"] in by_id:
                self.settle(b["id"], *by_id[b["match_id"]])
                settled.append(b["id"])
        return settled

    def summary(self) -> dict:
        bets = self.list()
        settled = [b for b in bets if b["status"] in ("won", "lost")]
        staked = sum(b["stake"] for b in settled)
        pnl = sum(b["pnl"] for b in settled)
        expected = sum(b["stake"] * b["ev"] for b in settled if b["ev"] is not None)
        clv = [b["odds"] / b["closing_odds"] - 1 for b in bets if b["closing_odds"]]
        open_ = [b for b in bets if b["status"] == "open"]
        return {
            "n_bets": len(bets),
            "n_open": sum(b["status"] == "open" for b in bets),
            "n_settled": len(settled),
            "n_won": sum(b["status"] == "won" for b in settled),
            "staked": round(staked, 2),  # settled only: the base for ROI
            "open_staked": round(sum(b["stake"] for b in open_), 2),
            "open_expected_pnl": round(sum(b["stake"] * b["ev"] for b in open_ if b["ev"] is not None), 2),
            "pnl": round(pnl, 2),
            "roi": round(pnl / staked, 4) if staked else None,
            "expected_pnl": round(expected, 2),
            "avg_clv": round(sum(clv) / len(clv), 4) if clv else None,
            "n_clv": len(clv),
            "outlook": open_outlook(open_),
        }

    def auto_settle_keys(self, results: dict[str, tuple[int, int]]) -> list[int]:
        """Settle open bets by board match key; `results` maps key -> 90-minute
        (home goals, away goals). Returns settled ids."""
        settled = []
        for b in self.list():
            if b["status"] == "open" and b["match_key"] in results:
                self.settle(b["id"], *results[b["match_key"]])
                settled.append(b["id"])
        return settled
