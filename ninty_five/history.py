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

    # -------------------------------------------------------- typed odds
    @staticmethod
    def _identity(match_key: str | None, dataset: str, home: str, away: str) -> tuple[str, tuple]:
        """Board matches are identified by key; hand-added ones by teams."""
        if match_key:
            return "match_key = ?", (match_key,)
        return "match_key IS NULL AND dataset = ? AND home = ? AND away = ?", (dataset, home, away)

    def last_odds(self, match_key: str | None, dataset: str, home: str, away: str) -> dict[str, dict]:
        where, args = self._identity(match_key, dataset, home, away)
        with self._conn() as c:
            rows = c.execute(
                f"""SELECT market, odds, entered_at FROM odds_entries
                    WHERE id IN (SELECT MAX(id) FROM odds_entries WHERE {where} GROUP BY market)""",
                args,
            ).fetchall()
        return {r["market"]: {"odds": r["odds"], "entered_at": r["entered_at"]} for r in rows}

    def record_odds(
        self, match_key: str | None, dataset: str, home: str, away: str, selections: list[dict], entered_at: str
    ) -> int:
        """Store each typed price that is valid (> 1) and differs from the last
        stored price for this match and market. Returns rows written."""
        last = self.last_odds(match_key, dataset, home, away)
        written = 0
        with self._conn() as c:
            for s in selections:
                odds = s.get("odds")
                if odds is None or odds <= 1:
                    continue
                prev = last.get(s["market"])
                if prev and abs(prev["odds"] - odds) < 1e-9:
                    continue
                c.execute(
                    """INSERT INTO odds_entries (match_key, dataset, home, away, entered_at, market, odds,
                                                 p_model, p_market, p_blend) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (match_key or None, dataset, home, away, entered_at, s["market"], float(odds),
                     s.get("p_model"), s.get("p_market"), s.get("p_blend")),
                )
                written += 1
        return written

    # ------------------------------------------------------------ results
    def pending_results(self, now: pd.Timestamp, lookback_days: int = 14) -> list[dict]:
        """Matches that kicked off within the look-back window, have no result
        yet and weren't called off."""
        start = now - pd.Timedelta(days=lookback_days)
        with self._conn() as c:
            rows = c.execute(
                "SELECT key, dataset, kickoff FROM matches WHERE home_goals IS NULL AND status IS NULL"
            ).fetchall()
        out = [dict(r) for r in rows if start <= pd.Timestamp(r["kickoff"]) < now]
        return sorted(out, key=lambda r: pd.Timestamp(r["kickoff"]))

    def set_result(self, key: str, hg: int | None, ag: int | None, status: str) -> None:
        with self._conn() as c:
            c.execute("UPDATE matches SET home_goals = ?, away_goals = ?, status = ? WHERE key = ?",
                      (hg, ag, status, key))

    # ------------------------------------------------------- track record
    def finished(self, competition: str | None = None) -> list[dict]:
        """Matches with a result and a snapshot taken before kick-off, newest
        first, each with that last pre-kick-off snapshot's probabilities and
        the last typed price per market before kick-off."""
        sql = "SELECT * FROM matches WHERE home_goals IS NOT NULL"
        args: tuple = ()
        if competition:
            sql += " AND competition = ?"
            args = (competition,)
        out = []
        with self._conn() as c:
            for m in c.execute(sql, args).fetchall():
                kickoff = pd.Timestamp(m["kickoff"])
                snaps = c.execute(
                    "SELECT taken_at, probabilities FROM snapshots WHERE match_key = ? ORDER BY id", (m["key"],)
                ).fetchall()
                before = [s for s in snaps if pd.Timestamp(s["taken_at"]) < kickoff]
                if not before:
                    continue
                odds: dict[str, float] = {}
                for e in c.execute(
                    "SELECT market, odds, entered_at FROM odds_entries WHERE match_key = ? ORDER BY id", (m["key"],)
                ):
                    if pd.Timestamp(e["entered_at"]) < kickoff:
                        odds[e["market"]] = e["odds"]
                out.append({
                    "key": m["key"], "dataset": m["dataset"], "competition": m["competition"],
                    "detail": m["detail"], "kickoff": m["kickoff"], "home": m["home"], "away": m["away"],
                    "hg": m["home_goals"], "ag": m["away_goals"],
                    "probabilities": json.loads(before[-1]["probabilities"]), "odds": odds,
                })
        return sorted(out, key=lambda x: pd.Timestamp(x["kickoff"]), reverse=True)
