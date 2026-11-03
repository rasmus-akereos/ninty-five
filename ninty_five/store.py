"""SQLite bet log. Tracking results is the only way to learn whether the model
has an edge, so every bet placed should be logged here."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

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
    pnl REAL
);
"""


class BetStore:
    def __init__(self, path: Path | str = DB_PATH):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as c:
            c.executescript(SCHEMA)

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn

    def add(self, bet: dict) -> int:
        fields = ["dataset", "home", "away", "market", "odds", "stake", "p_model", "p_blend", "ev"]
        values = [bet.get(f) for f in fields]
        with self._conn() as c:
            cur = c.execute(
                f"INSERT INTO bets (created, {', '.join(fields)}) VALUES (?, {', '.join('?' * len(fields))})",
                [datetime.now(timezone.utc).isoformat(timespec="seconds"), *values],
            )
            return int(cur.lastrowid)

    def list(self) -> list[dict]:
        with self._conn() as c:
            return [dict(r) for r in c.execute("SELECT * FROM bets ORDER BY id DESC")]

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

    def summary(self) -> dict:
        bets = self.list()
        settled = [b for b in bets if b["status"] in ("won", "lost")]
        staked = sum(b["stake"] for b in settled)
        pnl = sum(b["pnl"] for b in settled)
        expected = sum(b["stake"] * b["ev"] for b in settled if b["ev"] is not None)
        clv = [b["odds"] / b["closing_odds"] - 1 for b in bets if b["closing_odds"]]
        return {
            "n_bets": len(bets),
            "n_open": sum(b["status"] == "open" for b in bets),
            "n_settled": len(settled),
            "n_won": sum(b["status"] == "won" for b in settled),
            "staked": round(staked, 2),
            "pnl": round(pnl, 2),
            "roi": round(pnl / staked, 4) if staked else None,
            "expected_pnl": round(expected, 2),
            "avg_clv": round(sum(clv) / len(clv), 4) if clv else None,
            "n_clv": len(clv),
        }
