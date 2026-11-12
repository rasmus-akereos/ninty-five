import pandas as pd
import pytest

from ninety_five import sharp_odds
from ninety_five.history import HistoryStore
from ninety_five.value import Settings, evaluate

NOW = pd.Timestamp("2026-11-07T08:00:00+00:00")


def _event(home="Ipswich Town", away="AFC Bournemouth", t="2026-11-08T14:00:00Z", books=None):
    return {"home_team": home, "away_team": away, "commence_time": t, "bookmakers": books if books is not None else [
        {"key": "pinnacle", "markets": [
            {"key": "h2h", "outcomes": [{"name": away, "price": 1.92}, {"name": home, "price": 4.09},
                                        {"name": "Draw", "price": 3.75}]},
            {"key": "totals", "outcomes": [{"name": "Over", "price": 1.95, "point": 2.75},
                                           {"name": "Under", "price": 1.9, "point": 2.75}]}]},
        {"key": "betfair_ex_eu", "markets": [
            {"key": "h2h", "outcomes": [{"name": away, "price": 1.95}, {"name": home, "price": 4.2},
                                        {"name": "Draw", "price": 3.85}]},
            {"key": "totals", "outcomes": [{"name": "Over", "price": 1.8, "point": 2.5},
                                           {"name": "Under", "price": 2.1, "point": 2.5}]}]},
    ]}


def _row(key="fd:1", home="Ipswich Town FC", away="AFC Bournemouth", comp="England",
         kickoff="2026-11-08T14:00:00+00:00"):
    return {"key": key, "home": home, "away": away, "competition": comp, "kickoff": kickoff}


def test_prices_take_each_market_from_the_first_sharp_book():
    p = sharp_odds.sharp_prices(_event())
    assert p["1"] == {"odds": 4.09, "book": "pinnacle"}
    assert p["X"] == {"odds": 3.75, "book": "pinnacle"} and p["2"] == {"odds": 1.92, "book": "pinnacle"}
    # Pinnacle quotes only a quarter line (2.75): kept for the goals fit; 2.5 comes from the exchange.
    assert p["Over 2.5"] == {"odds": 1.8, "book": "betfair_ex_eu"}
    assert p["Over 2.75"] == {"odds": 1.95, "book": "pinnacle"} and p["Under 2.75"]["odds"] == 1.9


def test_incomplete_h2h_is_ignored():
    ev = _event(books=[{"key": "pinnacle", "markets": [
        {"key": "h2h", "outcomes": [{"name": "Ipswich Town", "price": 4.0}, {"name": "Draw", "price": 3.7}]}]}])
    assert sharp_odds.sharp_prices(ev) == {}


def test_allowance_spreads_credits_over_the_rest_of_the_month():
    # 7 Nov: 24 days left incl. today. 490 at start of day - 10 reserve = 480 -> 20 a day.
    assert sharp_odds.allowance(remaining=480, spent_today=10, now=NOW) == 20
    assert sharp_odds.allowance(remaining=None, spent_today=0, now=NOW) == 20  # 500 assumed
    assert sharp_odds.allowance(remaining=5, spent_today=0, now=NOW) == 0


def test_due_sports_soonest_first_within_horizon_and_gap():
    rows = [_row(comp="Germany 2", kickoff="2026-11-08T12:00:00+00:00"),
            _row(comp="England", kickoff="2026-11-07T15:00:00+00:00"),
            _row(comp="England 2", kickoff="2026-11-07T15:00:00+00:00"),       # not covered
            _row(comp="Spain", kickoff="2026-11-10T20:00:00+00:00"),           # beyond 48 h
            _row(comp="Italy", kickoff="2026-11-07T18:00:00+00:00")]           # fetched 2 h ago
    due = sharp_odds.due_sports(rows, NOW, {"soccer_italy_serie_a": "2026-11-07T06:00:00+00:00",
                                            "soccer_epl": "2026-11-07T03:00:00+00:00"})
    assert due == ["soccer_epl", "soccer_germany_bundesliga2"]


def test_events_match_board_rows_by_names_and_kickoff():
    rows = [_row(), _row(key="fd:2", home="Arsenal FC", away="Chelsea FC")]
    pairs = sharp_odds.match_events([_event(), _event("Arsenal", "Chelsea", t="2026-11-12T14:00:00Z")], rows, "soccer_epl")
    assert [r["key"] for r, _ in pairs] == ["fd:1"]  # Arsenal-Chelsea kicks off days apart


def _fake_get(calls, remaining=480):
    def get(path, params):
        calls.append((path, params))
        return [_event()], {"remaining": str(remaining), "used": "20", "last": "2"}
    return get


