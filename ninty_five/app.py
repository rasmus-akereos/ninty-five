"""FastAPI web app: enter Danske Spil odds, get model prices and value bets."""

from __future__ import annotations

import logging
from pathlib import Path

import numpy as np
import pandas as pd
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from . import fixtures, service, track
from .model import DixonColes, markets
from .history import HistoryStore
from .store import BetStore
from .value import Settings, evaluate, market_probabilities, overround

STATIC = Path(__file__).resolve().parent / "static"

app = FastAPI(title="ninty-five")
store = BetStore()
history = HistoryStore()
log = logging.getLogger("ninty_five")


def get_model(dataset: str, refresh: bool = False) -> DixonColes:
    try:
        return service.get_model(dataset, refresh=refresh)
    except fixtures.ApiKeyMissing as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:  # network / unknown league
        raise HTTPException(502, f"Could not load data for {dataset}: {exc}") from exc


# ----------------------------------------------------------------- schemas
class SettingsIn(BaseModel):
    alpha: float = Field(0.5, ge=0, le=1)
    kelly_fraction: float = Field(0.25, ge=0, le=1)
    max_stake_pct: float = Field(0.03, ge=0, le=1)
    min_edge: float = 0.03
    bankroll: float = Field(1000.0, gt=0)


class EvaluateIn(BaseModel):
    dataset: str = "INTL"
    home: str
    away: str
    neutral: bool = False
    odds: dict[str, float] = {}
    settings: SettingsIn = SettingsIn()
    match_key: str | None = None  # board match the odds belong to (fd:/espn:)


class BetIn(BaseModel):
    dataset: str
    home: str
    away: str
    market: str
    odds: float = Field(gt=1)
    stake: float = Field(gt=0)
    p_model: float | None = None
    p_blend: float | None = None
    ev: float | None = None
    match_id: int | None = None
    kickoff: str | None = None
    match_key: str | None = None


class SettleIn(BaseModel):
    home_goals: int | None = None
    away_goals: int | None = None
    closing_odds: float | None = None
    void: bool = False


# ------------------------------------------------------------------ routes
@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC / "index.html")


@app.get("/api/datasets")
def datasets() -> dict[str, str]:
    return service.datasets()


STALE_AFTER = pd.Timedelta(hours=6)


def _stored_board(days: int, min_edge: float) -> dict:
    fetched = history.fetched_at()
    stale = fetched is None or pd.Timestamp.now(tz="UTC") - pd.Timestamp(fetched) > STALE_AFTER
    return {
        "fixtures": service.with_min_odds(history.board(days), min_edge),
        "fetched_at": fetched,
        "stale": stale,
    }


@app.get("/api/fixtures")
def fixture_board(days: int = 7, min_edge: float = 0.08) -> dict:
    """The stored board: upcoming matches from the last fetch with their latest
    model prices and form. Never contacts the fixture sources."""
    return _stored_board(days, min_edge)


@app.post("/api/fixtures/refresh")
def refresh_fixture_board(days: int = 7, min_edge: float = 0.08) -> dict:
    """Fetch fresh fixtures, store a snapshot of every match, fetch results and
    settle finished bets, then return the stored board."""
    try:
        out = service.refresh_board(history, store, days)
    except fixtures.ApiKeyMissing as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(502, f"Could not fetch fixtures: {exc}") from exc
    return {**_stored_board(days, min_edge), "warnings": out["warnings"], "auto_settled": out["auto_settled"]}


