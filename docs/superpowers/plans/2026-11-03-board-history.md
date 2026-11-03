# Board History, Form and Track Record Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Keep the fixture board between reloads, snapshot every board match (model prices + form) and every typed Danske Spil price, fetch actual results, and show a track record comparing model vs Danske Spil vs result.

**Architecture:** A new `HistoryStore` (SQLite, same file as the bet log) owns matches, snapshots, typed odds and board metadata. `service.py` builds fresh board rows (now with all-market probabilities and form) and a `refresh_board` pipeline that saves them, fetches results from football-data.org and ESPN, and auto-settles bets. The API serves the stored board on GET and refreshes on POST; the page shows the stored board immediately and refreshes in the background when stale. A new `track.py` computes track-record metrics.

**Tech Stack:** Python 3.14, FastAPI, pandas, sqlite3, pytest; vanilla JS single page (`ninty_five/static/index.html`).

**Spec:** `docs/superpowers/specs/2026-11-03-board-history-design.md`

## Global Constraints

- Storage lives in `data/bets.sqlite` (the existing `store.DB_PATH`); existing `bets` rows must survive unchanged — migrations only add nullable columns.
- Match keys: `fd:<football-data match id>` for club fixtures, `espn:<ESPN event id>` for national-team fixtures. Manual matches have no key.
- Stale threshold for the stored board: **6 hours**. Results look-back: **14 days**.
- Snapshot dedupe: skip when probabilities rounded to **4 decimals** and form equal the match's previous snapshot.
- Typed odds: store only values > 1, only when different from the last stored price for that match + market.
- Stored odds are **never** fed into evaluation; reopened matches show empty boxes with a grey hint `last entered 2.70 at 14:05` (local time, HH:MM).
- Form = last **5** results before kick-off, most recent first, each `{date, opponent, venue: "H"|"A"|"N", gf, ga, result: "W"|"D"|"L"}`.
- Minimum odds are derived on read from stored probabilities and the current min edge; never stored.
- History, result or source failures must never stop the stored board from being returned; they become warnings.
- No network in tests (monkeypatch sources, as `tests/test_fixtures.py` does).

## Review Focus

1. **Typing fast in a fold-down** (debounced evaluations with intermediate values like `2` then `2.7`): only the final changed value per market should be stored — test in Task 2 (`test_record_odds_skips_unchanged_and_invalid`).
2. **A board match postponed or dropped by the source**: it must vanish from the board once kick-off passes and must never block result fetching for the others — test in Task 3 (`test_board_hides_past_kickoffs`) and Task 5 (`test_results_ignores_cancelled`).
3. **Extra time / penalties in national-team matches**: the stored result must be the 90-minute score; if it can't be determined safely (own goals in an AET match), store no result rather than a wrong one — test in Task 5 (`test_goals_90_espn_aet`).
4. **First run with an empty database / a fresh install**: GET board returns `[]` with `fetched_at: null` and the page triggers a refresh — test in Task 4 (`test_get_board_empty_db`).
5. **Changing "Min edge for list" or "Next days" after a stored fetch**: the board must reflect the new edge without refetching, and a larger day window must trigger a refresh — test in Task 4 (`test_stored_board_min_odds_follow_edge`) and Task 6 (manual browser check).

---

### Task 1: HistoryStore — matches, snapshots, board metadata

**Files:**
- Create: `ninty_five/history.py`
- Test: `tests/test_history.py`

**Interfaces:**
- Consumes: `store.DB_PATH`.
- Produces:
  - `class HistoryStore(path: Path | str = DB_PATH)` — creates tables `matches`, `snapshots`, `odds_entries`, `meta` if missing.
  - `save_board(rows: list[dict], fetched_at: str) -> int` — upserts each row into `matches` (key, dataset, match_id, competition, detail, stage, kickoff, home, away, neutral; sets `first_seen` once, `last_seen` every time) and writes a snapshot when not a duplicate; stores `fetched_at` in `meta` under `board_fetched_at`. Returns snapshots written. Rows with `rated == False` are upserted into `matches` but get no snapshot.
  - Board row shape (input to `save_board`, output of `board`): `key, dataset, match_id, kickoff (ISO UTC), competition, detail, stage, home, away, neutral, rated, xg, data_matches, leagues, probabilities (all markets), model_as_of, form: {"home": [...], "away": [...]}`.
  - `board(days: int, now: pd.Timestamp | None = None) -> list[dict]` — matches with `now <= kickoff < now.normalize() + days`, each merged with its latest snapshot (unrated if none), sorted by kickoff.
  - `fetched_at() -> str | None`.

