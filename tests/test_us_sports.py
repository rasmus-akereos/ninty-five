import pandas as pd
import pytest

from ninety_five import us_sports
from ninety_five.history import HistoryStore
from ninety_five.model import is_push, settle
from ninety_five.stats import market_group

NOW = pd.Timestamp("2026-11-09T08:00:00+00:00")


def _event(id_="ev1", home="Detroit Lions", away="Chicago Bears", t="2026-11-09T18:00:00Z", books=None):
    return {"id": id_, "sport_key": "americanfootball_nfl", "commence_time": t, "home_team": home, "away_team": away,
            "bookmakers": books if books is not None else [{"key": "pinnacle", "markets": [
                {"key": "h2h", "outcomes": [{"name": away, "price": 3.1}, {"name": home, "price": 1.4}]},
                {"key": "spreads", "outcomes": [{"name": away, "price": 1.95, "point": 7.5},
                                                {"name": home, "price": 1.91, "point": -7.5}]},
                {"key": "totals", "outcomes": [{"name": "Over", "price": 1.9, "point": 47},
                                               {"name": "Under", "price": 1.95, "point": 47}]}]}]}


def test_prices_in_app_market_names():
    p = us_sports.sharp_prices(_event())
    assert p["ML 1"] == {"odds": 1.4, "book": "pinnacle"} and p["ML 2"]["odds"] == 3.1
    assert p["Spread 1 -7.5"]["odds"] == 1.91 and p["Spread 2 +7.5"]["odds"] == 1.95
    assert p["Over 47"]["odds"] == 1.9 and p["Under 47"]["odds"] == 1.95


def test_incomplete_markets_are_dropped():
    ev = _event(books=[{"key": "pinnacle", "markets": [
        {"key": "h2h", "outcomes": [{"name": "Detroit Lions", "price": 1.4}]},
        {"key": "totals", "outcomes": [{"name": "Over", "price": 1.9, "point": 47}]}]}])
    assert us_sports.sharp_prices(ev) == {}


def test_fair_devigs_each_two_way_pair():
    fair = us_sports.fair({"ML 1": 1.4, "ML 2": 3.1, "Spread 1 -7.5": 1.91, "Spread 2 +7.5": 1.95,
                           "Over 47": 1.9, "Under 47": 1.95, "Over 50": 2.4})
    assert fair["ML 1"] + fair["ML 2"] == pytest.approx(1)
    assert fair["ML 1"] > 1 / 1.4 - 0.05 and fair["ML 1"] < 1 / 1.4
    assert fair["Spread 1 -7.5"] + fair["Spread 2 +7.5"] == pytest.approx(1)
    assert fair["Over 47"] + fair["Under 47"] == pytest.approx(1)
    assert "Over 50" not in fair  # no Under 50 to strip the margin


def test_settle_moneyline_spreads_totals_and_pushes():
    assert settle("ML 1", 27, 24) is True and settle("ML 2", 27, 24) is False
    assert settle("Spread 1 -3.5", 27, 24) is False and settle("Spread 2 +3.5", 27, 24) is True
    assert settle("Spread 1 -2.5", 27, 24) is True
    assert settle("Over 47.5", 27, 24) is True and settle("Under 51.5", 27, 24) is True
    assert is_push("Spread 1 -3", 27, 24) and is_push("Over 51", 27, 24) and is_push("ML 1", 20, 20)
    assert not is_push("Spread 1 -3.5", 27, 24) and not is_push("1", 1, 1) and not is_push("Over 2.5", 1, 1)


def test_market_groups():
    assert market_group("ML 1") == "Moneyline" and market_group("Spread 2 +7.5") == "Spread"


def _fake_get(calls, events=None, remaining=440):
    def get(path, params):
        calls.append((path, params))
        return events if events is not None else [_event()], {"remaining": str(remaining), "used": "60", "last": "3"}
    return get


def test_fetch_stores_games_prices_and_credits(tmp_path, monkeypatch):
    h = HistoryStore(tmp_path / "h.sqlite")
    calls = []
    monkeypatch.setattr(us_sports.sharp_odds, "api_key", lambda: "k")
    monkeypatch.setattr(us_sports.sharp_odds, "_get", _fake_get(calls))
    r = us_sports.fetch(h, "NFL", NOW)
    assert r["fetched"] and calls[0][0] == "sports/americanfootball_nfl/odds"
    assert calls[0][1]["markets"] == "h2h,spreads,totals"
    games = h.us_games("NFL", NOW)
    assert [g["key"] for g in games] == ["us:ev1"] and games[0]["home"] == "Detroit Lions"
    assert h.sharp_for("us:ev1")["prices"]["ML 1"]["odds"] == 1.4
    st = h.odds_api_state(NOW)
    assert st["spent_today"] == 3 and st["remaining"] == 440
    # Straight after: no second fetch (MIN_GAP).
    r = us_sports.fetch(h, "NFL", NOW + pd.Timedelta(minutes=10))
    assert not r["fetched"] and "minutes" in r["message"] and len(calls) == 1


def test_fetch_keeps_the_reserve_and_needs_a_key(tmp_path, monkeypatch):
    h = HistoryStore(tmp_path / "h.sqlite")
    assert not us_sports.fetch(h, "NBA", NOW)["fetched"]  # conftest: no key
    h.log_odds_api("2026-11-09T07:00:00+00:00", "soccer_epl", 2, 12)
    calls = []
    monkeypatch.setattr(us_sports.sharp_odds, "api_key", lambda: "k")
    monkeypatch.setattr(us_sports.sharp_odds, "_get", _fake_get(calls))
    r = us_sports.fetch(h, "NBA", NOW)
    assert not r["fetched"] and calls == [] and "credits" in r["message"]


def test_fetch_failure_is_a_message(tmp_path, monkeypatch):
    def boom(path, params):
        raise OSError("down")
    monkeypatch.setattr(us_sports.sharp_odds, "api_key", lambda: "k")
    monkeypatch.setattr(us_sports.sharp_odds, "_get", boom)
    r = us_sports.fetch(HistoryStore(tmp_path / "h.sqlite"), "NFL", NOW)
    assert not r["fetched"] and "OSError" in r["message"]


def test_us_games_lists_upcoming_and_recent_games_soonest_first(tmp_path):
    h = HistoryStore(tmp_path / "h.sqlite")
    h.save_us_games([
        {"key": "us:a", "sport": "NFL", "home": "A", "away": "B", "kickoff": "2026-11-10T01:15:00+00:00"},
        {"key": "us:b", "sport": "NFL", "home": "C", "away": "D", "kickoff": "2026-11-09T18:00:00+00:00"},
        {"key": "us:c", "sport": "NFL", "home": "E", "away": "F", "kickoff": "2026-11-08T18:00:00+00:00"},  # yesterday
        {"key": "us:d", "sport": "NBA", "home": "G", "away": "H", "kickoff": "2026-11-10T00:30:00+00:00"}],
        "2026-11-09T07:00:00+00:00")
    assert [g["key"] for g in h.us_games("NFL", NOW)] == ["us:b", "us:a"]
    assert h.us_game("us:d")["sport"] == "NBA"
    assert h.competitions()["us:d"] == "NBA"
