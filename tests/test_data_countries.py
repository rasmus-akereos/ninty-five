import pytest

from ninety_five import data

MAIN_HEADER = "Div,Date,HomeTeam,AwayTeam,FTHG,FTAG\n"


def _main(div, rows):
    return (MAIN_HEADER + "".join(f"{div},{d},{h},{a},{hg},{ag}\n" for d, h, a, hg, ag in rows)).encode()


FILES = {
    "D1_2627.csv": _main("D1", [("20/09/2026", "Promoted FC", "Bayern", 0, 3), ("27/09/2026", "Bayern", "Dortmund", 2, 2)]),
    "D2_2526.csv": _main("D2", [("20/09/2025", "Promoted FC", "Paderborn", 2, 0)]),
    "D2_2627.csv": _main("D2", [("20/09/2026", "Paderborn", "Hertha", 1, 1)]),
    "DNK.csv": ("﻿Country,League,Season,Date,Time,Home,Away,HG,AG,Res\n"
                "Denmark,Superliga,2026/2027,01/11/2026,17:00,Brondby,Aarhus,2,0,H\n"
                "Denmark,Superliga,2026/2027,02/11/2026,18:00,Sonderjyske,FC Copenhagen,0,2,A\n").encode("utf-8"),
}


@pytest.fixture
def fake_csvs(monkeypatch):
    calls = []

    def fake(name, url, refresh):
        calls.append((name, refresh))
        if name not in FILES:
            raise OSError("404")
        return FILES[name]

    monkeypatch.setattr(data, "_cached", fake)
    return calls


def test_load_country_concatenates_divisions_with_latest_group(fake_csvs):
    df, groups = data.load_country("GER")
    assert set(df["competition"]) == {"Germany 1", "Germany 2"}
    assert len(df) == 4
    assert groups["Promoted FC"] == "Germany 1"  # played D2 last season, D1 this season
    assert groups["Hertha"] == "Germany 2"


def test_load_country_den_reads_extra_format_without_groups(fake_csvs):
    df, groups = data.load_country("DEN")
    assert set(df["competition"]) == {"Denmark 1"} and groups == {}
    assert list(df["home"]) == ["Brondby", "Sonderjyske"]


def test_refresh_current_downloads_only_newest_season(fake_csvs):
    data.load_club_league("D2", refresh_current=True)
    assert [n for n, r in fake_csvs if r] == ["D2_2627.csv"]


def test_refresh_current_extra_league_redownloads_file(fake_csvs):
    data.load_club_league("DNK", refresh_current=True)
    assert [n for n, r in fake_csvs if r] == ["DNK.csv"]


def test_new_divisions_known():
    assert {"E2", "E3", "SP2", "I2", "F2"} <= set(data.FD_MAIN_LEAGUES)
    assert data.DIVISION_COUNTRY["DNK"] == "DEN" and data.DIVISION_COUNTRY["SP2"] == "ESP"
    assert data.COUNTRIES["ENG"] == {"E0": "England 1", "E1": "England 2", "E2": "England 3", "E3": "England 4"}
