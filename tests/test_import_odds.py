import pytest
from pathlib import Path

from ninety_five import import_odds

TEXT = (Path(__file__).parent / "data" / "danskespil_blackburn_stoke.txt").read_text()
TITLE = "Blackburn - Stoke | England Championship Odds | Spil på kampen her"


def test_parse_danskespil_event_page():
    p = import_odds.parse_page(TEXT, TITLE)
    assert (p["home"], p["away"]) == ("Blackburn", "Stoke")
    assert p["odds"] == {
        "1": 2.70, "X": 3.40, "2": 2.60,
        "BTTS Yes": 1.77, "BTTS No": 1.95,
        "Over 0.5": 1.04, "Under 0.5": 8.00, "Over 1.5": 1.31, "Under 1.5": 3.15,
        "Over 2.5": 1.98, "Under 2.5": 1.77, "Over 3.5": 3.40, "Under 3.5": 1.29,
        "Over 4.5": 6.25, "Under 4.5": 1.08,
    }


def test_parse_page_without_markets():
    p = import_odds.parse_page("Fodbold\nNoget andet", "Danske Spil")
    assert p["odds"] == {} and p["home"] is None


def test_team_names_match_board_names():
    m = import_odds.same_team
    assert m("Blackburn", "Blackburn Rovers FC") and m("Stoke", "Stoke City FC")
    assert m("Man United", "Manchester United FC") and m("Atl. Madrid", "Club Atlético de Madrid")
    assert m("Bayern München", "FC Bayern München") and m("PSV", "PSV") and m("Paris SG", "Paris Saint-Germain FC")
    assert m("Danmark", "Denmark") and m("Tyrkiet", "Turkey")
    assert not m("Stoke", "Blackburn Rovers FC") and not m("Man City", "Manchester United FC")


def test_find_match_on_board():
    board = [{"key": "fd:1", "home": "Portsmouth FC", "away": "Swansea City AFC"},
             {"key": "fd:2", "home": "Blackburn Rovers FC", "away": "Stoke City FC"}]
    assert import_odds.find_match("Blackburn", "Stoke", board)["key"] == "fd:2"
    assert import_odds.find_match("Stoke", "Blackburn", board) is None  # home/away must agree
    assert import_odds.find_match("Leeds", "Hull", board) is None


def test_spelling_variants_match():
    m = import_odds.same_team
    assert m("Shaktar Donetsk", "FK Shakhtar Donetsk")
    assert not m("Shaktar Donetsk", "Dynamo Kyiv")


def test_one_team_plus_a_shared_word_is_enough():
    board = [{"key": "fd:575377", "home": "FK Shakhtar Donetsk", "away": "Sporting Clube de Portugal"},
             {"key": "fd:567350", "home": "Sporting Clube de Portugal", "away": "Moreirense FC"}]
    hit = import_odds.find_match("FC Shakhtar Donetsk", "Sporting Lissabon", board)
    assert hit["key"] == "fd:575377"
    # one side alone, with nothing in common on the other side, is not enough
    assert import_odds.find_match("FC Shakhtar Donetsk", "Benfica", board) is None


def test_common_words_are_not_enough():
    board = [{"key": "fd:1", "home": "Birmingham City FC", "away": "Swansea City AFC"},
             {"key": "fd:2", "home": "Manchester United FC", "away": "Aston Villa FC"}]
    assert import_odds.find_match("Cardiff City", "Swansea", board) is None
    assert import_odds.find_match("United Arab Emirates", "Aston Villa", board) is None


def test_near_spelling_needs_same_first_letter():
    assert not import_odds.same_team("Angers", "Queens Park Rangers FC")


def test_same_teams_twice_picks_the_next_match():
    board = [{"key": "espn:2", "home": "New Zealand", "away": "India", "kickoff": "2026-11-15T07:00:00+00:00"},
             {"key": "espn:1", "home": "New Zealand", "away": "India", "kickoff": "2026-11-12T07:00:00+00:00"}]
    assert import_odds.find_match("New Zealand", "Indien", board)["key"] == "espn:1"


