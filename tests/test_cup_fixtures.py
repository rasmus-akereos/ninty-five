import pandas as pd
import pytest

from ninety_five import cup_fixtures, intl_fixtures, league_fixtures

NOW = pd.Timestamp("2026-11-05 12:00", tz="UTC")
ENG_MODEL = ["Oxford", "Stevenage", "Leicester", "Sheffield Weds", "Oldham", "Wolves", "Bristol Rvs", "Cambridge"]


def ev(eid, date, home, away, status="STATUS_SCHEDULED", neutral=False):
    return {"id": eid, "date": date, "status": {"type": {"name": status}},
            "competitions": [{"neutralSite": neutral, "competitors": [
                {"homeAway": "home", "team": {"id": "1", "displayName": home}, "score": "0"},
                {"homeAway": "away", "team": {"id": "2", "displayName": away}, "score": "0"}]}]}


def board(name, events):
    return {"leagues": [{"name": name}], "events": events}


LEAGUE_CLUBS = ["Oxford United", "Stevenage", "Leicester City", "Sheffield Wednesday", "Oldham Athletic",
                "Bristol Rovers", "Cambridge United", "Wolverhampton Wanderers", "Brand New FC"]
CUP = board("English FA Cup", [
    ev("1", "2026-11-07T15:00Z", "Oxford United", "Stevenage"),
    ev("2", "2026-11-06T19:45Z", "Oxford City", "Leicester City"),          # non-league: skipped
    ev("3", "2026-11-08T17:15Z", "Oldham Athletic", "Sheffield Wednesday"),  # alias
    ev("4", "2026-11-06T19:30Z", "Bristol Rovers", "Cambridge United", neutral=True),
    ev("5", "2026-11-07T15:00Z", "Brand New FC", "Stevenage"),              # league club the model lacks
    ev("6", "2026-11-30T15:00Z", "Oxford United", "Leicester City"),        # beyond the window
])


@pytest.fixture
def espn(monkeypatch):
    def fake_get(slug, month):
        if slug == "eng.fa":
            return CUP
        if slug == "eng.3":
            return board("League One", [ev("90", "2026-11-01T15:00Z", a, b, "STATUS_FULL_TIME")
                                        for a, b in zip(LEAGUE_CLUBS[::2], LEAGUE_CLUBS[1::2] + ["Stevenage"])])
        return board("x", [])

    monkeypatch.setattr(intl_fixtures, "_get", fake_get)
    monkeypatch.setattr(cup_fixtures, "model_teams", lambda country: ENG_MODEL)


def test_cup_upcoming_keeps_only_matches_between_known_league_clubs(espn):
    df, warnings = cup_fixtures.REAL_UPCOMING(7, now=NOW)
    pairs = {(r.home, r.away) for r in df.itertuples()}
    assert pairs == {("Oxford", "Stevenage"), ("Oldham", "Sheffield Weds"), ("Bristol Rvs", "Cambridge")}
    r = df[df.home == "Oxford"].iloc[0]
    assert r.key == "espn:1" and r.competition == "FA Cup" and r.dataset == "ENG" and not r.neutral
    assert df[df.home == "Bristol Rvs"].iloc[0].neutral  # ESPN's neutral-site flag
    assert warnings == ["FA Cup: no model team for Brand New FC (add it to cup_fixtures.ALIASES)"]


def test_cup_names_never_loosely_match_non_league_clubs(espn):
    df, _ = cup_fixtures.REAL_UPCOMING(7, now=NOW)
    assert "espn:2" not in set(df["key"])  # Oxford City is not Oxford United


def test_cup_results_use_90_minutes():
    aet = ev("7", "2026-11-07T15:00Z", "Oxford United", "Stevenage", "STATUS_FINAL_AET")
    c = aet["competitions"][0]
    c["competitors"][0]["score"], c["competitors"][1]["score"] = "2", "1"
    c["details"] = [{"scoringPlay": True, "clock": {"displayValue": "30'"}, "team": {"id": "1"}},
                    {"scoringPlay": True, "clock": {"displayValue": "60'"}, "team": {"id": "2"}},
                    {"scoringPlay": True, "clock": {"displayValue": "105'"}, "team": {"id": "1"}}]
    assert intl_fixtures.goals_90(aet) == (1, 1)


def test_resolve_reserve_teams_only_match_reserve_teams():
    esp = ["Sociedad", "Sociedad B", "Celta", "Celta B"]
    assert league_fixtures.resolve_name("Real Sociedad II", "SP2", esp) == "Sociedad B"
    assert league_fixtures.resolve_name("Real Sociedad", "SP1", esp) == "Sociedad"
    assert league_fixtures.resolve_name("Celta B", "SP2", esp) == "Celta B"


def test_aliases_cover_the_names_the_matcher_misses():
    a = cup_fixtures.ALIASES
    assert a[("ENG", "Wolverhampton Wanderers")] == "Wolves"
    assert a[("GER", "Borussia Mönchengladbach")] == "M'gladbach"
    assert a[("ESP", "Atlético Madrid")] == "Ath Madrid" and a[("ESP", "RC Celta Fortuna")] == "Celta B"
    assert a[("FRA", "Paris Saint-Germain")] == "Paris SG"


def test_superliga_comes_from_espn(monkeypatch):
    def fake_get(slug, month):
        if slug == "den.1":
            return board("Danish Superliga", [ev("50", "2026-11-08T11:00Z", "Brøndby IF", "F.C. København"),
                                              ev("51", "2026-11-08T13:00Z", "AC Horsens", "AGF")])
        return board("x", [])

    monkeypatch.setattr(intl_fixtures, "_get", fake_get)
    monkeypatch.setattr(cup_fixtures, "model_teams",
                        lambda country: ["Brondby", "FC Copenhagen", "Horsens", "Aarhus"] if country == "DEN" else [])
    df, warnings = cup_fixtures.REAL_UPCOMING(7, now=NOW)
    assert {(r.home, r.away) for r in df.itertuples()} == {("Brondby", "FC Copenhagen"), ("Horsens", "Aarhus")}
    assert set(df["competition"]) == {"Denmark 1"} and set(df["dataset"]) == {"DEN"} and warnings == []
    assert "den.1" in cup_fixtures.ESPN_SLUGS

