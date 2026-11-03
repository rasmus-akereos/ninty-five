"""FastAPI web app: enter Danske Spil odds, get model prices and value bets."""

from __future__ import annotations

import logging
from pathlib import Path

import numpy as np
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from . import fixtures, service
from .model import DixonColes, markets
from .store import BetStore
from .value import Settings, evaluate, overround

STATIC = Path(__file__).resolve().parent / "static"

app = FastAPI(title="ninty-five")
store = BetStore()
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


@app.get("/api/fixtures")
def fixture_board(days: int = 7, min_edge: float = 0.08) -> dict:
    """Upcoming club and national-team matches with model prices. Also settles any
    open bets whose matches have finished."""
    try:
        rows, warnings = service.board(days=days, min_edge=min_edge)
    except fixtures.ApiKeyMissing as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(502, f"Could not fetch fixtures: {exc}") from exc
    settled: list[int] = []
    try:
        settled = store.auto_settle(service.results_frame())
    except Exception as exc:  # settlement is best effort
        log.warning("auto-settle failed: %s", exc)
    return {"fixtures": rows, "warnings": warnings, "auto_settled": settled}


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
    m = get_model(req.dataset)
    try:
        lh, la = m.expected_goals(req.home, req.away, req.neutral)
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    sm = m.score_matrix(req.home, req.away, req.neutral)
    probs = markets(sm)
    odds = {k: v for k, v in req.odds.items() if v and v > 1}
    selections = evaluate(probs, odds, Settings(**req.settings.model_dump()))

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
