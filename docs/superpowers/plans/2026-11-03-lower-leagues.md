# Lower Leagues and a Daily Fetch Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Put League One, League Two, 2. Bundesliga, Segunda División, Serie B, Ligue 2 and the Danish Superliga on the board, priced by per-country models, with results and auto-settlement, and fetch the whole board once a day automatically.

**Architecture:** football-data.co.uk is the only source for the new leagues: its season CSVs train six country models (`ENG`, `GER`, `ESP`, `ITA`, `FRA`, `DEN`) and give results, and its two "upcoming fixtures" files give fixtures with the same team names. A new `league_fixtures.py` reads fixtures and matches results; `service.build_board` gains it as a third source. A `scheduler.py` thread in the server runs the refresh at 07:00 Copenhagen time, guarded by a SQLite lease that the manual refresh also takes.

**Tech Stack:** Python 3, pandas, FastAPI, SQLite, pytest (monkeypatched sources, no network), vanilla JS in `ninty_five/static/index.html`.

**Spec:** `docs/superpowers/specs/2026-11-03-lower-leagues-design.md`

## Global Constraints

- Existing leagues, national teams, the Champions League and all calculations keep working exactly as now (EUROPE / INTL models unchanged; the Championship stays on EUROPE).
- Board labels, exactly: `England 3`, `England 4`, `Germany 2`, `Spain 2`, `Italy 2`, `France 2`, `Denmark 1`.
- Country models: `ENG` = E0, E1, E2, E3 · `GER` = D1, D2 · `ESP` = SP1, SP2 · `ITA` = I1, I2 · `FRA` = F1, F2 · `DEN` = DNK only (no groups). Three seasons plus the current one. A team's group is the division it played in most recently.
- Match key for the new leagues: `fdcu:<division code>:<kick-off date YYYY-MM-DD, UK>:<home>:<away>`.
- Fixture times in the files are UK local time (`Europe/London`; verified: Blackburn–Stoke "19:45" = 20:45 Copenhagen). Store kick-offs as ISO UTC like the other sources.
- Results: same division + home + away, date closest to the stored kick-off, within 30 days. No result 30 days after kick-off → status `NO_RESULT`.
- Daily run: 07:00 `Europe/Copenhagen`, 7-day window, thread wakes hourly; catch up on start if the last fetch is older than 24 h or missing; refresh current-season CSVs of the six country models and refit them.
- Lease: one row in SQLite, abandoned after 30 minutes; a manual refresh that finds it held returns the stored board with the warning `Refresh already running`.
- Validation: a division whose walk-forward 1X2 log loss does not beat its baseline is shown with a `model unproven` tag (styled like `thin data`, tooltip with both numbers) and a note in the fold-down.
- No new dependencies (`zoneinfo` is in the standard library). No OS scheduler. Never print the football-data.org token.
- Commits end with:
  `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>` and `Claude-Session: https://claude.ai/code/session_01JvQovzdGJqot2swz3p8d9w`

## Review Focus

1. A fixtures file missing, empty, or with a division the app doesn't know → that source becomes a warning, other sources still show (Task 3 test `test_build_board_lower_league_failure_is_warning`).
2. A match moved to another day after it was stored under its old key → the old row still gets its result and its bets settle (Task 2 `test_match_result_moved_match`, Task 3 `test_update_results_fdcu`).
3. The app restarted after the machine was off at 07:00 → one catch-up fetch, not two, and not a fetch on every restart (Task 6 `test_due_*`).
4. A manual refresh pressed while the daily run is fetching → no second fetch, no 502 (Task 5 `test_refresh_while_leased`).
5. A crashed run that never released the lease → the next run after 30 minutes proceeds (Task 5 `test_lease_abandoned_after_30_minutes`).

---

### Task 1: Country data sets and models

**Files:**
- Modify: `ninty_five/data.py` (add divisions, `refresh_current`, `COUNTRIES`, `load_country`)
- Modify: `ninty_five/service.py:23-49` (`datasets`, `_fit`, `_cached` expiry)
- Test: `tests/test_data_countries.py`

