"""Shared glue: fit/cache models per data set and build the fixture board."""

from __future__ import annotations

import logging
import threading
import time
import uuid

import pandas as pd

from . import data as data_mod
from . import cup_fixtures, fixtures, intl_fixtures, league_fixtures, uefa
from .model import CLUB_PARAMS, INTL_PARAMS, DixonColes, markets

LEAGUE_LOOKBACK_DAYS = 31
BOARD_MARKETS = ["1", "X", "2", "Over 2.5", "Under 2.5", "BTTS Yes", "BTTS No"]
MODEL_TTL_SECONDS = 6 * 3600

_models: dict[str, tuple[float, DixonColes, pd.DataFrame]] = {}
_lock = threading.Lock()
log = logging.getLogger("ninty_five")


def datasets() -> dict[str, str]:
    return {"EUROPE": "Europe: Champions League + top leagues (auto fixtures)",
            "UEFA": "Europe: Europa + Conference League (20 leagues + European results)", **data_mod.COUNTRY_NAMES,
            **data_mod.available_datasets()}


def _fit(key: str, refresh: bool | str = False) -> tuple[DixonColes, pd.DataFrame]:
    """Fit the model for a data set and return it with its training frame.
    `refresh="current"` re-downloads only a country's current season."""
    key = key.upper()
    if key == "EUROPE":
        df, groups = fixtures.load_europe(refresh=bool(refresh))
        return DixonColes(**CLUB_PARAMS).fit(df, groups=groups), df
    if key == "UEFA":
        df, groups, _ = uefa.load_uefa(refresh_current=bool(refresh))
        return DixonColes(**CLUB_PARAMS).fit(df, groups=groups), df
    if key in data_mod.COUNTRIES:
        df, groups = data_mod.load_country(key, refresh_current=bool(refresh))
        return DixonColes(**CLUB_PARAMS).fit(df, groups=groups or None), df
    refresh = bool(refresh)
    df = data_mod.load_dataset(key, refresh=refresh)
    return DixonColes(**(INTL_PARAMS if key == "INTL" else CLUB_PARAMS)).fit(df), df


def fit_dataset(key: str, refresh: bool = False) -> DixonColes:
    return _fit(key, refresh)[0]


def _cached(key: str, refresh: bool | str = False) -> tuple[float, DixonColes, pd.DataFrame]:
    key = key.upper()
    with _lock:
        cached = _models.get(key)
        # EUROPE and country data update during the season, so refit periodically.
        seasonal = key in ("EUROPE", "UEFA") or key in data_mod.COUNTRIES
        expired = cached and seasonal and time.time() - cached[0] > MODEL_TTL_SECONDS
        if refresh or cached is None or expired:
            _models[key] = (time.time(), *_fit(key, refresh=refresh))
        return _models[key]


def get_model(key: str, refresh: bool = False) -> DixonColes:
    return _cached(key, refresh)[1]


def training_data(key: str) -> pd.DataFrame:
    """The results the cached model for `key` was fitted on (data.COLUMNS)."""
    return _cached(key)[2]


def form(df: pd.DataFrame, team: str, before: pd.Timestamp, n: int = 5) -> list[dict]:
    """A team's last `n` results before `before`, most recent first."""
    cutoff = before.tz_convert(None) if before.tzinfo else before
    games = df[((df["home"] == team) | (df["away"] == team)) & (pd.to_datetime(df["date"]) < cutoff)]
    out = []
    for g in games.sort_values("date", ascending=False).head(n).itertuples():
        at_home = g.home == team
        gf, ga = (int(g.hg), int(g.ag)) if at_home else (int(g.ag), int(g.hg))
        out.append({
            "date": pd.Timestamp(g.date).strftime("%Y-%m-%d"),
            "opponent": g.away if at_home else g.home,
            "venue": "N" if bool(g.neutral) else ("H" if at_home else "A"),
            "gf": gf,
            "ga": ga,
            "result": "W" if gf > ga else "D" if gf == ga else "L",
        })
    return out


def _priced(model: DixonColes, df: pd.DataFrame, row: dict) -> dict:
    """Add model prices for every market and both teams' form to a fixture
    row, or mark it unrated when the model doesn't know a team."""
    home, away, neutral = row["home"], row["away"], row["neutral"]
    try:
        lh, la = model.expected_goals(home, away, neutral)
    except KeyError:
        return {**row, "rated": False}
    kickoff = pd.Timestamp(row["kickoff"])
    return {
        **row,
        "rated": True,
        "model_as_of": str(model.as_of.date()),
        "xg": [lh, la],
        "data_matches": [model.n_matches.get(home, 0), model.n_matches.get(away, 0)],
        "leagues": [model.groups.get(home, ""), model.groups.get(away, "")],
        "probabilities": {k: float(v) for k, v in markets(model.score_matrix(home, away, neutral)).items()},
        "form": {"home": form(df, home, kickoff), "away": form(df, away, kickoff)},
    }