- [ ] **Step 1: Write the failing tests**

```python
def _row(key="fd:1", kickoff="2026-11-10T20:00:00+00:00", p1=0.4, form=None, rated=True):
    return {"key": key, "dataset": "EUROPE", "match_id": 1, "kickoff": kickoff, "competition": "England 2",
            "detail": "", "stage": "", "home": "A", "away": "B", "neutral": False, "rated": rated,
            "xg": [1.2, 1.0], "data_matches": [100, 100], "leagues": ["", ""], "model_as_of": "2026-11-02",
            "probabilities": {"1": p1, "X": 0.3, "2": 0.7 - p1}, "form": form or {"home": [], "away": []}}

def test_save_board_dedupes_snapshots(tmp_path):
    h = HistoryStore(tmp_path / "h.sqlite")
    assert h.save_board([_row()], "2026-11-03T10:00:00+00:00") == 1
    assert h.save_board([_row(p1=0.40000001)], "2026-11-03T11:00:00+00:00") == 0   # same at 4 dp
    assert h.save_board([_row(p1=0.41)], "2026-11-03T12:00:00+00:00") == 1
    assert h.fetched_at() == "2026-11-03T12:00:00+00:00"

def test_board_returns_latest_snapshot(tmp_path):
    h = HistoryStore(tmp_path / "h.sqlite")
    h.save_board([_row(p1=0.40)], "t1"); h.save_board([_row(p1=0.45)], "t2")
    [r] = h.board(days=14, now=pd.Timestamp("2026-11-03T12:00Z"))
    assert r["probabilities"]["1"] == pytest.approx(0.45) and r["rated"] and r["key"] == "fd:1"

def test_board_hides_past_kickoffs(tmp_path):
    h = HistoryStore(tmp_path / "h.sqlite")
    h.save_board([_row(key="fd:1", kickoff="2026-11-01T20:00:00+00:00"), _row(key="fd:2")], "t")
    assert [r["key"] for r in h.board(days=14, now=pd.Timestamp("2026-11-03T12:00Z"))] == ["fd:2"]

def test_unrated_row_has_no_snapshot(tmp_path):
    h = HistoryStore(tmp_path / "h.sqlite")
    assert h.save_board([_row(rated=False)], "t") == 0
    [r] = h.board(days=14, now=pd.Timestamp("2026-11-03T12:00Z"))
    assert r["rated"] is False
```

- [ ] **Step 2: Run** `.venv/bin/pytest tests/test_history.py -v` — Expected: FAIL (`ModuleNotFoundError: ninty_five.history`).
- [ ] **Step 3: Implement `HistoryStore` in `ninty_five/history.py`** following `store.BetStore`'s connection style (`sqlite3.Row`, `with self._conn()`). Store `probabilities`, `xg`, `data_matches`, `leagues`, `form` as JSON text in `snapshots`; dedupe by comparing `json.dumps({k: round(v, 4)}, sort_keys=True)` + form JSON with the match's latest snapshot. `taken_at` = the `fetched_at` passed in.
- [ ] **Step 4: Run** `.venv/bin/pytest tests/test_history.py -v` — Expected: PASS.
- [ ] **Step 5: Commit** `git add ninty_five/history.py tests/test_history.py && git commit -m "Add history store for board matches and snapshots"`

---

### Task 2: Typed odds storage

**Files:**
- Modify: `ninty_five/history.py`
- Test: `tests/test_history.py`

**Interfaces:**
- Consumes: `HistoryStore` (Task 1).
- Produces:
  - `record_odds(match_key: str | None, dataset: str, home: str, away: str, selections: list[dict], entered_at: str) -> int` — `selections` are `value.Selection.to_dict()` dicts (`market, odds, p_model, p_market, p_blend`). Writes one `odds_entries` row per selection whose `odds > 1` and differs from the latest stored odds for the same identity + market. Identity = `match_key` when given, else `(dataset, home, away)`. Returns rows written.
  - `last_odds(match_key: str | None, dataset: str, home: str, away: str) -> dict[str, dict]` — `{market: {"odds": float, "entered_at": str}}` for the latest entry per market.

- [ ] **Step 1: Write the failing tests**

