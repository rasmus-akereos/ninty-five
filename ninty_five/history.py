"""History of the fixture board: every match seen, snapshots of the model's
prices and both teams' form at each fetch, the Danske Spil odds typed in, and
the actual results. Lives in the same SQLite file as the bet log.

Comparing these over time answers whether the model or the bookmaker reads
matches better (see track.py)."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pandas as pd

from .store import DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS matches (
    key TEXT PRIMARY KEY,          -- fd:<football-data id> | espn:<ESPN event id>
    dataset TEXT NOT NULL,
    match_id INTEGER,              -- football-data id (club matches only)
    competition TEXT NOT NULL,
    detail TEXT NOT NULL DEFAULT '',
    stage TEXT NOT NULL DEFAULT '',
    kickoff TEXT NOT NULL,         -- ISO UTC
    home TEXT NOT NULL,
    away TEXT NOT NULL,
    neutral INTEGER NOT NULL,
    status TEXT,                   -- set with the result (e.g. STATUS_CANCELED)
    home_goals INTEGER,            -- 90-minute score
    away_goals INTEGER,
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    match_key TEXT NOT NULL REFERENCES matches(key),
    taken_at TEXT NOT NULL,
    model_as_of TEXT,
    xg TEXT NOT NULL,              -- JSON [home, away]
    data_matches TEXT NOT NULL,    -- JSON [home, away]
    leagues TEXT NOT NULL,         -- JSON [home, away]
    probabilities TEXT NOT NULL,   -- JSON {market: p}
    form TEXT NOT NULL             -- JSON {"home": [...], "away": [...]}
);
CREATE INDEX IF NOT EXISTS snapshots_match ON snapshots(match_key, id);
CREATE TABLE IF NOT EXISTS odds_entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    match_key TEXT,                -- NULL for matches added by hand
    dataset TEXT NOT NULL,
    home TEXT NOT NULL,
    away TEXT NOT NULL,
    entered_at TEXT NOT NULL,
    market TEXT NOT NULL,
    odds REAL NOT NULL,
    p_model REAL,
    p_market REAL,
    p_blend REAL
);
CREATE INDEX IF NOT EXISTS odds_identity ON odds_entries(match_key, dataset, home, away, market, id);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
"""


def _probs_json(probs: dict) -> str:
    return json.dumps({k: round(float(v), 4) for k, v in probs.items()}, sort_keys=True)


def _form_json(form: dict) -> str:
    return json.dumps(form, sort_keys=True)


class HistoryStore:
    def __init__(self, path: Path | str = DB_PATH):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as c:
            c.executescript(SCHEMA)

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn

    # ------------------------------------------------------------- board
    def save_board(self, rows: list[dict], fetched_at: str) -> int:
        """Upsert every match and snapshot the rated ones unless nothing
        changed since their previous snapshot. Returns snapshots written."""
        written = 0
        with self._conn() as c:
            for r in rows:
                c.execute(
                    """INSERT INTO matches (key, dataset, match_id, competition, detail, stage, kickoff,
                                            home, away, neutral, first_seen, last_seen)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                       ON CONFLICT(key) DO UPDATE SET
                           dataset=excluded.dataset, match_id=excluded.match_id,
                           competition=excluded.competition, detail=excluded.detail, stage=excluded.stage,
                           kickoff=excluded.kickoff, home=excluded.home, away=excluded.away,
                           neutral=excluded.neutral, last_seen=excluded.last_seen""",
                    (r["key"], r["dataset"], r.get("match_id"), r["competition"], r.get("detail", ""),
                     r.get("stage", ""), r["kickoff"], r["home"], r["away"], int(bool(r["neutral"])),
                     fetched_at, fetched_at),
                )
                if not r.get("rated"):
                    continue
                probs, form = _probs_json(r["probabilities"]), _form_json(r.get("form") or {"home": [], "away": []})
                prev = c.execute(
                    "SELECT probabilities, form FROM snapshots WHERE match_key = ? ORDER BY id DESC LIMIT 1",
                    (r["key"],),
                ).fetchone()
                if prev and prev["probabilities"] == probs and prev["form"] == form:
                    continue
                c.execute(
                    """INSERT INTO snapshots (match_key, taken_at, model_as_of, xg, data_matches, leagues,
                                              probabilities, form) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (r["key"], fetched_at, r.get("model_as_of"), json.dumps(r["xg"]),
                     json.dumps(r["data_matches"]), json.dumps(r.get("leagues", ["", ""])), probs, form),
                )
                written += 1
            c.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('board_fetched_at', ?)", (fetched_at,))
        return written

    def fetched_at(self) -> str | None:
        with self._conn() as c:
            row = c.execute("SELECT value FROM meta WHERE key = 'board_fetched_at'").fetchone()
        return row["value"] if row else None

    def board(self, days: int, now: pd.Timestamp | None = None) -> list[dict]:
        """Upcoming matches (now <= kick-off < start of today + days), each
        with its latest snapshot, in board-row shape."""
        now = now or pd.Timestamp.now(tz="UTC")
        end = now.normalize() + pd.Timedelta(days=days)
        with self._conn() as c:
            rows = c.execute(
                """SELECT m.*, s.model_as_of, s.xg, s.data_matches, s.leagues, s.probabilities, s.form,
                          s.id AS snapshot_id
                   FROM matches m
                   LEFT JOIN snapshots s ON s.id = (SELECT MAX(id) FROM snapshots WHERE match_key = m.key)
                   WHERE m.status IS NULL OR m.status NOT LIKE 'STATUS_CANCEL%'"""
            ).fetchall()
        out = []
        for r in rows:
            kickoff = pd.Timestamp(r["kickoff"])
            if not (now <= kickoff < end):
                continue
            row = {
                "key": r["key"], "dataset": r["dataset"], "match_id": r["match_id"], "kickoff": r["kickoff"],
                "competition": r["competition"], "detail": r["detail"], "stage": r["stage"],
                "home": r["home"], "away": r["away"], "neutral": bool(r["neutral"]),
                "rated": r["snapshot_id"] is not None,
            }
            if row["rated"]:
                row.update(
                    model_as_of=r["model_as_of"], xg=json.loads(r["xg"]), data_matches=json.loads(r["data_matches"]),
                    leagues=json.loads(r["leagues"]), probabilities=json.loads(r["probabilities"]),
                    form=json.loads(r["form"]),
                )
            out.append(row)
        return sorted(out, key=lambda x: pd.Timestamp(x["kickoff"]))
