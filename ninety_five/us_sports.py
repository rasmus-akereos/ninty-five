"""NFL and NBA: Danske Spil's prices judged against Pinnacle's.

There is no model here. Pinnacle's prices for these leagues are among the
sharpest anywhere, so its de-margined price is the fair price, exactly as for
football (sharp_odds.py). A Danske Spil price is judged only when Pinnacle
quotes the same market on the same line: a spread of -3 and one of -3.5 are
different bets (many NFL games end 3 or 7 points apart).

Markets (home = 1, away = 2):
* "ML 1" / "ML 2": moneyline, overtime included. An NFL tie is a push.
* "Spread 1 -3.5" / "Spread 2 +3.5": point spread (handicap) on the final score.
* "Over 47.5" / "Under 47.5": total points.
Whole-number lines push (stake back) when the result lands on the line.

Budget: one fetch is 3 credits (h2h, spreads, totals) for one league. It
runs only when asked for (the button on the NFL / NBA tab), at most once per
MIN_GAP per league, and never below the credit RESERVE. The spend counts
towards the football fetch's daily allowance (same credit log).
"""

from __future__ import annotations

import pandas as pd

from . import sharp_odds
from .value import devig_power

SPORTS = {"NFL": "americanfootball_nfl", "NBA": "basketball_nba"}
MARKETS = "h2h,spreads,totals"
COST = len(MARKETS.split(","))
MIN_GAP = pd.Timedelta(hours=1)


def _prices(bookmaker: dict, home: str, away: str) -> dict[str, float]:
    out: dict[str, float] = {}
    side = {home: "1", away: "2"}
    for m in bookmaker.get("markets", []):
        outcomes = m.get("outcomes", [])
        key = m.get("key")
        if key == "h2h":
            got = {f"ML {side[o['name']]}": float(o["price"]) for o in outcomes if o.get("name") in side}
            if len(got) == 2:
                out.update(got)
        elif key == "spreads":
            got = {}
            for o in outcomes:
                if o.get("name") in side and o.get("point") is not None:
                    got[side[o["name"]]] = (float(o["point"]), float(o["price"]))
            if len(got) == 2 and got["1"][0] == -got["2"][0]:
                for s, (point, price) in got.items():
                    out[f"Spread {s} {point:+g}"] = price
        elif key == "totals":
            got = {o["name"]: (float(o["point"]), float(o["price"])) for o in outcomes
                   if o.get("name") in ("Over", "Under") and o.get("point") is not None}
            if len(got) == 2 and got["Over"][0] == got["Under"][0]:
                for s, (point, price) in got.items():
                    out[f"{s} {point:g}"] = price
    return out


def sharp_prices(event: dict) -> dict[str, dict]:
    """{market: {"odds", "book"}}, each market from the first book in
    sharp_odds.BOOKS that quotes it completely."""
    books = {b.get("key"): b for b in event.get("bookmakers", [])}
    out: dict[str, dict] = {}
    for book in sharp_odds.BOOKS:
        if book in books:
            for market, price in _prices(books[book], event.get("home_team"), event.get("away_team")).items():
                out.setdefault(market, {"odds": price, "book": book})
    return out


def pair_of(market: str) -> str | None:
    """The other side of a two-way market."""
    if market in ("ML 1", "ML 2"):
        return "ML 2" if market == "ML 1" else "ML 1"
    if market.startswith("Spread "):
        _, s, line = market.split()
        return f"Spread {'2' if s == '1' else '1'} {-float(line):+g}"
    if market.startswith("Over "):
        return "Under " + market[5:]
    if market.startswith("Under "):
        return "Over " + market[6:]
    return None


def fair(prices: dict[str, float]) -> dict[str, float]:
    """De-margined probability of every market whose other side is priced
    too. For whole lines it is the probability equivalent of the fair price
    (pushes left out), which is what an edge is computed from."""
    out: dict[str, float] = {}
    for m, o in prices.items():
        other = pair_of(m)
        if other in prices and m not in out:
            out[m], out[other] = devig_power([o, prices[other]])
    return out


def fetch(history, sport: str, now: pd.Timestamp | None = None) -> dict:
    """Fetch Pinnacle's prices for one league's games. Returns {"fetched":
    bool, "message": str | None}; never raises."""
    now = now or pd.Timestamp.now(tz="UTC")
    key = sharp_odds.api_key()
    if not key:
        return {"fetched": False, "message": f"No {sharp_odds.KEY_NAME} in .env"}
    sport_key = SPORTS[sport]
    try:
        state = history.odds_api_state(now)
        last = state["last_fetch"].get(sport_key)
        if last and now - pd.Timestamp(last) < MIN_GAP:
            wait = MIN_GAP - (now - pd.Timestamp(last))
            return {"fetched": False, "message": f"Fetched {int((now - pd.Timestamp(last)).total_seconds() // 60)} minutes ago: "
                                                 f"next fetch in {int(wait.total_seconds() // 60) + 1} minutes"}
        remaining = state["remaining"]
        if remaining is not None and remaining - COST < sharp_odds.RESERVE:
            return {"fetched": False, "message": f"Only {remaining} credits left this month: kept for football"}
        events, headers = sharp_odds._get(f"sports/{sport_key}/odds", {
            "apiKey": key, "regions": "eu", "markets": MARKETS, "oddsFormat": "decimal",
            "bookmakers": ",".join(sharp_odds.BOOKS)})
        stamp = now.isoformat(timespec="seconds")
        history.log_odds_api(stamp, sport_key, int(headers.get("last") or COST),
                             int(headers["remaining"]) if headers.get("remaining") is not None else None)
        games = []
        for e in events if isinstance(events, list) else []:
            try:
                kickoff = pd.Timestamp(e["commence_time"]).isoformat()
                g = {"key": f"us:{e['id']}", "sport": sport, "home": e["home_team"], "away": e["away_team"],
                     "kickoff": kickoff}
            except (KeyError, ValueError, TypeError):
                continue
            games.append(g)
            prices = sharp_prices(e)
            if prices:
                history.save_sharp(g["key"], stamp, prices)
        history.save_us_games(games, stamp)
        return {"fetched": True, "message": None}
    except Exception as exc:  # a failed fetch is a message, never a crash
        return {"fetched": False, "message": f"Pinnacle prices unavailable: {type(exc).__name__}"}