**Interfaces:**
- Produces:
  - `data.COUNTRIES: dict[str, dict[str, str]]` — country key → {division code: board label}, e.g. `"ENG": {"E0": "England 1", "E1": "England 2", "E2": "England 3", "E3": "England 4"}`, `"DEN": {"DNK": "Denmark 1"}` (top divisions labelled `<Country> 1`, my call).
  - `data.DIVISION_COUNTRY: dict[str, str]` — division code → country key.
  - `data.load_club_league(code, n_seasons=4, refresh=False, refresh_current=False) -> DataFrame` — `refresh_current` re-downloads only the newest season file (for DNK, its single file).
  - `data.load_country(key, refresh_current=False) -> tuple[DataFrame, dict[str, str]]` — concatenated results (`competition` = board label) and team → board label of the division it played in most recently.
  - `service._fit("ENG")` etc. fits `DixonColes(**CLUB_PARAMS)` with those groups (no groups for DEN); `service._cached(key, refresh="current")` refits with `refresh_current=True`.

- [ ] **Step 1: Write the failing tests** (monkeypatch `data._cached` to return small CSV bytes per file name; no network)

```python
def test_load_country_concatenates_divisions_with_latest_group(fake_csvs):
    df, groups = data.load_country("GER")
    assert set(df["competition"]) == {"Germany 1", "Germany 2"}
    assert groups["Promoted FC"] == "Germany 1"   # played D2 last season, D1 this season

def test_load_country_den_reads_extra_format_without_groups(fake_csvs):
    df, groups = data.load_country("DEN")
    assert set(df["competition"]) == {"Denmark 1"} and groups == {}

def test_refresh_current_downloads_only_newest_season(monkeypatch):
    calls = []  # record (name, refresh) passed to _cached
    ...
    data.load_club_league("E2", refresh_current=True)
    assert [n for n, r in calls if r] == ["E2_2627.csv"]

def test_new_divisions_known():
    assert {"E2", "E3", "SP2", "I2", "F2"} <= set(data.FD_MAIN_LEAGUES)
    assert data.DIVISION_COUNTRY["DNK"] == "DEN" and data.DIVISION_COUNTRY["SP2"] == "ESP"
```

- [ ] **Step 2: Run** `pytest tests/test_data_countries.py -v` — Expected: FAIL (`load_country` not defined).

- [ ] **Step 3: Implement** the Produces items. Country keys go in `service.datasets()` as e.g. `"ENG": "England: Premier League to League Two"`. Country models expire after `MODEL_TTL_SECONDS` like EUROPE.

- [ ] **Step 4: Run** `pytest tests/test_data_countries.py -v` then `pytest -q` — Expected: all pass.

- [ ] **Step 5: Smoke-fit for real** `python -c "from ninty_five import service; m=service.get_model('ENG'); print(len(m.teams), m.group_strength)"` — Expected: ~92 teams, four groups with England 1 strongest. Repeat for DEN (no groups).

- [ ] **Step 6: Commit** `Add country data sets for the lower leagues`

---

### Task 2: Lower-league fixtures and results module

