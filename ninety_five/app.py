"""FastAPI web app: enter Danske Spil odds, get model prices and value bets."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

import numpy as np
import pandas as pd
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from . import fixtures, implied, import_odds, scheduler, service, sharp_odds, stats, track, tuning, uefa, validate
from .model import COMBO_SEP, DixonColes, combo_probability, markets
from .history import HistoryStore
from .store import BetStore, DuplicateBet
from .value import Settings, evaluate, market_probabilities, overround

STATIC = Path(__file__).resolve().parent / "static"

store = BetStore()
history = HistoryStore()


@asynccontextmanager
async def lifespan(_app):
    try:  # a tuned club half-life survives restarts
        tuning.apply_stored(history)
    except Exception as exc:
        log.warning("could not apply tuned settings: %s", exc)
    # The daily fetch (07:00 Copenhagen time, with catch-up on start).
    scheduler.start(history, store)
    yield


app = FastAPI(title="ninety-five", lifespan=lifespan)
log = logging.getLogger("ninety_five")


def get_model(dataset: str, refresh: bool = False) -> DixonColes:
    try:
        return service.get_model(dataset, refresh=refresh)
    except fixtures.ApiKeyMissing as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:  # network / unknown league
        raise HTTPException(502, f"Could not load data for {dataset}: {exc}") from exc


# ----------------------------------------------------------------- schemas
class SettingsIn(BaseModel):  # the model weight alpha is the server's (tuning.py)
    kelly_fraction: float = Field(0.25, ge=0, le=1)
    max_stake_pct: float = Field(0.03, ge=0, le=1)
    min_edge: float = 0.03
    bankroll: float = Field(1000.0, gt=0)
    match_limit_pct: float = Field(0.05, ge=0, le=1)
    gap_warn: float = Field(0.15, ge=0, le=1)


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
    cash_out: float | None = Field(None, ge=0)  # Take Profit: amount paid out


# ------------------------------------------------------------------ routes
@app.get("/")
def index() -> FileResponse:
    # Always revalidate, so an updated page is picked up on the next load.
    return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-cache"})


@app.get("/api/datasets")
def datasets() -> dict[str, str]:
    return service.datasets()


STALE_AFTER = pd.Timedelta(hours=6)


def _credits() -> dict:
    """The Odds API's state for the board: whether a key is set, credits left
    and today's allowance."""
    if not sharp_odds.api_key():
        return {"enabled": False}
    now = pd.Timestamp.now(tz="UTC")
    try:
        st = history.odds_api_state(now)
    except Exception as exc:
        log.warning("could not read odds api state: %s", exc)
        return {"enabled": True}
    return {"enabled": True, "remaining": st["remaining"], "spent_today": st["spent_today"],
            "allowance": sharp_odds.allowance(st["remaining"], st["spent_today"], now)}


