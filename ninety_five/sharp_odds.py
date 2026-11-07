"""Sharp bookmaker prices from The Odds API (the-odds-api.com, free plan).

Why
---
A walk-forward test over two seasons and 8,000 matches showed Pinnacle's
de-margined prices predict results clearly better than our goals model, and
that blending the model in only makes them worse. So when a sharp price is
available, a Danske Spil price is judged against Pinnacle's fair price, not
the model's: a bet is value when Danske Spil pays more than Pinnacle thinks
the outcome is worth. The model stays on screen as a second opinion.

Budget
------
The free plan has 500 credits a month. One request costs one credit per
market type (1X2 = h2h, over/under = totals) for one league, so a request
for both costs 2. Prices are only fetched:
* for leagues with a board match kicking off in the next HORIZON hours,
* at most once per MIN_GAP per league,
* within a daily allowance: the credits left at the start of the day (minus
  a RESERVE), spread evenly over the days left in the month.
The soonest matches' leagues go first. Nothing runs in a loop: fetching is
part of the daily refresh and the refresh button.

Key: ODDS_API_KEY in the project's .env (or the environment).
"""

from __future__ import annotations

import calendar
import json
import os
import urllib.parse
import urllib.request

import pandas as pd

from .fixtures import ENV_FILE
from .import_odds import find_match

BASE_URL = "https://api.the-odds-api.com/v4"
KEY_NAME = "ODDS_API_KEY"

# Board competition -> The Odds API sport key. The Championship (England 2)
# and the Conference League are not covered.
SPORTS = {
    "England": "soccer_epl",
    "England 1": "soccer_epl",
    "England 3": "soccer_england_league1",
    "England 4": "soccer_england_league2",
    "FA Cup": "soccer_fa_cup",
    "Carabao Cup": "soccer_england_efl_cup",
    "Germany": "soccer_germany_bundesliga",
    "Germany 1": "soccer_germany_bundesliga",
    "Germany 2": "soccer_germany_bundesliga2",
    "DFB-Pokal": "soccer_germany_dfb_pokal",
    "Spain": "soccer_spain_la_liga",
    "Spain 1": "soccer_spain_la_liga",
    "Spain 2": "soccer_spain_segunda_division",
    "Copa del Rey": "soccer_spain_copa_del_rey",
    "Italy": "soccer_italy_serie_a",
    "Italy 1": "soccer_italy_serie_a",
    "Italy 2": "soccer_italy_serie_b",
    "France": "soccer_france_ligue_one",
    "France 1": "soccer_france_ligue_one",
    "France 2": "soccer_france_ligue_two",
    "Netherlands": "soccer_netherlands_eredivisie",
    "Portugal": "soccer_portugal_primeira_liga",
    "Denmark 1": "soccer_denmark_superliga",
    "Champions League": "soccer_uefa_champs_league",
    "Europa League": "soccer_uefa_europa_league",
}

MARKETS = "h2h,totals"
COST = len(MARKETS.split(","))   # credits per league per fetch
BOOKS = ("pinnacle", "betfair_ex_eu", "matchbook")  # first one quoting a market wins
BOOK_NAMES = {"pinnacle": "Pinnacle", "betfair_ex_eu": "Betfair Exchange", "matchbook": "Matchbook"}
HORIZON = pd.Timedelta(hours=48)
MIN_GAP = pd.Timedelta(hours=4)
KICKOFF_SLACK = pd.Timedelta(hours=3)  # the two sources' kick-off times may differ a little
RESERVE = 10
FREE_CREDITS = 500


def api_key() -> str | None:
    if os.environ.get(KEY_NAME):
        return os.environ[KEY_NAME]
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text().splitlines():
            k, _, v = line.partition("=")
            if k.strip() == KEY_NAME and v.strip():
                return v.strip().strip('"').strip("'")
    return None


def _get(path: str, params: dict) -> tuple[object, dict]:
    """GET from The Odds API: (JSON body, credit headers). Tests replace this."""
    url = f"{BASE_URL}/{path}?" + urllib.parse.urlencode(params)
    with urllib.request.urlopen(url, timeout=20) as r:
        headers = {k: r.headers.get(f"x-requests-{k}") for k in ("remaining", "used", "last")}
        return json.load(r), headers


