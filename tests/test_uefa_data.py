import pandas as pd
import pytest

from ninty_five import data, uefa

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