@app.get("/api/track-record")
def track_record(competition: str | None = None) -> dict:
    """Model vs Danske Spil vs result on finished board matches."""
    matches = history.finished(competition or None)
    bets_by_key: dict[str, list[str]] = {}
    for b in store.list():
        if b.get("match_key"):
            bets_by_key.setdefault(b["match_key"], []).append(f"{b['market']} @ {b['odds']:.2f}")
    recent = []
    for m in matches[:50]:
        market = market_probabilities({k: v for k, v in m["odds"].items() if k in ("1", "X", "2")})
        recent.append({
            "kickoff": m["kickoff"], "competition": m["competition"], "detail": m["detail"],
            "home": m["home"], "away": m["away"], "result": f"{m['hg']}-{m['ag']}",
            "model": {k: m["probabilities"].get(k) for k in ("1", "X", "2")},
            "market": {k: market[k] for k in ("1", "X", "2")} if "1" in market else None,
            "bets": bets_by_key.get(m["key"], []),
        })
    return {"summary": track.summary(matches), "matches": recent}


@app.get("/api/teams")
def teams(dataset: str = "INTL") -> dict:
    m = get_model(dataset)
    return {"teams": m.teams, "as_of": str(m.as_of.date())}


@app.post("/api/refresh")
def refresh(dataset: str = "INTL") -> dict:
    m = get_model(dataset, refresh=True)
    return {"teams": len(m.teams), "as_of": str(m.as_of.date())}


@app.get("/api/ratings")
def ratings(dataset: str = "INTL", top: int = 50) -> list[dict]:
    return get_model(dataset).ratings().head(top).round(3).to_dict(orient="records")


@app.post("/api/evaluate")
def evaluate_match(req: EvaluateIn) -> dict:
    # Stamp typed odds on arrival: requests can finish out of order while the
    # model is (re)fitting, and the newest typed price must win.
    received = pd.Timestamp.now(tz="UTC").isoformat(timespec="milliseconds")
    m = get_model(req.dataset)
    try:
        lh, la = m.expected_goals(req.home, req.away, req.neutral)
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    sm = m.score_matrix(req.home, req.away, req.neutral)
    probs = markets(sm)
    odds = {k: v for k, v in req.odds.items() if v and v > 1}
    selections = evaluate(probs, odds, Settings(**req.settings.model_dump()))
    # Previous visit's prices are shown as hints, so read them before storing.
    last_odds: dict = {}
    try:
        last_odds = history.last_odds(req.match_key, req.dataset, req.home, req.away)
        history.record_odds(req.match_key, req.dataset, req.home, req.away, [s.to_dict() for s in selections], received)
    except Exception as exc:  # history must never break pricing
        log.warning("could not record odds: %s", exc)

    flat = [(int(i), int(j), float(sm[i, j])) for i, j in np.ndindex(sm.shape)]
    top_scores = sorted(flat, key=lambda t: -t[2])[:8]
    margins = {}
    if all(k in odds for k in ("1", "X", "2")):
        margins["1X2"] = overround([odds["1"], odds["X"], odds["2"]])
    if all(k in odds for k in ("Over 2.5", "Under 2.5")):
        margins["O/U 2.5"] = overround([odds["Over 2.5"], odds["Under 2.5"]])
    return {
        "home": req.home,
        "away": req.away,
        "xg": [lh, la],
        "data_matches": [m.n_matches.get(req.home, 0), m.n_matches.get(req.away, 0)],
        "model_as_of": str(m.as_of.date()),
        "probabilities": probs,
        "fair_odds": {k: (1 / v if v > 0 else None) for k, v in probs.items()},
        "top_scores": [{"score": f"{i}-{j}", "p": p} for i, j, p in top_scores],
        "margins": margins,
        "selections": [s.to_dict() for s in selections],
        "last_odds": last_odds,
    }


@app.get("/api/bets")
def list_bets() -> dict:
    return {"bets": store.list(), "summary": store.summary()}


@app.post("/api/bets")
def add_bet(bet: BetIn) -> dict:
    return {"id": store.add(bet.model_dump())}


@app.post("/api/bets/{bet_id}/settle")
def settle_bet(bet_id: int, req: SettleIn) -> dict:
    try:
        return store.settle(bet_id, **req.model_dump())
    except KeyError as exc:
        raise HTTPException(404, "Bet not found") from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.delete("/api/bets/{bet_id}")
def delete_bet(bet_id: int) -> dict:
    store.delete(bet_id)
    return {"ok": True}
