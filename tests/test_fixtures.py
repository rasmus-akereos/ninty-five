import pandas as pd
import pytest

from ninty_five import fixtures, service
from ninty_five.store import BetStore


def _match(mid, date, home, away, hg=None, ag=None, status="FINISHED", stage="REGULAR_SEASON", code=None, rt=None):
    m = {
        "id": mid,
        "utcDate": f"{date}T19:00:00Z",
        "status": status,
        "stage": stage,
        "homeTeam": {"name": home},
        "awayTeam": {"name": away},
        "score": {"fullTime": {"home": hg, "away": ag}, "regularTime": rt or {"home": None, "away": None}},
    }
    if code:
        m["competition"] = {"code": code}
    return m


def test_goals_90_uses_regular_time_after_extra_time():
    m = _match(1, "2026-03-01", "A", "B", 3, 2, rt={"home": 2, "away": 2})
    df = fixtures.parse_matches({"matches": [m]}, "CL")
    assert (df.hg[0], df.ag[0]) == (2, 2)
    plain = fixtures.parse_matches({"matches": [_match(2, "2026-03-01", "A", "B", 1, 0)]}, "PL")
    assert (plain.hg[0], plain.ag[0]) == (1, 0)


def test_api_key_from_env_file(tmp_path, monkeypatch):
    for k in fixtures.KEY_NAMES:
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(fixtures, "ENV_FILE", tmp_path / ".env")
    with pytest.raises(fixtures.ApiKeyMissing):
        fixtures.api_key()
    (tmp_path / ".env").write_text("FOOTBALL_DATA_TOKEN='abc'\n")
    assert fixtures.api_key() == "abc"


def _fake_europe():
    """Two leagues, linked only through CL matches; league X is clearly stronger."""
    rows, mid = [], 0
    for season_day in range(0, 400, 7):
        d = (pd.Timestamp("2025-08-01") + pd.Timedelta(days=season_day)).date()
        for league, teams in (("PL", ["X1", "X2", "X3", "X4"]), ("DED", ["Y1", "Y2", "Y3", "Y4"])):
            for i in range(0, 4, 2):
                mid += 1
                h, a = teams[(i + season_day // 7) % 4], teams[(i + 1 + season_day // 7) % 4]
                rows.append((league, _match(mid, d, h, a, 1 + (season_day // 7) % 2, 1)))
        if season_day % 28 == 0:  # European night: X teams beat Y teams
            mid += 1
            rows.append(("CL", _match(mid, d, f"X{1 + season_day % 4}", f"Y{1 + season_day % 3}", 3, 0, stage="LEAGUE_STAGE")))
            mid += 1
            rows.append(("CL", _match(mid, d, f"Y{2 + season_day % 3}", f"X{2}", 0, 2, stage="LEAGUE_STAGE")))
    return rows


def test_europe_board_and_auto_settle(monkeypatch, tmp_path):
    rows = _fake_europe()

    def fake_season(code, season, **kw):
        return fixtures.parse_matches({"matches": [m for c, m in rows if c == code]}, code)

    monkeypatch.setattr(fixtures, "season_matches", fake_season)
    monkeypatch.setattr(fixtures, "current_season", lambda today=None: 2025)
    df, groups = fixtures.load_europe(n_seasons=1)
    assert groups["X1"] == "England" and groups["Y1"] == "Netherlands"

    model = service.fit_dataset("EUROPE")
    # league offsets learned from CL games: English side favoured against a Dutch side
    lh, la = model.expected_goals("X3", "Y3", neutral=True)
    assert lh > la
    assert model.group_strength["England"][0] > model.group_strength["Netherlands"][0]

    upcoming = fixtures.parse_matches(
        {"matches": [_match(9001, "2026-12-01", "X1", "Y1", status="TIMED", stage="LEAGUE_STAGE", code="CL"),
                     _match(9002, "2026-12-01", "X1", "Newcomer FC", status="SCHEDULED", code="CL")]}
    )
    monkeypatch.setattr(fixtures, "upcoming", lambda days=7, competitions=None: upcoming)
    service._models.clear()
    board = service.board(days=7, min_edge=0.1)
    assert board[0]["rated"] and not board[1]["rated"]
    p = board[0]["probabilities"]
    assert p["1"] + p["X"] + p["2"] == pytest.approx(1)
    assert board[0]["min_odds"]["1"] == pytest.approx(1.1 / p["1"])

    st = BetStore(tmp_path / "b.sqlite")
    bid = st.add({"dataset": "EUROPE", "home": "X1", "away": "Y1", "market": "1", "odds": 1.8, "stake": 10, "match_id": 9001})
    other = st.add({"dataset": "INTL", "home": "A", "away": "B", "market": "1", "odds": 2.0, "stake": 10})
    done = pd.DataFrame([{"match_id": 9001, "hg": 2, "ag": 0}])
    assert st.auto_settle(done) == [bid]
    bets = {b["id"]: b for b in st.list()}
    assert bets[bid]["status"] == "won" and bets[bid]["pnl"] == pytest.approx(8)
    assert bets[other]["status"] == "open"
