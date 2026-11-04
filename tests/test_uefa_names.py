import pytest
import pandas as pd

from ninty_five import intl_fixtures, uefa

TEAMS = {
    "BEL": ["Club Brugge", "Cercle Brugge", "Anderlecht"],
    "SCO": ["Celtic", "Rangers", "Hearts"],
    "ROU": ["Dinamo Bucuresti", "FCSB"],
    "NED": ["Twente", "Ajax"],
    "ENG": ["Twente FC"],  # contrived namesake for the ambiguity test
}
LEAGUE_CLUBS = {"BEL": {"Club Brugge", "Cercle Brugge", "RSC Anderlecht"},
                "SCO": {"Celtic", "Rangers", "Heart of Midlothian", "Hibernian"}}


def test_club_in_espn_league_resolves_within_country():
    r = uefa.resolve_clubs(["Club Brugge"], TEAMS, LEAGUE_CLUBS)
    assert r.mapping == {"Club Brugge": "Club Brugge"} and not r.warnings


def test_ambiguous_across_countries_is_excluded():
    r = uefa.resolve_clubs(["Twente"], TEAMS, LEAGUE_CLUBS)
    assert "Twente" in r.excluded and "Twente" not in r.mapping
    assert r.warnings == ["UEFA: no domestic team for Twente (add it to uefa.ALIASES)"]


def test_league_club_without_match_is_excluded_not_european_only():
    r = uefa.resolve_clubs(["Hibernian"], TEAMS, LEAGUE_CLUBS)
    assert r.excluded == {"Hibernian"} and not r.european_only and not r.mapping


def test_unknown_country_club_is_european_only():
    r = uefa.resolve_clubs(["Dinamo Zagreb"], TEAMS, LEAGUE_CLUBS)
    assert r.european_only == {"Dinamo Zagreb"} and r.mapping == {"Dinamo Zagreb": "Dinamo Zagreb"}


def test_european_only_namesake_does_not_merge():
    r = uefa.resolve_clubs(["Dinamo Zagreb", "Dinamo"], TEAMS, LEAGUE_CLUBS)
    assert r.mapping["Dinamo Zagreb"] == "Dinamo Zagreb"
    assert "Dinamo" not in r.mapping or r.mapping["Dinamo"] != "Dinamo Bucuresti"


def test_alias_wins(monkeypatch):
    monkeypatch.setitem(uefa.ALIASES, ("SCO", "Hibernian"), "Hibs")
    r = uefa.resolve_clubs(["Hibernian"], TEAMS, LEAGUE_CLUBS)
    assert r.mapping == {"Hibernian": "Hibs"} and not r.excluded


def test_club_outside_espn_leagues_resolves_by_unique_team_list():
    r = uefa.resolve_clubs(["FCSB"], TEAMS, LEAGUE_CLUBS)
    assert r.mapping == {"FCSB": "FCSB"} and not r.european_only


def test_espn_league_clubs_skips_failing_slug(monkeypatch):
    def fake_get(slug, month):
        if slug == "sco.1":
            raise OSError("down")
        if slug == "bel.1":
            return {"events": [{"id": "1", "date": "2026-11-01T15:00Z", "status": {"type": {"name": "STATUS_FULL_TIME"}},
                                "competitions": [{"competitors": [
                                    {"homeAway": "home", "team": {"displayName": "Club Brugge"}},
                                    {"homeAway": "away", "team": {"displayName": "Gent"}}]}]}]}
        return {"events": []}

    monkeypatch.setattr(intl_fixtures, "_get", fake_get)
    clubs = uefa.espn_league_clubs(pd.Timestamp("2026-11-04", tz="UTC"))
    assert clubs["BEL"] == {"Club Brugge", "Gent"} and "SCO" not in clubs


# Wrong links the live check (2026-11-04) found with loose matching.
LIVE_TEAMS = {"GRE": ["AEK", "Aris", "Olympiakos"], "POL": ["Cracovia", "Legia"], "AUT": ["Ried", "LASK"],
              "DEN": ["Vejle"], "NOR": ["Viking"], "ROU": ["U. Cluj", "Univ. Craiova", "U Craiova 1948"], "FIN": ["Inter Turku"],
              "ITA": ["Inter"]}
LIVE_CLUBS = {"GRE": {"AEK Athens", "Olympiacos"}, "AUT": {"LASK Linz"}, "DEN": {"Vejle Boldklub"},
              "NOR": {"Viking FK"}, "ITA": {"Internazionale"}}


@pytest.mark.parametrize("name", ["AEK Larnaca", "Aris Limassol", "Lincoln Red Imps", "Velez Mostar",
                                  "Víkingur", "Vikingur Reykjavik", "Inter D'Escaldes"])
def test_foreign_clubs_never_loosely_join_a_covered_team(name):
    r = uefa.resolve_clubs([name], LIVE_TEAMS, LIVE_CLUBS)
    assert r.mapping.get(name) in (None, name), r.mapping


def test_clubs_outside_espn_leagues_match_strictly():
    r = uefa.resolve_clubs(["FC Inter Turku", "Legia Warsaw"], LIVE_TEAMS, LIVE_CLUBS)
    assert r.mapping["FC Inter Turku"] == "Inter Turku"  # every word equal
    assert r.mapping["Legia Warsaw"] == "Legia"           # the team's words all in the ESPN name


def test_romanian_craiova_never_becomes_polish_cracovia():
    assert uefa.resolve_clubs(["CSU Craiova"], LIVE_TEAMS, LIVE_CLUBS).mapping.get("CSU Craiova") != "Cracovia"