# ----------------------------------------------------------------- parsing
def _prices(bookmaker: dict, home: str, away: str) -> dict[str, float]:
    """One bookmaker's prices in the app's market names (1/X/2, Over/Under x.5)."""
    out: dict[str, float] = {}
    for m in bookmaker.get("markets", []):
        outcomes = m.get("outcomes", [])
        if m.get("key") == "h2h":
            names = {home: "1", away: "2", "Draw": "X"}
            got = {names[o["name"]]: float(o["price"]) for o in outcomes if o.get("name") in names}
            if len(got) == 3:
                out.update(got)
        elif m.get("key") == "totals":
            lines: dict[float, dict[str, float]] = {}
            for o in outcomes:
                point = o.get("point")
                if point is None or o.get("name") not in ("Over", "Under"):
                    continue
                lines.setdefault(float(point), {})[o["name"]] = float(o["price"])
            for point, sides in lines.items():
                # Quarter lines (2.25, 2.75) settle differently: only whole halves.
                if len(sides) == 2 and (point * 2) % 2 == 1:
                    for side, price in sides.items():
                        out[f"{side} {point:g}"] = price
    return out


def sharp_prices(event: dict) -> dict[str, dict]:
    """{market: {"odds", "book"}} for one event, each market from the first
    book in BOOKS that quotes it completely."""
    home, away = event.get("home_team"), event.get("away_team")
    books = {b.get("key"): b for b in event.get("bookmakers", [])}
    out: dict[str, dict] = {}
    for book in BOOKS:
        if book not in books:
            continue
        for market, price in _prices(books[book], home, away).items():
            out.setdefault(market, {"odds": price, "book": book})
    return out


# ------------------------------------------------------------------ budget
def days_left_in_month(now: pd.Timestamp) -> int:
    last = calendar.monthrange(now.year, now.month)[1]
    return last - now.day + 1


def allowance(remaining: int | None, spent_today: int, now: pd.Timestamp) -> int:
    """Credits that may be spent today in total (including what's spent)."""
    left = FREE_CREDITS if remaining is None else remaining
    start_of_day = left + spent_today
    return max(int((start_of_day - RESERVE) / days_left_in_month(now)), 0)


def due_sports(rows: list[dict], now: pd.Timestamp, last_fetch: dict[str, str]) -> list[str]:
    """Sport keys with a board match kicking off within HORIZON, not fetched
    within MIN_GAP, soonest kick-off first."""
    soonest: dict[str, pd.Timestamp] = {}
    for r in rows:
        sport = SPORTS.get(r.get("competition"))
        k = pd.Timestamp(r["kickoff"])
        if sport is None or not (now <= k < now + HORIZON):
            continue
        soonest[sport] = min(soonest.get(sport, k), k)
    due = [s for s in soonest if s not in last_fetch or now - pd.Timestamp(last_fetch[s]) >= MIN_GAP]
    return sorted(due, key=lambda s: soonest[s])


# ------------------------------------------------------------------ update
def match_events(events: list, rows: list[dict], sport: str) -> list[tuple[dict, dict]]:
    """(board row, event) pairs: same league, kick-off close, both teams match."""
    league = [r for r in rows if SPORTS.get(r.get("competition")) == sport]
    pairs = []
    for e in events:
        try:
            t = pd.Timestamp(e["commence_time"])
        except (KeyError, ValueError):
            continue
        near = [r for r in league if abs(pd.Timestamp(r["kickoff"]) - t) <= KICKOFF_SLACK]
        row = find_match(e.get("home_team", ""), e.get("away_team", ""), near)
        if row is not None:
            pairs.append((row, e))
    return pairs


def update(history, rows: list[dict], now: pd.Timestamp | None = None) -> list[str]:
    """Fetch sharp prices for the leagues that are due and within budget, and
    store them per board match. Returns warnings; never raises."""
    now = now or pd.Timestamp.now(tz="UTC")
    key = api_key()
    if not key:
        return []
    warnings: list[str] = []
    try:
        state = history.odds_api_state(now)
        due = due_sports(rows, now, state["last_fetch"])
        budget = allowance(state["remaining"], state["spent_today"], now)
        spent = state["spent_today"]
        skipped = 0
        for sport in due:
            if spent + COST > budget:
                skipped += 1
                continue
            try:
                events, headers = _get(f"sports/{sport}/odds", {
                    "apiKey": key, "regions": "eu", "markets": MARKETS, "oddsFormat": "decimal",
                    "bookmakers": ",".join(BOOKS)})
            except Exception as exc:
                warnings.append(f"Sharp odds unavailable for {sport}: {type(exc).__name__}")
                continue
            cost = int(headers.get("last") or COST)
            remaining = int(headers["remaining"]) if headers.get("remaining") is not None else None
            spent += cost
            stamp = now.isoformat(timespec="seconds")
            history.log_odds_api(stamp, sport, cost, remaining)
            for row, event in match_events(events if isinstance(events, list) else [], rows, sport):
                prices = sharp_prices(event)
                if prices:
                    history.save_sharp(row["key"], stamp, prices)
        if skipped:
            warnings.append(f"Sharp odds: {skipped} league(s) skipped today to stay within the free credits")
    except Exception as exc:  # sharp prices are a bonus: never fail the refresh
        warnings.append(f"Sharp odds unavailable: {exc}")
    return warnings