def test_update_fetches_stores_and_logs_credits(tmp_path, monkeypatch):
    h = HistoryStore(tmp_path / "h.sqlite")
    calls = []
    monkeypatch.setattr(sharp_odds, "api_key", lambda: "k")
    monkeypatch.setattr(sharp_odds, "_get", _fake_get(calls))
    assert sharp_odds.update(h, [_row()], NOW) == []
    assert calls[0][0] == "sports/soccer_epl/odds" and calls[0][1]["markets"] == "h2h,totals"
    got = h.sharp_for("fd:1")
    assert got["prices"]["1"] == {"odds": 4.09, "book": "pinnacle"}
    st = h.odds_api_state(NOW)
    assert st["remaining"] == 480 and st["spent_today"] == 2 and "soccer_epl" in st["last_fetch"]
    # A second refresh straight after doesn't fetch again (MIN_GAP).
    sharp_odds.update(h, [_row()], NOW + pd.Timedelta(hours=1))
    assert len(calls) == 1


def test_update_skips_leagues_beyond_todays_allowance(tmp_path, monkeypatch):
    h = HistoryStore(tmp_path / "h.sqlite")
    h.log_odds_api("2026-11-07T07:00:00+00:00", "soccer_fa_cup", 18, 40)  # 58 at start: (58-10)/24 = 2 a day
    calls = []
    monkeypatch.setattr(sharp_odds, "api_key", lambda: "k")
    monkeypatch.setattr(sharp_odds, "_get", _fake_get(calls))
    warnings = sharp_odds.update(h, [_row()], NOW)
    assert calls == [] and "skipped" in warnings[0]


def test_update_without_key_does_nothing(tmp_path):
    assert sharp_odds.update(HistoryStore(tmp_path / "h.sqlite"), [_row()], NOW) == []  # conftest: no key, no network


def test_update_failure_is_a_warning(tmp_path, monkeypatch):
    def boom(path, params):
        raise OSError("down")
    monkeypatch.setattr(sharp_odds, "api_key", lambda: "k")
    monkeypatch.setattr(sharp_odds, "_get", boom)
    warnings = sharp_odds.update(HistoryStore(tmp_path / "h.sqlite"), [_row()], NOW)
    assert warnings and "soccer_epl" in warnings[0]


def test_remaining_credits_forgotten_in_a_new_month(tmp_path):
    h = HistoryStore(tmp_path / "h.sqlite")
    h.log_odds_api("2026-10-31T07:00:00+00:00", "soccer_epl", 2, 12)
    assert h.odds_api_state(pd.Timestamp("2026-11-01T07:00:00+00:00"))["remaining"] is None


# ----------------------------------------------------------- value.evaluate
ODDS = {"1": 2.4, "X": 3.4, "2": 3.0}
MODEL = {"1": 0.60, "X": 0.22, "2": 0.18}


def test_sharp_probability_replaces_the_blend():
    sharp = {"1": 0.40, "X": 0.28, "2": 0.32}
    sel = {s.market: s for s in evaluate(MODEL, ODDS, Settings(alpha=0.5), sharp=sharp)}
    assert sel["1"].basis == "sharp" and sel["1"].p_blend == pytest.approx(0.40)
    assert sel["1"].ev == pytest.approx(0.40 * 2.4 - 1)
    assert sel["1"].p_model == pytest.approx(0.60)  # still reported
    # The model rates the away side far lower, but only the sharp price counts: no bet.
    assert sel["2"].ev == pytest.approx(-0.04) and not sel["2"].recommended and sel["2"].caution == ""
    ou = evaluate({"Over 2.5": 0.5, "Under 2.5": 0.5}, {"Over 2.5": 2.0, "Under 2.5": 1.8}, Settings(), sharp=sharp)
    assert {s.basis for s in ou} == {"blend"}  # no sharp over/under price: the usual blend


def test_big_edge_against_a_sharp_price_is_flagged():
    sel = {s.market: s for s in evaluate(MODEL, ODDS, Settings(alpha=0.5), sharp={"1": 0.47, "X": 0.25, "2": 0.28})}
    assert sel["1"].ev == pytest.approx(0.128) and sel["1"].caution == "amber"  # 12.8% edge: halved
    sel = {s.market: s for s in evaluate(MODEL, ODDS, Settings(alpha=0.5), sharp={"1": 0.52, "X": 0.22, "2": 0.26})}
    assert sel["1"].caution == "red"  # 24.8%: quartered


def test_uefa_nations_league_internationals_are_fetched_friendlies_are_not():
    from ninety_five import sharp_odds
    now = pd.Timestamp("2026-11-12T08:00:00Z")
    soon = "2026-11-13T19:45:00+00:00"
    rows = [{"competition": "International", "detail": "UEFA Nations League", "kickoff": soon, "home": "Denmark", "away": "Spain"},
            {"competition": "International", "detail": "International Friendly", "kickoff": soon, "home": "Norway", "away": "Japan"}]
    assert sharp_odds.due_sports(rows, now, {}) == ["soccer_uefa_nations_league"]
    event = {"home_team": "Denmark", "away_team": "Spain", "commence_time": soon}
    assert [r["home"] for r, _ in sharp_odds.match_events([event], rows, "soccer_uefa_nations_league")] == ["Denmark"]
