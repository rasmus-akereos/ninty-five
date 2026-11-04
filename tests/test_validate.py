import numpy as np
import pandas as pd

from ninety_five import data, validate
from ninety_five.history import HistoryStore


def _two_divisions():
    rng = np.random.default_rng(1)
    top, second = ["T1", "T2", "T3", "T4"], ["S1", "S2", "S3", "S4"]
    rows = []
    for i in range(500):
        day = pd.Timestamp("2024-08-01") + pd.Timedelta(days=i)
        for teams, label, mu in ((top, "Germany 1", 1.6), (second, "Germany 2", 1.2)):
            h, a = rng.choice(teams, 2, replace=False)
            rows.append((day, h, a, rng.poisson(mu), rng.poisson(1.0), False, label))
    df = pd.DataFrame(rows, columns=data.COLUMNS)
    groups = {t: "Germany 1" for t in top} | {t: "Germany 2" for t in second}
    return df, groups


def test_validate_country_reports_each_new_division(monkeypatch):
    monkeypatch.setattr(validate.data, "load_country", lambda key, refresh_current=False: _two_divisions())
    out = validate.validate_country("GER", start="2025-06-01", step_months=3)
    assert set(out) == {"Germany 2"}  # only the new leagues are reported
    g = out["Germany 2"]
    assert g["matches"] > 100 and 0.5 < g["model"] < 1.5 and 0.5 < g["baseline"] < 1.5


def test_validation_round_trip(tmp_path):
    h = HistoryStore(tmp_path / "h.sqlite")
    assert h.validation() == {}
    h.set_validation({"Spain 2": {"model": 1.02, "baseline": 1.01, "matches": 300}}, as_of="2026-11-03")
    h.set_validation({"Italy 2": {"model": 1.00, "baseline": 1.01, "matches": 280}}, as_of="2026-11-04")
    v = h.validation()
    assert v["Spain 2"]["model"] == 1.02 and v["Italy 2"]["as_of"] == "2026-11-04"
    assert v["Spain 2"]["as_of"] == "2026-11-03"


# ---------------------------------------------------------------- UEFA model
from ninety_five import uefa  # noqa: E402


def test_unproven_judges_europa_rows():
    row = {"competition": "Europa League"}
    worse = {"Europa League": {"model": 1.05, "baseline": 1.04, "matches": 300}}
    assert validate.unproven(row, worse) == {"model": 1.05, "baseline": 1.04}
    better = {"Europa League": {"model": 1.00, "baseline": 1.04, "matches": 300}}
    assert validate.unproven(row, better) is None
    assert validate.unproven({"competition": "Conference League"}, {}) == {"model": None, "baseline": None}
    assert validate.unproven({"competition": "Champions League"}, {}) is None


def _synthetic_uefa():
    rng = np.random.default_rng(1)
    rows = []
    bel, ned = ["A", "B", "C", "D"], ["E", "F", "G", "H"]
    for i, day in enumerate(pd.date_range("2021-08-01", "2026-10-31", freq="7D")):
        for teams, label in ((bel, "Belgium 1"), (ned, "Netherlands 1")):
            h, a = teams[i % 4], teams[(i + 1 + i // 4) % 4]
            if h != a:
                rows.append((day, h, a, int(rng.poisson(1.5)), int(rng.poisson(1.1)), False, label))
        if i % 2 == 0:
            rows.append((day + pd.Timedelta(days=3), "A", "E", int(rng.poisson(1.4)), int(rng.poisson(1.2)), False,
                         "Europa League"))
            rows.append((day + pd.Timedelta(days=3), "Z", "C", int(rng.poisson(1.0)), int(rng.poisson(1.3)), False,
                         "Europa League"))
        if i % 4 == 0:
            rows.append((day + pd.Timedelta(days=3), "B", "F", int(rng.poisson(1.3)), int(rng.poisson(1.3)), False,
                         "Conference League"))
            rows.append((day + pd.Timedelta(days=4), "Z", "H", 1, 1, False, "Conference League qualifying"))
    df = pd.DataFrame(rows, columns=data.COLUMNS)
    groups = {**{t: "Belgium 1" for t in bel}, **{t: "Netherlands 1" for t in ned}, "Z": uefa.EUROPEAN_ONLY}
    return df, groups, uefa.Resolution(european_only={"Z"})


def test_validate_uefa_scores_only_domestic_pairs_and_pools_small_samples(monkeypatch):
    monkeypatch.setattr(uefa, "load_uefa", lambda start=None, **kw: _synthetic_uefa())
    out = validate.validate_uefa(start="2026-05-01")
    assert out["Europa League"]["matches"] == out["Conference League"]["matches"]  # pooled: ECL under 150
    assert out["Europa League"]["model"] == out["Conference League"]["model"]
    assert out["Europa League (thin data)"]["matches"] > 0  # Z v C
    df, _, _ = _synthetic_uefa()
    after = df[(df.date >= "2026-05-01") & df.competition.isin(["Europa League", "Conference League"])
               & (df.home != "Z")]
    assert out["Europa League"]["matches"] == len(after)