def test_danish_club_names_match_the_board():
    from ninety_five.import_odds import same_team
    assert same_team("FC København", "FC Copenhagen")
    assert same_team("AGF", "Aarhus")
    assert same_team("Brøndby", "Brondby") and same_team("Sønderjyske", "Sonderjyske")


def test_danske_spil_names_match_european_board_names():
    """Europa/Conference League rows use football-data.co.uk's short names
    ("Betis", "Gent") or ESPN's ("Red Star Belgrade") for European-only clubs."""
    pairs = [("Real Betis", "Betis"), ("Real Sociedad", "Sociedad"), ("KAA Gent", "Gent"),
             ("NEC Nijmegen", "Nijmegen"), ("Legia Warszawa", "Legia"), ("Rapid Wien", "SK Rapid"),
             ("Sporting Lissabon", "Sp Lisbon"), ("Sporting CP", "Sp Lisbon"), ("Eintracht Frankfurt", "Ein Frankfurt"),
             ("Athletic Bilbao", "Ath Bilbao"), ("Union Saint-Gilloise", "St. Gilloise"),
             ("Crvena Zvezda", "Red Star Belgrade"), ("Istanbul Basaksehir", "Buyuksehyr"),
             ("Nottingham Forest", "Nott'm Forest"), ("Sparta Prag", "Sparta Prague"), ("FC Midtjylland", "Midtjylland")]
    misses = [(ds, b) for ds, b in pairs if not import_odds.find_match(ds, "Celtic", [
        {"home": b, "away": "Celtic", "kickoff": "2026-11-05T20:00:00+00:00"}])]
    assert misses == []


def test_short_board_name_inside_longer_name_still_needs_both_teams():
    board = [{"home": "Gent", "away": "Ajax", "kickoff": "1"}, {"home": "Genk", "away": "PSV", "kickoff": "1"}]
    assert import_odds.find_match("KAA Gent", "Ajax Amsterdam", board)["home"] == "Gent"


def test_danish_name_for_red_star():
    assert import_odds.same_team("Røde Stjerne", "Red Star Belgrade")


def test_ob_is_odense():
    assert import_odds.same_team("OB", "Odense")


# Danske Spil names (left) for board names (right) found missing in a sweep of
# every board team.
DS_NAMES = [
    ("FCK", "FC Copenhagen"), ("FCM", "Midtjylland"), ("AaB", "Aalborg"),
    ("Spurs", "Tottenham Hotspur FC"), ("Wolves", "Wolverhampton Wanderers FC"),
    ("Man Utd", "Manchester United FC"), ("Sheffield Utd", "Sheffield United FC"), ("Sheff Utd", "Sheffield United FC"),
    ("Nottm Forest", "Nottingham Forest FC"), ("Sheffield Wednesday", "Sheffield Weds"), ("Bristol Rovers", "Bristol Rvs"),
    ("BVB", "Borussia Dortmund"), ("Gladbach", "Borussia Mönchengladbach"), ("HSV", "Hamburger SV"),
    ("RC Lens", "Racing Club de Lens"), ("Rennes", "Stade Rennais FC 1901"),
    ("Sporting Lissabon", "Sporting Clube de Portugal"), ("Sporting CP", "Sporting Clube de Portugal"),
    ("Slavia Prag", "SK Slavia Praha"), ("Sparta Prag", "Sparta Prague"), ("Sparta Praha", "Sparta Prague"),
    ("AEK Athen", "PAE AEK"), ("Sint-Truiden", "St Truiden"), ("Hapoel Beer Sheva", "Hapoel Be'er"),
    ("OFI Kreta", "OFI Crete"), ("Union SG", "St. Gilloise"), ("Royale Union SG", "St. Gilloise"),
    ("Bosnien og Hercegovina", "Bosnia and Herzegovina"), ("Forenede Arabiske Emirater", "United Arab Emirates"),
    ("Kap Verde", "Cape Verde"), ("Ækvatorialguinea", "Equatorial Guinea"),
    ("Den Centralafrikanske Republik", "Central African Republic"), ("Etiopien", "Ethiopia"), ("Libyen", "Libya"),
    ("Mauretanien", "Mauritania"), ("Sydsudan", "South Sudan"), ("Comorerne", "Comoros"), ("Kirgisistan", "Kyrgyzstan"),
    ("Syrien", "Syria"), ("Kinesisk Taipei", "Taiwan"), ("Trinidad og Tobago", "Trinidad and Tobago"),
    ("Antigua og Barbuda", "Antigua and Barbuda"), ("Saint Kitts og Nevis", "Saint Kitts and Nevis"),
    ("Saint Vincent og Grenadinerne", "Saint Vincent and the Grenadines"), ("Fransk Guyana", "French Guiana"),
    ("Caymanøerne", "Cayman Islands"), ("Kamerun", "Cameroon"), ("Den Dominikanske Republik", "Dominican Republic"),
    ("Moldavien", "Moldova"), ("Swaziland", "Eswatini"), ("Usbekistan", "Uzbekistan"), ("Indonesien", "Indonesia"),
    ("Madagaskar", "Madagascar"), ("Palæstina", "Palestine"), ("Surinam", "Suriname"), ("Libanon", "Lebanon"),
    ("Nott'm Forest", "Nottingham Forest FC"), ("Hapoel Be'er Sheva", "Hapoel Be'er"),
]