**Files:**
- Create: `ninty_five/league_fixtures.py`
- Test: `tests/test_league_fixtures.py`, fixtures `tests/data/fdcu_fixtures.csv`, `tests/data/fdcu_new_league_fixtures.csv` (trimmed copies of today's real files, plus one E2 and one Denmark row added by hand)

**Interfaces:**
- Consumes: `data.COUNTRIES`, `data.DIVISION_COUNTRY`, `data.load_club_league(..., refresh_current=True)`, `data._fetch`.
- Produces:
  - `FIXTURES_URL = "https://www.football-data.co.uk/fixtures.csv"`, `NEW_FIXTURES_URL = "https://www.football-data.co.uk/new_league_fixtures.csv"`
  - `NEW_DIVISIONS = {"E2": "England 3", "E3": "England 4", "D2": "Germany 2", "SP2": "Spain 2", "I2": "Italy 2", "F2": "France 2", "DNK": "Denmark 1"}`
  - `parse_main(raw: bytes) -> DataFrame` and `parse_extra(raw: bytes) -> DataFrame`, both with columns `division, kickoff (tz-aware UTC), home, away`, keeping only `NEW_DIVISIONS` (extra file: `Country == "Denmark"` and `League.strip() == "Superliga"` → `DNK`).
  - `match_key(division: str, kickoff_utc: pd.Timestamp, home: str, away: str) -> str` — date part is the UK local date.
  - `upcoming(days: int, now: pd.Timestamp) -> DataFrame` — both files, `now <= kickoff < now.normalize() + days`, with `key`, `dataset` (country key), `competition` (board label). Files cached in memory for 1 hour.
  - `results(division: str) -> DataFrame` — that division's results (data.COLUMNS) with the current season freshly downloaded, cached in memory for 1 hour.
  - `match_result(res: DataFrame, home: str, away: str, kickoff: pd.Timestamp) -> tuple[int, int] | None` — closest date within 30 days.

- [ ] **Step 1: Write the failing tests**

```python
def test_parse_main_keeps_new_divisions_and_converts_uk_time():
    df = league_fixtures.parse_main(SAMPLE_MAIN)
    assert set(df["division"]) <= set(league_fixtures.NEW_DIVISIONS) and "E1" not in set(df["division"])
    r = df[df.home == "<E2 home in sample>"].iloc[0]
    assert r.kickoff == pd.Timestamp("<date> 19:45", tz="Europe/London").tz_convert("UTC")

def test_parse_main_summer_time():  # 15:00 UK in August = 14:00 UTC
def test_parse_extra_only_danish_superliga():
def test_match_key_uses_uk_date():  # 23:30 UTC on a summer day is the next UK date → key date is UK date
def test_upcoming_window_and_labels(monkeypatch):  # _fetch patched; competition "Denmark 1", dataset "DEN"
def test_match_result_moved_match():
    res = _df([("2026-11-14", "Wigan", "Bolton", 2, 1)])
    assert league_fixtures.match_result(res, "Wigan", "Bolton", pd.Timestamp("2026-11-08T15:00Z")) == (2, 1)
def test_match_result_none_beyond_30_days_or_other_pair():
def test_match_result_picks_closest_of_two_meetings():  # Superliga pair twice in a season
```

- [ ] **Step 2: Run** `pytest tests/test_league_fixtures.py -v` — Expected: FAIL (module missing).
- [ ] **Step 3: Implement** the Produces items. Read CSVs with `encoding="utf-8-sig"` falling back to latin-1; dates `dayfirst=True`.
- [ ] **Step 4: Run** `pytest tests/test_league_fixtures.py -v` — Expected: PASS.
- [ ] **Step 5: Commit** `Read lower-league fixtures and results from football-data.co.uk`

---

### Task 3: Board source and results for the new leagues

**Files:**
- Modify: `ninty_five/service.py` (`league_board`, `build_board`, `update_results`)
- Modify: `ninty_five/history.py:19` (key comment only)
- Test: `tests/test_service_board.py`

**Interfaces:**
- Consumes: Task 1 `_cached(country)`, Task 2 `upcoming`, `results`, `match_result`.
- Produces:
  - `service.league_board(days: int, now: pd.Timestamp | None = None) -> list[dict]` — board rows shaped like `intl_board`'s (`key`, `dataset`=country, `match_id` None, `competition`=board label, `detail` "", `stage` "", `neutral` False), priced via `_priced` with that country's model and training frame.
  - `build_board` runs three sources (`Club fixtures`, `National-team fixtures`, `Lower-league fixtures`); raises only when all three fail.
  - `update_results` handles `fdcu:` keys: pending window 31 days for them (14 days stays for `fd:`/`espn:`), result via `match_result` → `set_result(key, hg, ag, "FINISHED")`; none and kick-off more than 30 days ago → `set_result(key, None, None, "NO_RESULT")`.

- [ ] **Step 1: Write the failing tests**

```python
def test_league_board_prices_with_country_model(monkeypatch):
    # fake upcoming() row for E2, fake _cached("ENG") model knowing both teams
    rows = service.league_board(7)
    assert rows[0]["key"].startswith("fdcu:E2:") and rows[0]["dataset"] == "ENG" and rows[0]["rated"]
def test_league_board_unknown_team_unrated(monkeypatch):
def test_build_board_lower_league_failure_is_warning(monkeypatch):
    # league_board raises → rows from the other two, warning "Lower-league fixtures unavailable: ..."
def test_build_board_raises_only_when_all_three_fail(monkeypatch):
def test_update_results_fdcu(tmp_path, monkeypatch):
    # stored fdcu match kicked off 8 Nov, result dated 14 Nov → FINISHED 2-1, open bet with that key settled
def test_update_results_fdcu_no_result_after_30_days(tmp_path, monkeypatch):
```

- [ ] **Step 2: Run** `pytest tests/test_service_board.py -v` — Expected: new tests FAIL.
- [ ] **Step 3: Implement** the Produces items.
- [ ] **Step 4: Run** `pytest -q` — Expected: all pass.
- [ ] **Step 5: Commit** `Add the lower leagues to the board and settle their bets`

---

### Task 4: Validation and the "model unproven" tag

**Files:**
- Create: `ninty_five/validate.py`
- Modify: `ninty_five/cli.py` (subcommand `validate`), `ninty_five/history.py` (meta helpers), `ninty_five/app.py` (`_stored_board`), `ninty_five/static/index.html` (tag + fold note)
- Test: `tests/test_validate.py`, `tests/test_app.py`

**Interfaces:**
- Consumes: `data.load_country`, `data.COUNTRIES`, `NEW_DIVISIONS`, `cli.backtest`'s method (refit at each monthly cut-off from `2024-08-01`, score the next month, baseline = home/draw/away frequencies of that division over the previous 3 years).
- Produces:
  - `validate.validate_country(key: str, start: str = "2024-08-01", step_months: int = 1) -> dict[str, dict]` → `{board label: {"model": float, "baseline": float, "matches": int}}` for that country's divisions in `NEW_DIVISIONS` (weighted means).
  - `HistoryStore.set_validation(results: dict) -> None` and `HistoryStore.validation() -> dict` (meta key `validation`, JSON, merged per label, plus `as_of`).
  - `_stored_board` adds `"unproven": {"model": x, "baseline": y}` to rows whose competition is in `NEW_DIVISIONS` labels and whose stored `model >= baseline`; a new-league row with no stored validation gets `"unproven": {"model": None, "baseline": None}` (my call: not validated counts as unproven).
  - CLI: `ninty-five validate [--country ENG]` runs, prints a table, stores results.
  - UI: badge `model unproven` next to `thin data`, tooltip `Model log loss 1.0123 vs baseline 1.0089 (lower is better)` or `Not validated yet`; fold-down note `Model unproven in this league: it hasn't beaten simple home/draw/away frequencies. Treat its prices with caution.`

- [ ] **Step 1: Write the failing tests**

```python
def test_validate_country_reports_each_new_division(monkeypatch):  # small synthetic two-division data
    out = validate.validate_country("GER", start="2025-01-01")
    assert set(out) == {"Germany 2"} and out["Germany 2"]["matches"] > 0
def test_validation_round_trip(tmp_path):
def test_board_marks_unproven_division(client_with_board):
    # validation {"Spain 2": model 1.02, baseline 1.01} → Spain 2 rows carry unproven, Italy 2 (1.00 < 1.01) don't,
    # England 1 / Championship / International rows never do
def test_board_unvalidated_new_division_is_unproven(client_with_board):
```

- [ ] **Step 2: Run** `pytest tests/test_validate.py tests/test_app.py -v` — Expected: new tests FAIL.
- [ ] **Step 3: Implement** the Produces items.
- [ ] **Step 4: Run** `pytest -q` — Expected: all pass.
- [ ] **Step 5: Run for real** `.venv/bin/ninty-five validate` — Expected: a row per new division; record the numbers in the ledger for the final report.
- [ ] **Step 6: Commit** `Validate the lower-league models and tag unproven leagues`

---

### Task 5: Refresh lease and run records

**Files:**
- Modify: `ninty_five/history.py` (tables `leases`, `fetch_runs`), `ninty_five/service.py` (`run_refresh`), `ninty_five/app.py` (refresh endpoint, `_stored_board`)
- Test: `tests/test_history.py`, `tests/test_app.py`

**Interfaces:**
- Produces:
  - `HistoryStore.take_lease(name: str, holder: str, now: pd.Timestamp, ttl: pd.Timedelta = pd.Timedelta(minutes=30)) -> bool` (atomic: one `INSERT … ON CONFLICT DO UPDATE … WHERE taken_at < now - ttl`, true if this holder now holds it), `HistoryStore.release_lease(name: str, holder: str) -> None`.
  - `HistoryStore.record_run(kind: str, started: str, finished: str, matches: int, warnings: list[str], error: str | None) -> None`, `HistoryStore.last_run(kind: str) -> dict | None`, `HistoryStore.last_success(kind: str | None = None) -> str | None` (`kind` = `"auto"` or `"manual"`).
  - `service.run_refresh(history, bets, days: int, kind: str, now=None, refit_countries: bool = False) -> dict | None` — takes lease `"refresh"`, optionally refits the six country models with `refresh="current"`, calls `refresh_board`, records the run (error recorded and re-raised), always releases; returns None when the lease is held.
  - Refresh endpoint uses `run_refresh(kind="manual")`; on None returns the stored board with `warnings: ["Refresh already running"]`.
  - `_stored_board` adds `"last_auto": history.last_run("auto")` (`{finished, matches, error}` or None).

- [ ] **Step 1: Write the failing tests**

```python
def test_lease_exclusive(tmp_path):
def test_lease_abandoned_after_30_minutes(tmp_path):
def test_release_only_by_holder(tmp_path):
def test_run_refresh_records_success_and_failure(tmp_path, monkeypatch):
def test_refresh_while_leased(client):  # lease held by "auto" → 200, warnings == ["Refresh already running"], no fetch called
```

- [ ] **Step 2: Run** `pytest tests/test_history.py tests/test_app.py -v` — Expected: new tests FAIL.
- [ ] **Step 3: Implement** the Produces items.
- [ ] **Step 4: Run** `pytest -q` — Expected: all pass.
- [ ] **Step 5: Commit** `Guard refreshes with a lease and record each run`

---

### Task 6: Daily fetch thread

**Files:**
- Create: `ninty_five/scheduler.py`
- Modify: `ninty_five/app.py` (start on startup), `ninty_five/static/index.html` (info line), `tests/conftest.py` (create: set `NINTY_FIVE_SCHEDULER=0`), `README.md`
- Test: `tests/test_scheduler.py`

**Interfaces:**
- Consumes: Task 5 `run_refresh`, `last_success`, `last_run`.
- Produces:
  - `scheduler.RUN_AT = time(7, 0)`, `scheduler.TZ = ZoneInfo("Europe/Copenhagen")`.
  - `scheduler.due(last_fetch: pd.Timestamp | None, last_auto: pd.Timestamp | None, now: pd.Timestamp) -> bool` — true if `last_fetch` is None or older than 24 h; or if `now` is at/after today's 07:00 Copenhagen and `last_auto` (last successful automatic run) is before it. `last_fetch` = last successful run of any kind.
  - `scheduler.start(history, bets, days: int = 7, wake: float = 3600) -> threading.Thread | None` — daemon thread: check `due` at start, then every `wake` seconds; when due, `run_refresh(kind="auto", refit_countries=True)`; exceptions logged, never kill the loop. Returns None when env `NINTY_FIVE_SCHEDULER` is `"0"`.
  - Info line: `Auto-fetched <dd Mon HH:MM>` or `Auto-fetch failed <time>: <error> — retrying hourly`.

- [ ] **Step 1: Write the failing tests**

```python
CPH = "Europe/Copenhagen"
def T(s): return pd.Timestamp(s, tz=CPH).tz_convert("UTC")
def test_due_first_start_without_any_fetch():           assert scheduler.due(None, None, T("2026-11-03 12:00"))
def test_due_catch_up_after_24h():                      assert scheduler.due(T("2026-11-02 06:00"), T("2026-11-02 07:00"), T("2026-11-03 06:30"))
def test_not_due_on_restart_after_recent_fetch_before_7(): assert not scheduler.due(T("2026-11-03 01:00"), T("2026-11-02 07:00"), T("2026-11-03 06:30"))
def test_due_at_seven_copenhagen():                     assert scheduler.due(T("2026-11-03 01:00"), T("2026-11-02 07:00"), T("2026-11-03 07:05"))
def test_not_due_twice_same_day():                      assert not scheduler.due(T("2026-11-03 07:01"), T("2026-11-03 07:01"), T("2026-11-03 09:00"))
def test_manual_fetch_after_seven_does_not_replace_auto(): assert scheduler.due(T("2026-11-03 07:30"), T("2026-11-02 07:00"), T("2026-11-03 08:00"))
def test_seven_is_copenhagen_across_dst():              # 2026-07-01 07:00 CEST = 05:00 UTC
def test_start_disabled_by_env(monkeypatch):            assert scheduler.start(None, None) is None
def test_loop_survives_failing_run(monkeypatch):        # run_refresh raises; with wake=0.01 the thread calls it again
```

- [ ] **Step 2: Run** `pytest tests/test_scheduler.py -v` — Expected: FAIL (module missing).
- [ ] **Step 3: Implement** the Produces items; start the thread from a FastAPI startup handler; add the info line; README section "Daily fetch".
- [ ] **Step 4: Run** `pytest -q` — Expected: all pass.
- [ ] **Step 5: Run the app for real**: restart the server, confirm in the log that the catch-up rule fires or not as expected, press ↻ Refresh, and check in the browser that the new league buttons appear with priced matches (only leagues with fixtures in the next few days will show), `model unproven` tags match the validation, and the info line shows the automatic fetch.
- [ ] **Step 6: Commit** `Fetch the board automatically every morning`