```python
def _sel(market, odds):
    return {"market": market, "odds": odds, "p_model": 0.4, "p_market": 0.35, "p_blend": 0.375}

def test_record_odds_skips_unchanged_and_invalid(tmp_path):
    h = HistoryStore(tmp_path / "h.sqlite")
    assert h.record_odds("fd:1", "EUROPE", "A", "B", [_sel("1", 2.0)], "t1") == 1
    assert h.record_odds("fd:1", "EUROPE", "A", "B", [_sel("1", 2.0), _sel("X", 1.0)], "t2") == 0
    assert h.record_odds("fd:1", "EUROPE", "A", "B", [_sel("1", 2.7)], "t3") == 1
    assert h.last_odds("fd:1", "EUROPE", "A", "B") == {"1": {"odds": 2.7, "entered_at": "t3"}}

def test_manual_match_odds_keyed_by_teams(tmp_path):
    h = HistoryStore(tmp_path / "h.sqlite")
    h.record_odds(None, "INTL", "Denmark", "Scotland", [_sel("Over 2.5", 2.1)], "t1")
    assert h.last_odds(None, "INTL", "Denmark", "Scotland")["Over 2.5"]["odds"] == 2.1
    assert h.last_odds(None, "INTL", "Scotland", "Denmark") == {}
```

- [ ] **Step 2: Run** `.venv/bin/pytest tests/test_history.py -v` — Expected: the two new tests FAIL (`AttributeError`).
- [ ] **Step 3: Implement** `record_odds` and `last_odds` in `ninty_five/history.py`.
- [ ] **Step 4: Run** `.venv/bin/pytest tests/test_history.py -v` — Expected: PASS.
- [ ] **Step 5: Commit** `git commit -am "Store typed Danske Spil odds"` (add the test file too).

---

### Task 3: Form and fresh board rows

**Files:**
- Modify: `ninty_five/service.py`
- Test: `tests/test_service_board.py` (create)

**Interfaces:**
- Consumes: `data.COLUMNS` frame shape (`date, home, away, hg, ag, neutral, competition`).
- Produces:
  - `training_data(key: str) -> pd.DataFrame` — the frame the cached model for `key` was fitted on. Change `_models` to cache `(fitted_at, model, df)`; `fit_dataset` returns the frame alongside the model via a new `_fit(key, refresh) -> tuple[DixonColes, pd.DataFrame]` (keep `fit_dataset(key, refresh) -> DixonColes` as a thin wrapper for the CLI).
  - `form(df: pd.DataFrame, team: str, before: pd.Timestamp, n: int = 5) -> list[dict]` — items as in Global Constraints; `before` is tz-aware UTC, `df.date` is naive UTC; venue `"N"` when `neutral`, else `"H"`/`"A"`.
  - `build_board(days: int) -> tuple[list[dict], list[str]]` — replaces today's `board` internals: rows in the Task 1 board-row shape, with `key` (`fd:`/`espn:`), `probabilities` for **all** markets from `markets(...)`, `model_as_of`, and `form` for both teams. Warnings/failure semantics as today's `board`.
  - `with_min_odds(rows: list[dict], min_edge: float) -> list[dict]` — adds `min_odds` for `BOARD_MARKETS` to rated rows.
  - Keep `board(days, min_edge) -> tuple[list[dict], list[str]]` = `build_board` + `with_min_odds` (CLI keeps working).

- [ ] **Step 1: Write the failing tests**

```python
def _df(rows):
    return pd.DataFrame(rows, columns=["date", "home", "away", "hg", "ag", "neutral", "competition"])

def test_form_last_five_before_kickoff_most_recent_first():
    df = _df([(pd.Timestamp(f"2026-10-{d:02d}"), "A", "B", 2, 1, False, "L") for d in range(1, 8)]
             + [(pd.Timestamp("2026-10-20"), "C", "A", 0, 0, True, "L"),
                (pd.Timestamp("2026-11-05"), "A", "D", 9, 0, False, "L")])   # after kick-off: excluded
    f = service.form(df, "A", before=pd.Timestamp("2026-11-01T20:00Z"))
    assert len(f) == 5
    assert f[0] == {"date": "2026-10-20", "opponent": "C", "venue": "N", "gf": 0, "ga": 0, "result": "D"}
    assert f[1]["venue"] == "H" and f[1]["result"] == "W" and f[1]["date"] == "2026-10-07"

def test_form_unknown_team_is_empty():
    assert service.form(_df([]), "Nobody", before=pd.Timestamp("2026-11-01T00:00Z")) == []

def test_with_min_odds():
    rows = service.with_min_odds([{"rated": True, "probabilities": {m: 0.5 for m in service.BOARD_MARKETS}},
                                  {"rated": False}], 0.08)
    assert rows[0]["min_odds"]["1"] == pytest.approx(2.16) and "min_odds" not in rows[1]
```