@pytest.mark.parametrize("ds,board", DS_NAMES)
def test_danske_spil_name_matches_board(ds, board):
    assert import_odds._same(ds, board)


def test_sporting_lisbon_alias_leaves_portugal_alone():
    assert not import_odds._same("Portugal", "Sporting Clube de Portugal")
    assert import_odds._same("Portugal", "Portugal")


@pytest.mark.parametrize("home,away,want", [
    ("Barcelona", "Sevilla", ("FC Barcelona", "Sevilla FC")),
    ("Milan", "Lazio", ("AC Milan", "SS Lazio")),
    ("Island", "Skotland", ("Iceland", "Scotland")),
    ("Østrig", "Wales", ("Austria", "Wales")),
    ("Bayern", "Wales", ("FC Bayern München", "Wales")),
    ("Paris SG", "Wales", ("Paris Saint-Germain FC", "Wales")),
    ("Inter Milan", "Wales", ("FC Internazionale Milano", "Wales")),
    ("Celta Vigo B", "Wales", ("Celta B", "Wales")),
])
def test_closest_names_win_when_two_matches_fit(home, away, want):
    board = [{"home": "FC Barcelona", "away": "Sevilla FC"}, {"home": "RCD Espanyol de Barcelona", "away": "Sevilla FC"},
             {"home": "AC Milan", "away": "SS Lazio"}, {"home": "FC Internazionale Milano", "away": "SS Lazio"},
             {"home": "Iceland", "away": "Scotland"}, {"home": "Republic of Ireland", "away": "Scotland"},
             {"home": "Austria", "away": "Wales"}, {"home": "Australia", "away": "Wales"},
             {"home": "FC Bayern München", "away": "Wales"}, {"home": "Bayer 04 Leverkusen", "away": "Wales"},
             {"home": "Paris Saint-Germain FC", "away": "Wales"}, {"home": "Paris FC", "away": "Wales"},
             {"home": "FC Internazionale Milano", "away": "Wales"}, {"home": "AC Milan", "away": "Wales"},
             {"home": "Celta B", "away": "Wales"}, {"home": "Ceuta", "away": "Wales"}]
    m = import_odds.find_match(home, away, board)
    assert m is not None and (m["home"], m["away"]) == want


def test_still_ambiguous_without_a_closest_name():
    board = [{"home": "Sporting Clube de Portugal", "away": "Rio Ave FC"}, {"home": "Sporting Clube de Braga", "away": "Rio Ave FC"}]
    assert import_odds.find_match("Sporting", "Rio Ave", board) is None
