import pandas as pd
import pytest

from ninty_five import service


def _df(rows):
    return pd.DataFrame(rows, columns=["date", "home", "away", "hg", "ag", "neutral", "competition"])


def test_form_last_five_before_kickoff_most_recent_first():
    df = _df([(pd.Timestamp(f"2026-10-{d:02d}"), "A", "B", 2, 1, False, "L") for d in range(1, 8)]
             + [(pd.Timestamp("2026-10-20"), "C", "A", 0, 0, True, "L"),
                (pd.Timestamp("2026-11-05"), "A", "D", 9, 0, False, "L")])  # after kick-off: excluded
    f = service.form(df, "A", before=pd.Timestamp("2026-11-01T20:00Z"))
    assert len(f) == 5
    assert f[0] == {"date": "2026-10-20", "opponent": "C", "venue": "N", "gf": 0, "ga": 0, "result": "D"}
    assert f[1] == {"date": "2026-10-07", "opponent": "B", "venue": "H", "gf": 2, "ga": 1, "result": "W"}
    g = service.form(df, "B", before=pd.Timestamp("2026-11-01T20:00Z"))
    assert g[0]["venue"] == "A" and g[0]["result"] == "L" and (g[0]["gf"], g[0]["ga"]) == (1, 2)


def test_form_unknown_team_is_empty():
    assert service.form(_df([]), "Nobody", before=pd.Timestamp("2026-11-01T00:00Z")) == []


def test_with_min_odds():
    rows = service.with_min_odds([{"rated": True, "probabilities": {m: 0.5 for m in service.BOARD_MARKETS}},
                                  {"rated": False}], 0.08)
    assert rows[0]["min_odds"]["1"] == pytest.approx(2.16) and "min_odds" not in rows[1]