def with_min_odds(rows: list[dict], min_edge: float) -> list[dict]:
    """Minimum odds worth betting at:  (1 + min_edge) / p_model."""
    return [
        {**r, "min_odds": {k: (1 + min_edge) / r["probabilities"][k] for k in BOARD_MARKETS}} if r.get("rated") else r
        for r in rows
    ]


def club_board(days: int) -> list[dict]:
    _, model, df = _cached("EUROPE")
    rows = []
    for f in fixtures.upcoming(days).itertuples():
        row = {
            "key": f"fd:{int(f.match_id)}",
            "dataset": "EUROPE",
            "match_id": int(f.match_id),
            "kickoff": f.kickoff.isoformat(),
            "competition": f.competition,
            "detail": "",
            "stage": f.stage,
            "home": f.home,
            "away": f.away,
            "neutral": f.stage == "FINAL",
        }
        rows.append(_priced(model, df, row))
    return rows


def intl_board(days: int) -> list[dict]:
    """National-team matches (no football-data.org match id)."""
    _, model, df = _cached("INTL")
    rows = []
    for f in intl_fixtures.upcoming(days).itertuples():
        row = {
            "key": f"espn:{f.event_id}",
            "dataset": "INTL",
            "match_id": None,
            "kickoff": f.kickoff.isoformat(),
            "competition": "International",
            "detail": f.league,
            "stage": "",
            "home": f.home,
            "away": f.away,
            "neutral": bool(f.neutral),
        }
        rows.append(_priced(model, df, row))
    return rows


def league_board(days: int, now: pd.Timestamp | None = None) -> list[dict]:
    """Lower-league matches (football-data.co.uk), priced by country models."""
    rows = []
    for f in league_fixtures.upcoming(days, now).itertuples():
        _, model, df = _cached(f.dataset)
        row = {
            "key": f.key,
            "dataset": f.dataset,
            "match_id": None,
            "kickoff": f.kickoff.isoformat(),
            "competition": f.competition,
            "detail": "",
            "stage": "",
            "home": f.home,
            "away": f.away,
            "neutral": False,
            "unmatched": getattr(f, "unmatched", ""),  # fixture names not found in the division
        }
        rows.append(_priced(model, df, row))
    return rows


def cup_board(days: int, now: pd.Timestamp | None = None) -> tuple[list[dict], list[str]]:
    """Domestic cup matches between known clubs (ESPN), priced by country
    models. Returns (rows, warnings about unmapped league clubs)."""
    fx, warnings = cup_fixtures.upcoming(days, now)
    rows = []
    for f in fx.itertuples():
        _, model, df = _cached(f.dataset)
        row = {
            "key": f.key,
            "dataset": f.dataset,
            "match_id": None,
            "kickoff": f.kickoff.isoformat(),
            "competition": f.competition,
            "detail": "",
            "stage": "",
            "home": f.home,
            "away": f.away,
            "neutral": bool(f.neutral),
        }
        rows.append(_priced(model, df, row))
    return rows, warnings


BOARD_SOURCES = (
    ("Club fixtures", "club_board"),
    ("National-team fixtures", "intl_board"),
    ("Lower-league fixtures", "league_board"),
    ("Cup fixtures", "cup_board"),
)


def build_board(days: int = 7) -> tuple[list[dict], list[str]]:
    """Fresh upcoming fixtures from every source with model prices and form.

    Returns (rows, warnings). A source that fails becomes a warning so the
    others still show; if all fail, the first error is raised."""
    rows: list[dict] = []
    warnings: list[str] = []
    errors: list[Exception] = []
    for name, fn in BOARD_SOURCES:
        try:
            got = globals()[fn](days)
            if isinstance(got, tuple):  # (rows, warnings)
                got, extra = got
                warnings += extra
            rows += got
        except Exception as exc:
            errors.append(exc)
            warnings.append(f"{name} unavailable: {exc}")
    if len(errors) == len(BOARD_SOURCES):
        raise errors[0]
    for r in rows:
        if r.get("unmatched"):
            warnings.append(f"{r['competition']}: unknown team name {r['unmatched']} (match shown unrated)")
    rows.sort(key=lambda r: r["kickoff"])
    return rows, warnings


def board(days: int = 7, min_edge: float = 0.08) -> tuple[list[dict], list[str]]:
    """Fresh board with minimum odds (used by the CLI)."""
    rows, warnings = build_board(days)
    return with_min_odds(rows, min_edge), warnings