def _stored_board(days: int, min_edge: float) -> dict:
    fetched = history.fetched_at()
    stale = fetched is None or pd.Timestamp.now(tz="UTC") - pd.Timestamp(fetched) > STALE_AFTER
    checks = history.validation()
    rows = []
    for r in service.with_min_odds(history.board(days), min_edge):
        note = validate.unproven(r, checks)
        r = {**r, "unproven": note} if note else r
        if uefa.EUROPEAN_ONLY in (r.get("leagues") or []):  # the stored board keeps leagues, not the flag
            r = {**r, "european_only": True}
        rows.append(r)
    return {
        "fixtures": rows,
        "sharp": _credits(),
        "fetched_at": fetched,
        "stale": stale,
        "last_auto": history.last_run("auto"),
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
        out = service.run_refresh(history, store, days, kind="manual")
    except fixtures.ApiKeyMissing as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(502, f"Could not fetch fixtures: {exc}") from exc
    if out is None:
        return {**_stored_board(days, min_edge), "warnings": ["Refresh already running"], "auto_settled": []}
    return {**_stored_board(days, min_edge), "warnings": out["warnings"], "auto_settled": out["auto_settled"]}


class ImportIn(BaseModel):
    text: str = Field(max_length=200_000)
    title: str = ""


@app.post("/api/import-odds")
def import_page_odds(req: ImportIn) -> dict:
    """Odds read from a Danske Spil match page by the bookmark button, matched
    to a stored board match (match_key None if no single match fits)."""
    parsed = import_odds.parse_page(req.text, req.title)
    match = None
    if parsed["home"] and parsed["away"]:
        match = import_odds.find_match(parsed["home"], parsed["away"], history.board(days=30))
    return {**parsed, "match_key": match["key"] if match else None}


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


@app.get("/api/stats/betting")
def stats_betting() -> dict:
    bets = store.list()
    return stats.betting(bets, history.competitions(), _sharp_at_kickoff(bets))


def _utc(t) -> pd.Timestamp:
    t = pd.Timestamp(t)
    return t.tz_convert("UTC") if t.tzinfo else t.tz_localize("UTC")


def _sharp_at_kickoff(bets: list[dict]) -> dict[int, float]:
    """bet id -> Pinnacle's fair probability for its market, from the last
    fetch before kick-off (the latest so far for matches not yet started)."""
    out: dict[int, float] = {}
    for b in bets:
        if not b.get("match_key"):  # sharp prices are stored per board match
            continue
        kickoff = b.get("kickoff") or (history.match(b["match_key"]) or {}).get("kickoff")
        if not kickoff:
            continue
        prices = history.sharp_before(b["match_key"], _utc(kickoff).isoformat(timespec="seconds"))  # fetched_at is ISO UTC
        p = market_probabilities(prices).get(b["market"])
        if p is not None:
            out[b["id"]] = p
    return out


@app.get("/api/stats/soft")
def stats_soft() -> dict:
    """Typed Danske Spil prices against Pinnacle's fair prices, by league and market."""
    fits: dict[tuple, dict] = {}  # one implied-goals fit per sharp fetch
    rows = []
    last: dict[tuple, dict] = {}  # only the last typed price per match and market (slips get corrected)
    for p in history.typed_vs_sharp():
        last[(p["match_key"], p["market"])] = p
    for p in last.values():
        ident = (p["match_key"], p["fetched_at"])
        if ident not in fits:
            direct = market_probabilities(p["sharp"])
            try:
                fitted = implied.grid(p["sharp"])
            except Exception:
                fitted = None
            fits[ident] = {"direct": direct, "implied": markets(fitted[1]) if fitted else {}}
        f = fits[ident]
        prob, basis = f["direct"].get(p["market"]), "sharp"
        if prob is None:
            prob, basis = f["implied"].get(p["market"]), "implied"
        if prob is None:
            continue
        m = history.match(p["match_key"]) or {}
        rows.append({"competition": p["competition"], "market": p["market"], "odds": p["odds"],
                     "gap": float(p["odds"] * prob - 1), "basis": basis, "fair": float(1 / prob),
                     "match": f"{m.get('home', '')} – {m.get('away', '')}", "kickoff": m.get("kickoff")})
    return stats.soft_prices(rows)


@app.get("/api/stats/model")
def stats_model() -> dict:
    return stats.model_insight(history.finished())


@app.get("/api/tuning")
def tuning_overview() -> dict:
    return tuning.overview(history)


@app.post("/api/tuning/{log_id}/revert")
def revert_tuning(log_id: int) -> dict:
    try:
        row = history.revert(log_id, pd.Timestamp.now(tz="UTC").isoformat(timespec="seconds"))
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if row["setting"] == "half_life":
        service.set_club_half_life(row["new"])
    return row


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


def _staked_on(req: EvaluateIn) -> float:
    """Kroner in open bets on this match (by board key, else by team names)."""
    try:
        bets = store.list()
    except Exception as exc:  # the bet log must never break pricing
        log.warning("could not read bets: %s", exc)
        return 0.0
    def same(b):
        if req.match_key and b.get("match_key"):
            return b["match_key"] == req.match_key
        return (b["dataset"], b["home"], b["away"]) == (req.dataset, req.home, req.away)
    return float(sum(b["stake"] for b in bets if b["status"] == "open" and same(b)))


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
    competition = None
    if req.match_key:
        try:
            competition = (history.match(req.match_key) or {}).get("competition")
        except Exception as exc:  # history must never break pricing
            log.warning("could not look up match: %s", exc)
    try:
        alpha, alpha_scope = tuning.alpha_for(history.tuned(), competition)
    except Exception as exc:
        log.warning("could not read tuned settings: %s", exc)
        alpha, alpha_scope = tuning.ALPHA_DEFAULT, "default"
    sharp, sharp_probs, fitted = _sharp_context(req.match_key)
    implied_probs = markets(fitted[1]) if fitted else {}
    already = _staked_on(req)
    st = Settings(alpha=alpha, **req.settings.model_dump())
    selections = evaluate(probs, odds, st, already_staked=already, sharp=sharp_probs, implied=implied_probs)
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
        "match_exposure": {"staked": already, "limit": round(st.match_limit_pct * st.bankroll, 2)},
        "probabilities": probs,
        "fair_odds": {k: (1 / v if v > 0 else None) for k, v in probs.items()},
        "top_scores": [{"score": f"{i}-{j}", "p": p} for i, j, p in top_scores],
        "margins": margins,
        "selections": [s.to_dict() for s in selections],
        "last_odds": last_odds,
        "alpha": {"value": alpha, "scope": alpha_scope},
        "sharp": None if not sharp else {
            "fetched_at": sharp["fetched_at"],
            "books": sorted({sharp_odds.BOOK_NAMES.get(p["book"], p["book"]) for p in sharp["prices"].values()}),
            "odds": {m: p["odds"] for m, p in sharp["prices"].items()},
            "probabilities": sharp_probs,
            "implied": None if not fitted else {"xg": list(fitted[0]), "probabilities": implied_probs},
        },
    }


