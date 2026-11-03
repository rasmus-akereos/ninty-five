import json
from pathlib import Path

import pandas as pd
import pytest

from ninty_five import intl_fixtures, service

SAMPLE = json.loads((Path(__file__).parent / "data" / "espn_scoreboard_sample.json").read_text())


def test_parse_scoreboard_sample():
    df = intl_fixtures.parse_scoreboard(SAMPLE)
    israel = df[df.home == "Israel"].iloc[0]
    assert israel.away == "Austria" and israel.league == "UEFA Nations League"
    assert israel.neutral  # home game played in Debrecen, Hungary
    assert israel.status == "STATUS_SCHEDULED"
    assert israel.kickoff == pd.Timestamp("2026-11-17T19:45Z")
    baku = df[df.home == "Azerbaijan"].iloc[0]
    assert not baku.neutral


@pytest.mark.parametrize(
    "home, country, neutral",
    [
        ("United States", "USA", False),
        ("Haiti", "USA", True),
        ("Turkey", "Türkiye", False),
        ("DR Congo", "Congo", False),
        ("Wales", "United Kingdom", False),
        ("Japan", "Singapore", True),
        ("Japan", None, False),
    ],
)
def test_is_neutral(home, country, neutral):
    assert intl_fixtures.is_neutral(home, country) is neutral


def test_team_names_mapped_to_intl_spelling():
    payload = {
        "leagues": [{"name": "International Friendly"}],
        "events": [{
            "id": "1", "date": "2026-11-14T18:00Z", "status": {"type": {"name": "STATUS_SCHEDULED"}},
            "competitions": [{"competitors": [
                {"homeAway": "home", "team": {"displayName": "Türkiye"}},
                {"homeAway": "away", "team": {"displayName": "Bosnia-Herzegovina"}}],
                "venue": {"address": {"country": "Türkiye"}}}],
        }],
    }
    r = intl_fixtures.parse_scoreboard(payload).iloc[0]
    assert (r.home, r.away, r.neutral) == ("Turkey", "Bosnia and Herzegovina", False)


def test_upcoming_filters_window_and_survives_failing_competition(monkeypatch):
    def fake_get(slug, month):
        if slug == "uefa.nations":
            return SAMPLE
        raise OSError("boom")

    monkeypatch.setattr(intl_fixtures, "_get", fake_get)
    df = intl_fixtures.upcoming(days=30, now=pd.Timestamp("2026-11-03T12:00Z"))
    assert list(df.home) == ["Israel"]  # the finished October match is dropped
    monkeypatch.setattr(intl_fixtures, "_get", lambda slug, month: (_ for _ in ()).throw(OSError("down")))
    with pytest.raises(RuntimeError):
        intl_fixtures.upcoming(days=7)


def test_board_keeps_clubs_when_intl_source_fails(monkeypatch):
    monkeypatch.setattr(service, "club_board", lambda days, min_edge: [{"kickoff": "2026-11-04T20:00:00+00:00"}])

    def broken(days, min_edge):
        raise RuntimeError("ESPN down")

    monkeypatch.setattr(service, "intl_board", broken)
    rows, warnings = service.board()
    assert len(rows) == 1 and "ESPN down" in warnings[0]