def update_results(history, bets, now: pd.Timestamp) -> list[int]:
    """Fetch 90-minute results for board matches that have kicked off (last 14
    days; 31 for lower-league matches, whose results arrive later and are
    given up after 30 days), store them, and settle open bets. Each source is
    best effort."""
    all_pending = history.pending_results(now, lookback_days=LEAGUE_LOOKBACK_DAYS)
    recent = now - pd.Timedelta(days=14)
    pending = [p for p in all_pending
               if not p["key"].startswith("fdcu:") and pd.Timestamp(p["kickoff"]) >= recent]
    found: dict[str, tuple[int, int]] = {}
    settled: list[int] = []

    try:  # clubs: same 14-day window as pending_results
        club = results_frame(days=14)
        settled += bets.auto_settle(club)  # bets logged before match keys existed
        by_id = {int(r.match_id): (int(r.hg), int(r.ag)) for r in club.itertuples()}
        for p in pending:
            if p["key"].startswith("fd:") and int(p["key"][3:]) in by_id:
                found[p["key"]] = by_id[int(p["key"][3:])]
                history.set_result(p["key"], *found[p["key"]], "FINISHED")
    except Exception as exc:
        log.warning("club results unavailable: %s", exc)

    intl = [p for p in pending if p["key"].startswith("espn:")]
    if intl:
        try:
            since = min(pd.Timestamp(p["kickoff"]) for p in intl)
            slugs = intl_fixtures.SLUGS + list(cup_fixtures.ESPN_SLUGS)  # national teams, cups, Superliga
            res = {r.event_id: r for r in intl_fixtures.results(since, now, slugs=slugs).itertuples()}
            for p in intl:
                r = res.get(p["key"][5:])
                if r is None:
                    continue
                if r.status in intl_fixtures.CALLED_OFF:
                    history.set_result(p["key"], None, None, r.status)
                elif pd.notna(r.hg) and pd.notna(r.ag):
                    found[p["key"]] = (int(r.hg), int(r.ag))
                    history.set_result(p["key"], *found[p["key"]], r.status)
        except Exception as exc:
            log.warning("national-team and cup results unavailable: %s", exc)

    lower = [p for p in all_pending if p["key"].startswith("fdcu:")]
    for division in sorted({p["key"].split(":")[1] for p in lower}):
        try:
            res = league_fixtures.results(division)
        except Exception as exc:
            log.warning("%s results unavailable: %s", division, exc)
            continue
        for p in (p for p in lower if p["key"].split(":")[1] == division):
            m = history.match(p["key"])
            listed = pd.Timestamp(m["first_seen"]) if m["first_seen"][:1].isdigit() else None
            got = league_fixtures.match_result(res, m["home"], m["away"], pd.Timestamp(p["kickoff"]), not_before=listed)
            if got is not None:
                found[p["key"]] = got
                history.set_result(p["key"], *got, "FINISHED")
            elif now - pd.Timestamp(p["kickoff"]) > league_fixtures.RESULT_WINDOW:
                history.set_result(p["key"], None, None, "NO_RESULT")

    settled += bets.auto_settle_keys(found)
    return sorted(set(settled))


def refresh_board(history, bets, days: int, now: pd.Timestamp | None = None) -> dict:
    """Fetch a fresh board, store it (snapshots included), then fetch results
    and auto-settle bets. Result failures never fail the refresh."""
    now = now or pd.Timestamp.now(tz="UTC")
    rows, warnings = build_board(days)
    snapshots = history.save_board(rows, now.isoformat(timespec="seconds"))
    settled: list[int] = []
    try:
        settled = update_results(history, bets, now)
    except Exception as exc:  # best effort
        warnings.append(f"Results unavailable: {exc}")
    return {"warnings": warnings, "auto_settled": settled, "snapshots": snapshots, "matches": len(rows)}


def run_refresh(history, bets, days: int, kind: str, now: pd.Timestamp | None = None,
                refit_countries: bool = False) -> dict | None:
    """One guarded refresh (daily or manual): None if another is running.
    The daily run first refits the country models on fresh current-season
    data. Every run's outcome is recorded; a failure is re-raised."""
    holder = f"{kind}-{uuid.uuid4().hex[:8]}"
    started = now or pd.Timestamp.now(tz="UTC")
    if not history.take_lease("refresh", holder, started):
        return None
    iso = lambda t: t.isoformat(timespec="seconds")  # noqa: E731
    try:
        warnings: list[str] = []
        if refit_countries:
            for key in [*data_mod.COUNTRIES, "UEFA"]:
                try:
                    _cached(key, refresh="current")
                except Exception as exc:  # keep the previous model
                    warnings.append(f"Could not update {key} data: {exc}")
            if uefa.last_resolution is not None:  # unmatched European clubs
                warnings += uefa.last_resolution.warnings
        out = refresh_board(history, bets, days, now=now)
        out["warnings"] = warnings + out["warnings"]
        history.record_run(kind, iso(started), iso(pd.Timestamp.now(tz="UTC")), out.get("matches", 0),
                           out["warnings"], None)
        return out
    except Exception as exc:
        history.record_run(kind, iso(started), iso(pd.Timestamp.now(tz="UTC")), 0, [], str(exc) or type(exc).__name__)
        raise
    finally:
        history.release_lease("refresh", holder)


def results_frame(days: int = 7) -> pd.DataFrame:
    return fixtures.recent_results(days)
