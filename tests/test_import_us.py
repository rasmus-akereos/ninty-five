"""Reading NFL / NBA / college football pages from Danske Spil (excerpts of real pages)."""

from ninety_five import import_odds

NFL_TITLE = "New York Giants - Washington Commanders | USA NFL Odds | Spil på kampen her"
NFL = """Fodbold
Amerikansk Fodbold
Basketball
Handicap
Over/Under
Dark mode
Sportsgrene
/
Amerikansk fodbold
/
USA
/
USA NFL

New York Giants

fre 02:15

Washington Commanders

Markeder
Byg væddemål
Populære
Handicap
Over/Under
Kombinationer
Spillelinjer
Kampvinder (Inkl. OT)
Handicap
Over/Under
1
1,80
2
2,05
-1.5
1,87
+1.5
1,91
O
42.5
1,87
U
42.5
1,87
Kampvinder (Inkl. OT)
1
1,80
2
2,05
Scorer touchdown i kampen
Antonio Williams
3,15
Vis mere
Handicap (inkl. OT)
New York Giants
-2.5
1,95
Washington Commanders
+2.5
1,80
-2.0
1,91
+2.0
1,87
+1.0
1,69
-1.0
2,10
Vis mere
Antal point O/U
O
41.5
1,80
U
41.5
1,93
O
42.0
1,83
U
42.0
1,91
Vis mere
Kampvinder + Antal point O/U 41.5 (inkl. OT)
New York Giants og over 41.5
3,25
1. halvleg handicap -0.5
New York Giants
-0.5
1,91
Washington Commanders
+0.5
1,80
Antal point i 1. halvleg O/U 20.5
"""

NBA_TITLE = "Atlanta Hawks - Los Angeles Lakers | USA NBA Odds | Spil på kampen her"
NBA = """Sportsgrene
/
Basketball
/
USA
/
USA NBA

Atlanta Hawks

I morgen 01:00

Los Angeles Lakers

Markeder
Kampvinder (Inkl. OT)
1
1,77
2
2,07
Kampvinder (Eks. OT)
1
1,98
X
13,00
2
2,00
Handicap (Inkl. OT)
Atlanta Hawks
-3.5
2,15
Los Angeles Lakers
+3.5
1,67
+1.5
1,78
-1.5
1,98
Vis mere
Antal point O/U (Inkl. OT)
O
234.5
1,73
U
234.5
2,05
Vis mere
"""


def test_nfl_page_moneyline_spread_ladder_and_totals():
    r = import_odds.parse_us_page(NFL, NFL_TITLE)
    assert r["sport"] == "NFL"
    assert (r["home"], r["away"]) == ("New York Giants", "Washington Commanders")
    assert r["odds"] == {
        "ML 1": 1.80, "ML 2": 2.05,
        "Spread 1 -2.5": 1.95, "Spread 2 +2.5": 1.80,
        "Spread 1 -2": 1.91, "Spread 2 +2": 1.87,  # whole lines named like Pinnacle's ("-2", not "-2.0")
        "Spread 1 +1": 1.69, "Spread 2 -1": 2.10,
        "Over 41.5": 1.80, "Under 41.5": 1.93, "Over 42": 1.83, "Under 42": 1.91,
    }  # no first-half lines, no "Kampvinder + Antal point" combos


def test_nba_page_skips_the_three_way_winner_without_overtime():
    r = import_odds.parse_us_page(NBA, NBA_TITLE)
    assert r["sport"] == "NBA"
    assert r["odds"] == {"ML 1": 1.77, "ML 2": 2.07, "Spread 1 -3.5": 2.15, "Spread 2 +3.5": 1.67,
                         "Spread 1 +1.5": 1.78, "Spread 2 -1.5": 1.98, "Over 234.5": 1.73, "Under 234.5": 2.05}


def test_college_football_and_football_pages():
    ncaa = NFL.replace("USA NFL", "USA NCAA")
    assert import_odds.parse_us_page(ncaa, "Ohio State - Michigan | USA NCAA Odds")["sport"] == "NCAAF"
    football = "Sportsgrene\n/\nFodbold\n/\nFrankrig\n/\nLigue 1\nKampvinder\n1\n2,70\nX\n3,40\n2\n2,60"
    assert import_odds.parse_us_page(football, "Lens - Marseille | Ligue 1 Odds")["sport"] is None


def test_us_game_found_either_way_round():
    games = [{"key": "us:a", "home": "New York Giants", "away": "Washington Commanders", "kickoff": "2099-11-13T01:15:00+00:00"},
             {"key": "us:b", "home": "New York Jets", "away": "Buffalo Bills", "kickoff": "2099-11-15T18:00:00+00:00"}]
    r = import_odds.parse_us_page(NFL, NFL_TITLE)
    g, odds = import_odds.find_us_game(r, games)
    assert g["key"] == "us:a" and odds == r["odds"]
    # Danske Spil lists the teams the other way round: 1 and 2 swap.
    swapped = [{**games[0], "home": "Washington Commanders", "away": "New York Giants"}]
    g, odds = import_odds.find_us_game(r, swapped)
    assert g["key"] == "us:a"
    assert odds["ML 2"] == 1.80 and odds["Spread 2 -2.5"] == 1.95 and odds["Spread 1 +2.5"] == 1.80
    assert odds["Over 41.5"] == 1.80
    assert import_odds.find_us_game(r, games[1:]) == (None, {})


def test_college_short_names_match_the_odds_api_names():
    r = {"home": "Ohio State", "away": "Michigan", "odds": {"ML 1": 1.3}}
    games = [{"key": "us:c", "home": "Ohio State Buckeyes", "away": "Michigan Wolverines", "kickoff": "2099-11-28T17:00:00+00:00"},
             {"key": "us:d", "home": "Michigan State Spartans", "away": "Penn State Nittany Lions", "kickoff": "2099-11-28T17:00:00+00:00"}]
    g, odds = import_odds.find_us_game(r, games)
    assert g["key"] == "us:c" and odds == {"ML 1": 1.3}