def _sharp_context(match_key: str | None):
    """(stored sharp prices or None, their de-margined probabilities, the
    implied (goals, score grid) or None). Never raises."""
    try:
        sharp = history.sharp_for(match_key)
    except Exception as exc:  # history must never break pricing
        log.warning("could not read sharp prices: %s", exc)
        return None, {}, None
    if not sharp:
        return None, {}, None
    prices = {m: p["odds"] for m, p in sharp["prices"].items()}
    try:
        fitted = implied.grid(prices)
    except Exception as exc:
        log.warning("could not fit implied goals: %s", exc)
        fitted = None
    return sharp, market_probabilities(prices), fitted


class ComboIn(BaseModel):
    dataset: str = "INTL"
    home: str
    away: str
    neutral: bool = False
    legs: list[str] = Field(min_length=2, max_length=6)
    odds: float = Field(gt=1)
    settings: SettingsIn = SettingsIn()
    match_key: str | None = None


@app.post("/api/combo")
def price_combo(req: ComboIn) -> dict:
    """A Bet Builder bet (several legs on one match, all must win): its fair
    chance from the score grid implied by Pinnacle's prices when stored, else
    from the model, and the usual edge and stake."""
    if len(set(req.legs)) < 2:
        raise HTTPException(400, "Pick at least two different legs")
    m = get_model(req.dataset)
    try:
        model_grid = m.score_matrix(req.home, req.away, req.neutral)
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    try:
        p_model = combo_probability(model_grid, req.legs)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    sharp, _, fitted = _sharp_context(req.match_key)
    p_fair = combo_probability(fitted[1], req.legs) if fitted else None
    if (p_fair if p_fair is not None else p_model) <= 1e-9:
        raise HTTPException(400, "These legs can't all win together")
    name = COMBO_SEP.join(req.legs)
    competition = (history.match(req.match_key) or {}).get("competition") if req.match_key else None
    try:
        alpha, _ = tuning.alpha_for(history.tuned(), competition)
    except Exception:
        alpha = tuning.ALPHA_DEFAULT
    st = Settings(alpha=alpha, **req.settings.model_dump())
    (sel,) = evaluate({name: p_model}, {name: req.odds}, st, already_staked=_staked_on(req),
                      implied=None if p_fair is None else {name: p_fair})
    singles = markets(fitted[1]) if fitted else markets(model_grid)
    return {
        "selection": sel.to_dict(),
        "independent": float(np.prod([singles[leg] for leg in req.legs])),
        "fetched_at": sharp["fetched_at"] if sharp else None,
    }


@app.get("/api/bets")
def list_bets() -> dict:
    return {"bets": store.list(), "summary": store.summary()}


@app.post("/api/bets")
def add_bet(bet: BetIn) -> dict:
    try:
        return {"id": store.add(bet.model_dump())}
    except DuplicateBet as exc:
        raise HTTPException(409, str(exc)) from exc


@app.get("/api/bets/{bet_id}/detail")
def bet_detail(bet_id: int) -> dict:
    """What the bet looked like: the Danske Spil prices typed when it was
    logged, the model's probabilities, and Pinnacle's price when it was logged
    and at kick-off, each with how much better the bet's odds were ("vs")."""
    bet = store.get(bet_id)
    if bet is None:
        raise HTTPException(404, "No such bet")
    logged = _utc(bet["created"]) + pd.Timedelta(minutes=1)  # odds are stored just before the bet
    key = bet.get("match_key")
    kickoff = bet.get("kickoff") or ((history.match(key) or {}).get("kickoff") if key else None)

    def pinnacle(before: pd.Timestamp) -> dict | None:
        got = history.sharp_fetch_before(key, before.isoformat(timespec="seconds"))
        if not got:
            return None
        probs = market_probabilities(got["odds"])
        p = probs.get(bet["market"])
        return {**got, "probabilities": probs, "vs": None if p is None else bet["odds"] * p - 1}

    at_kickoff = pinnacle(_utc(kickoff)) if kickoff else None
    return {
        "bet": bet,
        "kickoff": kickoff,
        "danske_spil": history.odds_at(key, bet["dataset"], bet["home"], bet["away"], logged),
        "model": history.model_before(key, logged),
        "pinnacle_logged": pinnacle(logged),
        "pinnacle_kickoff": at_kickoff,
    }


@app.post("/api/bets/{bet_id}/settle")
def settle_bet(bet_id: int, req: SettleIn) -> dict:
    scored = req.home_goals is not None and req.away_goals is not None
    if not (scored or req.void or req.cash_out is not None or req.closing_odds is not None):
        raise HTTPException(400, "Nothing to settle: give a score, void, a Take Profit amount or closing odds")
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
