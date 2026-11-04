import pandas as pd
import pytest

from ninety_five import data, uefa

MAIN_HEADER = "Div,Date,HomeTeam,AwayTeam,FTHG,FTAG\n"
EXTRA_HEADER = "Country,League,Season,Date,Time,Home,Away,HG,AG,Res\n"


def _main(div, rows):
    return (MAIN_HEADER + "".join(f"{div},{d},{h},{a},{hg},{ag}\n" for d, h, a, hg, ag in rows)).encode()


def _extra(rows):
    return (EXTRA_HEADER + "".join(f"Norway,Eliteserien,2025,{d},18:00,{h},{a},{hg},{ag},H\n"
                                   for d, h, a, hg, ag in rows)).encode()


FILES = {
    "B1_2526.csv": _main("B1", [("13/09/2025", "Club Brugge", "Gent", 2, 1)]),
    "B1_2627.csv": _main("B1", [("20/09/2026", "Club Brugge", "Anderlecht", 1, 1)]),
    "N1_2526.csv": _main("N1", [("21/09/2025", "Ajax", "Twente", 3, 0)]),
    "N1_2627.csv": _main("N1", [("21/09/2026", "Ajax", "Gent", 0, 0)]),  # Gent "moved" league later
    "NOR.csv": _extra([("12/10/2025", "Brann", "Molde", 2, 2), ("01/03/2019", "Old FC", "Molde", 1, 0)]),
}


@pytest.fixture
def fake_files(monkeypatch):
    def fake(name, url, refresh):
        if name not in FILES:
            raise OSError("404")
        return FILES[name]

    monkeypatch.setattr(data, "_cached", fake)
    monkeypatch.setattr(uefa, "UEFA_LEAGUES", {"B1": ("BEL", "Belgium 1"), "N1": ("NED", "Netherlands 1"),
                                               "NOR": ("NOR", "Norway 1")})


def test_uefa_leagues_cover_twenty_top_flights():
    assert len(uefa.UEFA_LEAGUES) == 20
    assert uefa.UEFA_LEAGUES["SC0"] == ("SCO", "Scotland 1") and uefa.UEFA_LEAGUES["SWZ"] == ("SUI", "Switzerland 1")


def test_load_domestic_labels_and_groups(fake_files):
    df, groups, _ = uefa.load_domestic(now=pd.Timestamp("2026-11-04"))
    assert set(df["competition"]) == {"Belgium 1", "Netherlands 1", "Norway 1"}
    assert groups["Gent"] == "Netherlands 1"  # league played most recently
    assert groups["Club Brugge"] == "Belgium 1"


def test_load_domestic_calendar_year_league_in_winter(fake_files):
    df, _, _ = uefa.load_domestic(now=pd.Timestamp("2026-01-15"))
    nor = df[df["competition"] == "Norway 1"]
    assert list(nor["home"]) == ["Brann"]  # last autumn kept, 2019 outside the window


def test_load_domestic_skips_failing_league(fake_files, monkeypatch):
    real = data._cached

    def failing(name, url, refresh):
        if name.startswith("N1"):
            raise OSError("down")
        return real(name, url, refresh)

    monkeypatch.setattr(data, "_cached", failing)
    df, _, _ = uefa.load_domestic(now=pd.Timestamp("2026-11-04"))
    assert set(df["competition"]) == {"Belgium 1", "Norway 1"}


def test_load_domestic_raises_when_everything_fails(monkeypatch):
    monkeypatch.setattr(data, "_cached", lambda name, url, refresh: (_ for _ in ()).throw(OSError("down")))
    with pytest.raises(RuntimeError):
        uefa.load_domestic(now=pd.Timestamp("2026-11-04"))


def test_country_teams_lists_recent_teams(fake_files):
    _, _, teams = uefa.load_domestic(now=pd.Timestamp("2026-11-04"))
    assert teams["NOR"] == ["Brann", "Molde"]
    assert teams["BEL"] == ["Anderlecht", "Club Brugge"]  # last year of data only


# ---------------------------------------------------------------- European results
from ninety_five import intl_fixtures  # noqa: E402


def ev(eid, date, home, away, status="STATUS_FULL_TIME", hs="1", as_="0", details=None, neutral=False):
    comp = {"neutralSite": neutral, "competitors": [
        {"homeAway": "home", "team": {"id": "1", "displayName": home}, "score": hs},
        {"homeAway": "away", "team": {"id": "2", "displayName": away}, "score": as_}]}
    if details is not None:
        comp["details"] = details
    return {"id": eid, "date": date, "status": {"type": {"name": status}}, "competitions": [comp]}


def goal(team, minute):
    return {"scoringPlay": True, "team": {"id": team}, "clock": {"displayValue": minute}}


def sb(events):
    return {"leagues": [{"name": "x"}], "events": events}


EVENTS = {
    ("uefa.europa", "202610"): sb([
        ev("10", "2026-10-02T19:00Z", "Celtic", "Besiktas", hs="2", as_="1"),
        ev("11", "2026-10-02T19:00Z", "Ajax", "Lyon", status="STATUS_SCHEDULED"),
        ev("12", "2026-10-02T19:00Z", "Roma", "Porto", status="STATUS_POSTPONED"),
    ]),
    ("uefa.europa.conf_qual", "202608"): sb([ev("20", "2026-08-07T18:00Z", "Brann", "Riga", hs="3", as_="0")]),
    ("uefa.europa", "202603"): sb([ev("30", "2026-03-12T20:00Z", "Celtic", "Sparta Prague", status="STATUS_FINAL_AET",
                                      hs="2", as_="1", details=[goal("1", "30'"), goal("2", "80'"), goal("1", "101'")])]),
}


