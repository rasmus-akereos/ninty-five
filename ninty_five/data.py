"""Historical match data loaders.

Every loader returns a DataFrame with the same columns:

    date (datetime64), home (str), away (str), hg (int), ag (int),
    neutral (bool), competition (str)

Sources
-------
* International (national teams, worldwide): the open dataset maintained at
  github.com/martj42/international_results. Covers every senior men's
  international since 1872, including World Cup, continental cups,
  qualifiers, Nations Leagues and friendlies.
* Club leagues: football-data.co.uk CSVs. "Main" leagues (E0, D1, SP1, ...)
  live under /mmz4281/<season>/<code>.csv; "extra" leagues (BRA, ARG, USA,
  JPN, ...) live under /new/<CODE>.csv.
"""

from __future__ import annotations

import io
import urllib.request
from pathlib import Path

import pandas as pd

COLUMNS = ["date", "home", "away", "hg", "ag", "neutral", "competition"]

INTERNATIONAL_URL = (
    "https://raw.githubusercontent.com/martj42/international_results/master/results.csv"
)
FD_MAIN_URL = "https://www.football-data.co.uk/mmz4281/{season}/{code}.csv"
FD_EXTRA_URL = "https://www.football-data.co.uk/new/{code}.csv"

# football-data.co.uk league codes. Main leagues use season folders.
FD_MAIN_LEAGUES = {
    "E0": "England Premier League",
    "E1": "England Championship",
    "SC0": "Scotland Premiership",
    "D1": "Germany Bundesliga",
    "D2": "Germany 2. Bundesliga",
    "I1": "Italy Serie A",
    "SP1": "Spain La Liga",
    "F1": "France Ligue 1",
    "N1": "Netherlands Eredivisie",
    "B1": "Belgium Pro League",
    "P1": "Portugal Primeira Liga",
    "T1": "Turkey Super Lig",
    "G1": "Greece Super League",
}
FD_EXTRA_LEAGUES = {
    "DNK": "Denmark Superliga",
    "SWE": "Sweden Allsvenskan",
    "NOR": "Norway Eliteserien",
    "FIN": "Finland Veikkausliiga",
    "BRA": "Brazil Serie A",
    "ARG": "Argentina Primera",
    "USA": "USA MLS",
    "MEX": "Mexico Liga MX",
    "JPN": "Japan J-League",
    "CHN": "China Super League",
    "AUT": "Austria Bundesliga",
    "SWZ": "Switzerland Super League",
    "POL": "Poland Ekstraklasa",
    "ROU": "Romania Liga 1",
    "RUS": "Russia Premier League",
    "IRL": "Ireland Premier Division",
}

DATA_DIR = Path(__file__).resolve().parent.parent / "data"


def _fetch(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "ninty-five/0.1"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return resp.read()


def _cached(name: str, url: str, refresh: bool) -> bytes:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    path = DATA_DIR / name
    if refresh or not path.exists():
        path.write_bytes(_fetch(url))
    return path.read_bytes()


def load_international(refresh: bool = False, since: str | None = None) -> pd.DataFrame:
    raw = _cached("international_results.csv", INTERNATIONAL_URL, refresh)
    df = pd.read_csv(io.BytesIO(raw))
    return normalise_international(df, since)


def normalise_international(df: pd.DataFrame, since: str | None = None) -> pd.DataFrame:
    df = df.dropna(subset=["home_score", "away_score"]).copy()
    out = pd.DataFrame(
        {
            "date": pd.to_datetime(df["date"]),
            "home": df["home_team"].astype(str),
            "away": df["away_team"].astype(str),
            "hg": df["home_score"].astype(int),
            "ag": df["away_score"].astype(int),
            "neutral": df["neutral"].astype(str).str.upper().eq("TRUE"),
            "competition": df["tournament"].astype(str),
        }
    )
    if since:
        out = out[out["date"] >= pd.Timestamp(since)]
    return out.sort_values("date").reset_index(drop=True)[COLUMNS]


def _season_codes(n_seasons: int, today: pd.Timestamp | None = None) -> list[str]:
    """football-data season folder names, e.g. '2425' for 2024/25."""
    today = today or pd.Timestamp.today()
    start = today.year if today.month >= 7 else today.year - 1
    return [f"{(y % 100):02d}{((y + 1) % 100):02d}" for y in range(start - n_seasons + 1, start + 1)]


def load_club_league(code: str, n_seasons: int = 4, refresh: bool = False) -> pd.DataFrame:
    code = code.upper()
    if code in FD_MAIN_LEAGUES:
        frames = []
        for season in _season_codes(n_seasons):
            try:
                raw = _cached(f"{code}_{season}.csv", FD_MAIN_URL.format(season=season, code=code), refresh)
            except Exception:  # season not published yet
                continue
            frames.append(pd.read_csv(io.BytesIO(raw), encoding="latin-1", on_bad_lines="skip"))
        if not frames:
            raise RuntimeError(f"No data downloaded for {code}")
        df = pd.concat(frames, ignore_index=True)
        out = pd.DataFrame(
            {
                "date": pd.to_datetime(df["Date"], dayfirst=True, format="mixed"),
                "home": df["HomeTeam"],
                "away": df["AwayTeam"],
                "hg": df["FTHG"],
                "ag": df["FTAG"],
            }
        )
        name = FD_MAIN_LEAGUES[code]
    elif code in FD_EXTRA_LEAGUES:
        raw = _cached(f"{code}.csv", FD_EXTRA_URL.format(code=code), refresh)
        df = pd.read_csv(io.BytesIO(raw), encoding="latin-1", on_bad_lines="skip")
        out = pd.DataFrame(
            {
                "date": pd.to_datetime(df["Date"], dayfirst=True, format="mixed"),
                "home": df["Home"],
                "away": df["Away"],
                "hg": df["HG"],
                "ag": df["AG"],
            }
        )
        cutoff = pd.Timestamp.today() - pd.DateOffset(years=n_seasons)
        out = out[out["date"] >= cutoff]
        name = FD_EXTRA_LEAGUES[code]
    else:
        raise ValueError(f"Unknown league code {code!r}")

    out = out.dropna(subset=["home", "away", "hg", "ag"]).copy()
    out["hg"] = out["hg"].astype(int)
    out["ag"] = out["ag"].astype(int)
    out["neutral"] = False
    out["competition"] = name
    return out.sort_values("date").reset_index(drop=True)[COLUMNS]


def available_datasets() -> dict[str, str]:
    return {"INTL": "International (national teams)", **FD_MAIN_LEAGUES, **FD_EXTRA_LEAGUES}


def load_dataset(key: str, refresh: bool = False) -> pd.DataFrame:
    if key.upper() == "INTL":
        return load_international(refresh=refresh, since="2014-01-01")
    return load_club_league(key, refresh=refresh)
