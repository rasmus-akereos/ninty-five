import numpy as np
import pandas as pd

from ninty_five import data, validate
from ninty_five.history import HistoryStore


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