@pytest.fixture
def espn(monkeypatch, tmp_path):
    calls = []

    def fake_get(slug, month):
        calls.append((slug, month))
        return EVENTS.get((slug, month), sb([]))

    monkeypatch.setattr(intl_fixtures, "_get", fake_get)
    monkeypatch.setattr(uefa, "ESPN_CACHE", tmp_path)
    return calls


START, END = pd.Timestamp("2026-03-01", tz="UTC"), pd.Timestamp("2026-11-04", tz="UTC")


def test_european_results_use_90_minutes(espn):
    df, _ = uefa.european_results(START, END)
    r = df[df["event_id"] == "30"].iloc[0]
    assert (r.hg, r.ag) == (1, 1)  # 2-1 after extra time, 1-1 at 90 minutes


def test_european_results_label_and_qualifier(espn):
    df, _ = uefa.european_results(START, END)
    q = df[df["event_id"] == "20"].iloc[0]
    assert q.competition == "Conference League qualifying" and q.qualifier
    e = df[df["event_id"] == "10"].iloc[0]
    assert e.competition == "Europa League" and not e.qualifier
    assert (e.home, e.away, e.hg, e.ag) == ("Celtic", "Besiktas", 2, 1)
    assert list(df.columns) == data.COLUMNS + ["event_id", "qualifier"]


def test_unfinished_events_are_dropped(espn):
    df, _ = uefa.european_results(START, END)
    assert set(df["event_id"]) == {"10", "20", "30"}


def test_past_months_read_from_disk(espn):
    uefa.european_results(START, END)
    n = len(espn)
    uefa.european_results(START, END)
    again = espn[n:]
    assert again and all(month == "202611" for _, month in again)  # only the current month is re-read


def test_current_month_refreshed_on_request(espn):
    uefa.european_results(START, END)
    n = len(espn)
    uefa.european_results(START, END, refresh_current=True)
    assert {m for _, m in espn[n:]} == {"202611"}


def test_failed_month_is_a_warning(espn, monkeypatch):
    real = intl_fixtures._get

    def flaky(slug, month):
        if (slug, month) == ("uefa.champions", "202604"):
            raise OSError("timeout")
        return real(slug, month)

    monkeypatch.setattr(intl_fixtures, "_get", flaky)
    df, warnings = uefa.european_results(START, END)
    assert "10" in set(df["event_id"])
    assert warnings == ["UEFA: could not load uefa.champions 202604: timeout"]


# ---------------------------------------------------------------- the data set
def test_load_uefa_links_european_and_domestic(monkeypatch):
    dom = pd.DataFrame([(pd.Timestamp("2026-09-20"), "Celtic", "Hearts", 2, 0, False, "Scotland 1"),
                        (pd.Timestamp("2026-09-21"), "Besiktas", "Galatasaray", 1, 1, False, "Turkey 1")],
                       columns=data.COLUMNS)
    monkeypatch.setattr(uefa, "load_domestic", lambda **kw: (
        dom, {"Celtic": "Scotland 1", "Hearts": "Scotland 1", "Besiktas": "Turkey 1", "Galatasaray": "Turkey 1"},
        {"SCO": ["Celtic", "Hearts"], "TUR": ["Besiktas", "Galatasaray"]}))
    eu = pd.DataFrame([(pd.Timestamp("2026-10-02"), "Celtic", "Beşiktaş JK", 2, 1, False, "Europa League", "1", False),
                       (pd.Timestamp("2026-10-02"), "Sparta Prague", "Celtic", 0, 0, False, "Europa League", "2", False),
                       (pd.Timestamp("2026-10-03"), "Hibernian", "Sparta Prague", 1, 3, False, "Europa League", "3", False)],
                      columns=data.COLUMNS + ["event_id", "qualifier"])
    monkeypatch.setattr(uefa, "european_results", lambda start, end, refresh_current=False: (eu, []))
    monkeypatch.setattr(uefa, "espn_league_clubs", lambda now: {"SCO": {"Celtic", "Hibernian"}})
    monkeypatch.setitem(uefa.ALIASES, ("TUR", "Beşiktaş JK"), "Besiktas")
    df, groups, res = uefa.load_uefa(now=pd.Timestamp("2026-11-04", tz="UTC"))
    europe = df[df["competition"] == "Europa League"]
    assert set(zip(europe.home, europe.away)) == {("Celtic", "Besiktas"), ("Sparta Prague", "Celtic")}
    assert groups["Sparta Prague"] == uefa.EUROPEAN_ONLY and groups["Celtic"] == "Scotland 1"
    assert res.excluded == {"Hibernian"} and uefa.last_resolution is res
    assert list(df.columns) == data.COLUMNS


def test_current_month_failure_raises_so_the_old_model_is_kept(espn, monkeypatch):
    real = intl_fixtures._get

    def flaky(slug, month):
        if month == "202611":
            raise OSError("timeout")
        return real(slug, month)

    monkeypatch.setattr(intl_fixtures, "_get", flaky)
    with pytest.raises(RuntimeError, match="202611"):
        uefa.european_results(START, END)