Also update `tests/test_fixtures.py::test_europe_board_and_auto_settle` and `tests/test_intl_fixtures.py::test_board_keeps_clubs_when_intl_source_fails` to monkeypatch `club_board`/`intl_board` as before and assert `board[0]["key"] == "fd:9001"` and `"form" in board[0]`.

- [ ] **Step 2: Run** `.venv/bin/pytest -q` — Expected: new tests FAIL (`AttributeError: form`).
- [ ] **Step 3: Implement** in `ninty_five/service.py`. Club rows: `key=f"fd:{match_id}"`. National rows: `key=f"espn:{event_id}"` (`intl_fixtures.upcoming` already returns `event_id`). Form uses `training_data(row dataset)`.
- [ ] **Step 4: Run** `.venv/bin/pytest -q` — Expected: all PASS.
- [ ] **Step 5: Commit** `git commit -am "Add team form and full probabilities to board rows"` (add the new test file).

---

### Task 4: Stored board API and recording odds on evaluate

**Files:**
- Modify: `ninty_five/service.py`, `ninty_five/app.py`, `ninty_five/cli.py`
- Test: `tests/test_app.py` (create; use `fastapi.testclient.TestClient` and monkeypatch `app.history`, `app.store`, `service.build_board`)

**Interfaces:**
- Consumes: `HistoryStore.save_board/board/fetched_at/record_odds/last_odds` (Tasks 1–2), `service.build_board/with_min_odds` (Task 3).
- Produces:
  - `service.refresh_board(history: HistoryStore, bets: BetStore, days: int, now: pd.Timestamp | None = None) -> dict` — `{"warnings": [...], "auto_settled": [...], "snapshots": int}`; calls `build_board(days)`, `history.save_board(rows, now.isoformat())`, then `update_results(...)` (Task 5; until then a no-op stub `update_results(history, bets, now) -> list[int]` returning `[]`).
  - `GET /api/fixtures?days=7&min_edge=0.08` → `{"fixtures": with_min_odds(history.board(days), min_edge), "fetched_at": str|None, "stale": bool}`; `stale` = no fetch or older than 6 h. Never calls external sources.
  - `POST /api/fixtures/refresh?days=7&min_edge=0.08` → refresh then the GET payload plus `warnings` and `auto_settled`. If both sources fail: HTTP 502 as today.
  - `EvaluateIn` gains `match_key: str | None = None`. `/api/evaluate` calls `history.record_odds(req.match_key, req.dataset, req.home, req.away, selections, now)` after evaluating (wrapped so a failure is logged, never raised) and returns `last_odds` in the response as `"last_odds"`, **read before** recording so the hint shows the previous visit's prices.
  - `BetIn` gains `match_key: str | None = None` (stored by Task 5's migration).
  - CLI `fixtures` command: unchanged output; keep using `service.board`.

- [ ] **Step 1: Write the failing tests**

```python
def test_get_board_empty_db(client):
    r = client.get("/api/fixtures").json()
    assert r == {"fixtures": [], "fetched_at": None, "stale": True}

def test_refresh_then_get_serves_stored(client, monkeypatch):
    monkeypatch.setattr(service, "build_board", lambda days: ([ROW], []))
    assert client.post("/api/fixtures/refresh").json()["fixtures"][0]["key"] == "fd:1"
    monkeypatch.setattr(service, "build_board", lambda days: (_ for _ in ()).throw(AssertionError("no fetch on GET")))
    r = client.get("/api/fixtures").json()
    assert r["fixtures"][0]["key"] == "fd:1" and r["stale"] is False

def test_stored_board_min_odds_follow_edge(client, monkeypatch):
    monkeypatch.setattr(service, "build_board", lambda days: ([ROW], []))
    client.post("/api/fixtures/refresh")
    a = client.get("/api/fixtures?min_edge=0.08").json()["fixtures"][0]["min_odds"]["1"]
    b = client.get("/api/fixtures?min_edge=0.0").json()["fixtures"][0]["min_odds"]["1"]
    assert a == pytest.approx(b * 1.08)

def test_evaluate_records_odds_and_returns_previous(client):
    body = {"dataset": "EUROPE", "home": "A", "away": "B", "match_key": "fd:1", "odds": {"1": 2.7}}
    assert client.post("/api/evaluate", json=body).json()["last_odds"] == {}
    assert client.post("/api/evaluate", json=body).json()["last_odds"]["1"]["odds"] == 2.7
```

(`ROW` = a rated board row with `kickoff` in the future relative to the real clock, e.g. `pd.Timestamp.now(tz="UTC") + 1 day`, and `probabilities` for all `BOARD_MARKETS`. The `client` fixture builds `TestClient(app.app)` with `app.history = HistoryStore(tmp)`, `app.store = BetStore(tmp)`, and `service.get_model` monkeypatched to a model fitted on a tiny frame containing teams A and B — reuse `_fake_europe` style data.)

- [ ] **Step 2: Run** `.venv/bin/pytest tests/test_app.py -v` — Expected: FAIL.
- [ ] **Step 3: Implement** in `service.py` and `app.py` (module-level `history = HistoryStore()` next to `store`).
- [ ] **Step 4: Run** `.venv/bin/pytest -q` — Expected: all PASS.
- [ ] **Step 5: Commit** `git commit -am "Serve the stored board and record typed odds"` (add the test file).

---

### Task 5: Results and auto-settlement by match key

**Files:**
- Modify: `ninty_five/intl_fixtures.py`, `ninty_five/history.py`, `ninty_five/store.py`, `ninty_five/service.py`
- Test: `tests/test_intl_fixtures.py`, `tests/test_history.py`, `tests/test_fixtures.py`

**Interfaces:**
- Consumes: `HistoryStore` (Task 1), `refresh_board` (Task 4), `fixtures.recent_results`.
- Produces:
  - `intl_fixtures.goals_90(event: dict) -> tuple[int, int] | None` — `STATUS_FULL_TIME` → competitor scores; `STATUS_FINAL_AET`/`STATUS_FINAL_PEN` → count `competitions[0].details[]` with `scoringPlay` true, `shootout` false and minute ≤ 90 (minute = integer before the first `'` of `clock.displayValue`, so `90'+3'` counts and `95'` doesn't), credited by `team.id`; if any such goal has `ownGoal` true, return `None` (attribution unverified). Other statuses → `None`.
  - `intl_fixtures.results(since: pd.Timestamp, now: pd.Timestamp | None = None) -> pd.DataFrame` — columns `event_id, hg, ag, status` for events in `[since, now]` across `SLUGS` (month form, same `_get` cache), `status` = ESPN status name; rows where `goals_90` is `None` have NaN goals.
  - `HistoryStore.pending_results(now: pd.Timestamp, lookback_days: int = 14) -> list[dict]` — `{key, dataset, kickoff}` for matches with kickoff in `[now - 14 days, now)` and no result and status not cancelled.
  - `HistoryStore.set_result(key: str, hg: int | None, ag: int | None, status: str) -> None`.
  - `BetStore`: add nullable `match_key TEXT` via `MIGRATIONS`; `add` stores it; `auto_settle_keys(results: dict[str, tuple[int, int]]) -> list[int]` settles open bets by `match_key`. Existing `auto_settle` (by `match_id`) stays.
  - `service.update_results(history, bets, now) -> list[int]` — replaces the Task 4 stub: for pending `fd:` keys use `fixtures.recent_results(days=10)` (by `match_id`), for `espn:` keys use `intl_fixtures.results(since=oldest pending kickoff)`; cancelled/postponed ESPN statuses (`STATUS_CANCELED`, `STATUS_POSTPONED`) → `set_result(key, None, None, status)`; then `bets.auto_settle(...)` + `bets.auto_settle_keys(...)`; returns settled bet ids. Each source wrapped so one failing doesn't stop the other.

- [ ] **Step 1: Write the failing tests**

```python
def test_goals_90_espn_aet():
    ev = {"status": {"type": {"name": "STATUS_FINAL_AET"}}, "competitions": [{
        "competitors": [{"homeAway": "home", "team": {"id": "1"}, "score": "2"},
                        {"homeAway": "away", "team": {"id": "2"}, "score": "1"}],
        "details": [_goal("1", "30'"), _goal("2", "90'+2'"), _goal("1", "105'")]}]}
    assert intl_fixtures.goals_90(ev) == (1, 1)
    ev["competitions"][0]["details"].append(_goal("2", "60'", own=True))
    assert intl_fixtures.goals_90(ev) is None

def test_goals_90_full_time_uses_score():
    ev = SAMPLE["events"][1]      # completed Azerbaijan 0-0 Liechtenstein
    assert intl_fixtures.goals_90(ev) == (0, 0)

def test_results_ignores_cancelled(tmp_path):     # in test_history.py
    h = HistoryStore(tmp_path / "h.sqlite")
    h.save_board([_row(key="espn:9", kickoff="2026-11-01T20:00:00+00:00")], "t")
    h.set_result("espn:9", None, None, "STATUS_CANCELED")
    assert h.pending_results(pd.Timestamp("2026-11-03T12:00Z")) == []

def test_update_results_settles_bets_by_key(tmp_path, monkeypatch):   # in test_fixtures.py
    # history with one past espn match, one open bet with match_key "espn:9";
    # intl_fixtures.results monkeypatched to return event 9 as 2-0 STATUS_FULL_TIME;
    # fixtures.recent_results monkeypatched to an empty frame.
    assert service.update_results(h, bets, now) == [bid]
    assert bets.list()[0]["status"] == "won"      # market "1"
```

(`_goal(team_id, display, own=False)` builds `{"scoringPlay": True, "shootout": False, "ownGoal": own, "team": {"id": team_id}, "clock": {"displayValue": display}}`.)

- [ ] **Step 2: Run** `.venv/bin/pytest -q` — Expected: new tests FAIL.
- [ ] **Step 3: Implement** as specified above.
- [ ] **Step 4: Run** `.venv/bin/pytest -q` — Expected: all PASS.
- [ ] **Step 5: Commit** `git commit -am "Fetch results for board matches and settle bets by match key"`

---

### Task 6: Page — stored board, background refresh, form, odds hints

**Files:**
- Modify: `ninty_five/static/index.html`

**Interfaces:**
- Consumes: `GET /api/fixtures`, `POST /api/fixtures/refresh` (Task 4), board rows with `key` and `form` (Task 3), `last_odds` in the evaluate response (Task 4), `match_key` on evaluate and bet bodies.
- Produces: UI only.

- [ ] **Step 1: On load** call `GET /api/fixtures` with current days/edge and render immediately; if `stale` (or the response is empty), call `POST /api/fixtures/refresh` in the background, keeping the stored board visible with "Refreshing…" in the info line, then re-render (keep the open fold-down and typed odds if its `key` is still on the board — key `open` state by `f.key` instead of the board index). Info line shows `updated HH:MM` from `fetched_at`. "Fetch matches" calls refresh. Changing "Next days" to a larger value triggers a refresh; changing "Min edge for list" re-GETs (no refresh).
- [ ] **Step 2: Form in the fold-down** — under the odds groups, one line per team: team name then five small W/D/L badges (green/grey/red using existing `--good`/`--muted`/`--bad`), each with a `title` of `2026-10-25 · H v Opponent 2-1`. National teams: append `as of <date of most recent form game>` in muted text.
- [ ] **Step 3: Odds hints** — each box's `<small>` shows `min X.XX` as now; when `last_odds[market]` exists append ` · last 2.70 at 14:05` (local HH:MM). Boxes stay empty; hints never feed `readOdds()`.
- [ ] **Step 4: Send `match_key`** (`open.fixture?.key ?? null`) on `/api/evaluate` and `/api/bets`.
- [ ] **Step 5: Verify in the browser** (server restarted): reload shows the board instantly with `updated HH:MM`; open Blackburn v Stoke, type `2.70`, reload, reopen → box empty with `last 2.70 at HH:MM`; form badges show with tooltips; raising "Next days" from 7 to 14 refreshes; no console errors.
- [ ] **Step 6: Commit** `git commit -am "Show stored board instantly with form and last-entered odds"`

---

### Task 7: Track record

**Files:**
- Create: `ninty_five/track.py`
- Modify: `ninty_five/history.py`, `ninty_five/app.py`, `ninty_five/static/index.html`, `README.md`
- Test: `tests/test_track.py`

**Interfaces:**
- Consumes: `HistoryStore` (Tasks 1–2, 5), `value.market_probabilities(odds: dict[str, float]) -> dict[str, float | None]`, bet log.
- Produces:
  - `HistoryStore.finished(competition: str | None = None) -> list[dict]` — matches with a result and ≥ 1 snapshot taken before kickoff: match fields + `hg, ag` + `probabilities` from the **last snapshot before kickoff** + `odds` = `{market: odds}` from the **last entry per market before kickoff** (keyed matches only).
  - `track.summary(matches: list[dict]) -> dict` with, for `"1X2"` (outcomes `1/X/2`) and `"OU25"` (`Over 2.5/Under 2.5`) each: `n`, `model_logloss`, `n_compared`, `model_logloss_compared`, `market_logloss_compared`, `model_closer`, `market_closer` (shares of compared matches; ties count for neither), `calibration`: list of 10 bands `{lo, hi, n, avg_pred, freq}` over every outcome probability of every match (empty bands included with `n=0`). "Compared" = matches whose entered odds cover the whole market; market probabilities from `value.market_probabilities`.
  - `GET /api/track-record?competition=` → `{"summary": track.summary(...), "matches": [latest 50, newest first, each with model 1/X/2, market 1/X/2 or null, result "2-1", bets: [market@odds]]}`.

- [ ] **Step 1: Write the failing tests**

```python
def _m(hg, ag, p, odds=None):
    return {"hg": hg, "ag": ag, "probabilities": {"1": p[0], "X": p[1], "2": p[2], "Over 2.5": 0.5, "Under 2.5": 0.5},
            "odds": odds or {}}

def test_summary_logloss_and_head_to_head():
    ms = [_m(1, 0, (0.5, 0.3, 0.2), {"1": 2.5, "X": 3.2, "2": 3.0}),   # market ~0.38 on home: model closer
          _m(0, 2, (0.5, 0.3, 0.2), {"1": 1.5, "X": 4.0, "2": 7.0}),   # market lower on away: model closer
          _m(1, 1, (0.4, 0.3, 0.3))]                                   # no odds: not compared
    s = track.summary(ms)["1X2"]
    assert s["n"] == 3 and s["n_compared"] == 2
    assert s["model_logloss"] == pytest.approx(-(math.log(0.5) + math.log(0.2) + math.log(0.3)) / 3)
    assert s["model_closer"] == 1.0 and s["market_closer"] == 0.0

def test_calibration_bands():
    s = track.summary([_m(1, 0, (0.55, 0.25, 0.2))])["1X2"]["calibration"]
    band = next(b for b in s if b["lo"] == 0.5)
    assert band["n"] == 1 and band["freq"] == 1.0 and band["avg_pred"] == pytest.approx(0.55)
    assert len(s) == 10

def test_summary_empty():
    assert track.summary([])["1X2"]["n"] == 0
```

Plus in `tests/test_history.py`: `test_finished_uses_last_snapshot_and_odds_before_kickoff` — snapshots at `t < kickoff` (p1 0.40) and `t > kickoff` (p1 0.60), odds entered before (2.5) and after (2.9) kickoff; result 1-0 → `probabilities["1"] == 0.40` and `odds["1"] == 2.5`.

- [ ] **Step 2: Run** `.venv/bin/pytest tests/test_track.py tests/test_history.py -v` — Expected: FAIL.
- [ ] **Step 3: Implement** `history.finished`, `track.summary` (log loss with probabilities clipped to `[1e-6, 1]`), and the API route.
- [ ] **Step 4: Page section "Track record"** between the board and the bet log: league buttons reuse the board's chips state; headline tiles per market group (matches, model log loss, "model closer X% · Danske Spil closer Y%" with `n_compared`), a calibration table, and the matches table. Empty state: "No finished matches yet. Results appear here after the matches you've seen on the board are played." Load on page load and after each refresh.
- [ ] **Step 5: README** — in the board section, add a short "History and track record" paragraph: board kept between reloads, refreshed after 6 hours or on *Fetch matches*, every match snapshotted with form, typed odds stored, results fetched automatically, Track record explained in two sentences.
- [ ] **Step 6: Run** `.venv/bin/pytest -q` — Expected: all PASS. Browser check: Track record shows the empty state without errors.
- [ ] **Step 7: Commit** `git commit -am "Add track record comparing model, Danske Spil and results"` (add new files).
