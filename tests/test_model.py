import numpy as np
import pandas as pd
import pytest

from ninty_five.model import DixonColes, markets, score_matrix, settle


def synthetic(n_rounds=40, seed=0):
    rng = np.random.default_rng(seed)
    teams = [f"T{i}" for i in range(10)]
    att = np.linspace(-0.4, 0.4, 10)
    dfn = np.linspace(0.3, -0.3, 10)
    mu, home = 0.2, 0.25
    rows, day = [], pd.Timestamp("2024-01-01")
    for r in range(n_rounds):
        for i in range(10):
            for j in range(10):
                if i == j or rng.random() > 0.2:
                    continue
                lh = np.exp(mu + home + att[i] - dfn[j])
                la = np.exp(mu + att[j] - dfn[i])
                rows.append((day + pd.Timedelta(days=r * 7), teams[i], teams[j], rng.poisson(lh), rng.poisson(la), False, "L"))
    return pd.DataFrame(rows, columns=["date", "home", "away", "hg", "ag", "neutral", "competition"]), att


def test_fit_recovers_ordering_and_home_advantage():
    df, att = synthetic()
    m = DixonColes(half_life_days=1e6, kappa=0.01).fit(df)
    est = np.array([m.attack[m.teams.index(f"T{i}")] for i in range(10)])
    assert np.corrcoef(est, att)[0, 1] > 0.9
    assert 0.1 < m.home_adv < 0.4


def test_neutral_removes_home_advantage():
    df, _ = synthetic()
    m = DixonColes().fit(df)
    h, _ = m.expected_goals("T5", "T4", neutral=False)
    hn, _ = m.expected_goals("T5", "T4", neutral=True)
    assert h > hn


def test_markets_consistent():
    mk = markets(score_matrix(1.6, 1.1, -0.05))
    assert mk["1"] + mk["X"] + mk["2"] == pytest.approx(1)
    assert mk["Over 2.5"] + mk["Under 2.5"] == pytest.approx(1)
    assert mk["1X"] == pytest.approx(mk["1"] + mk["X"])
    assert mk["1"] > mk["2"]


def test_unknown_team():
    df, _ = synthetic(5)
    with pytest.raises(KeyError):
        DixonColes().fit(df).expected_goals("T1", "Nowhere FC")


@pytest.mark.parametrize(
    "market,hg,ag,expected",
    [("1", 2, 1, True), ("X", 1, 1, True), ("2", 1, 1, False), ("X2", 0, 0, True),
     ("Over 2.5", 2, 1, True), ("Under 2.5", 2, 1, False), ("BTTS Yes", 1, 0, False), ("BTTS No", 0, 3, True)],
)
def test_settle(market, hg, ag, expected):
    assert settle(market, hg, ag) is expected
