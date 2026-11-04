# Statistics Tab and Self-Tuning Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a Statistics tab (My betting · Model · Tuning) and an automatic, guarded tuning loop for the model weight α (per league) and the club half-life.

**Architecture:** Pure computation lives in two new modules: `ninety_five/stats.py` (betting and model insight from rows) and `ninety_five/tuning.py` (decide whether a setting changes). Tuned values and the change log are new tables owned by `HistoryStore` in the same SQLite file. The server becomes the owner of α (resolved per match in `/api/evaluate`); the half-life is read by `service._fit` from a module-level value loaded at startup. Tuning runs in the scheduler thread right after the daily refresh, in its own lease. The page gains one tab with three sub-tabs and inline-SVG charts.

**Tech Stack:** Python 3, FastAPI, SQLite, numpy/pandas, pytest; vanilla JS + inline SVG in `ninety_five/static/index.html` (no build step, no new libraries).

**Spec:** `docs/superpowers/specs/2026-11-04-statistics-and-tuning-design.md`

## Global Constraints

- Never alter or delete existing bets; already-logged bets keep their `p_blend`/`ev`.
- App listens on 127.0.0.1 only; nothing new is exposed; no new network calls.
- Every figure shows its n. Faded (not hidden) below: bets 10; team matches 15.
- α: bounds [0.2, 0.8]; step ≤ 0.10 per change; overall needs ≥ 100 finished board matches with full 1X2 odds; per league ≥ 100 in that league.
- Half-life: one value for all club data sets; INTL not tuned; bounds [180, 1095] days; step ≤ ×/÷1.5; tested at most monthly.
- Better means: chosen on the older ~70% of evidence (by kick-off), confirmed on the newest ~30%; adopt only if mean paired log-loss improvement on the confirm set > its standard error (and > 0). Otherwise keep, and log "kept".
- Revert restores the old value and blocks that (setting, scope) for 30 days.
- Team flag: n ≥ 15 and |actual − expected points| > 2·sd; "overrates" when actual < expected.
- Team corrections are NOT built (deferred); Tuning sub-tab lists "Team corrections — not yet active".
- Charts: thin lines (1.5 px), expected dashed; light + dark theme; no horizontal scroll at 360 px; page max width 1280 px.
- Commits end with the session's attribution lines.

## Review Focus

1. Manual bets (no `match_key`, no `kickoff`, `ev` NULL) and `void`/open bets → stats must not crash; manual bets grouped under their dataset; void/open excluded from settled figures; "took profit" counts as settled.
2. Finished matches with partial odds (e.g. only "1" and "X") or snapshots without `xg` → skipped as α evidence / team-goal evidence, never an exception.
3. A tuning run that raises → the daily refresh still records success; the error is logged as a warning; no half-written change.
4. Revert then next morning's run → the reverted (setting, scope) does not change again for 30 days.
5. A league that has its own α plus a manual (dataset-only) evaluation → falls back to the overall α; response states which α was used.

---

### Task 1: Betting statistics

**Files:**
- Create: `ninety_five/stats.py`
- Modify: `ninety_five/history.py` (add `competitions`), `ninety_five/app.py` (endpoint)
- Test: `tests/test_stats_betting.py`

**Interfaces:**
- Produces: `stats.betting(bets: list[dict], competitions: dict[str, str]) -> dict`;
  `HistoryStore.competitions() -> dict[str, str]` (match_key → competition);
  `GET /api/stats/betting` → `stats.betting(store.list(), history.competitions())`.
- Return shape of `betting`:
  ```
  {"tiles": {"n_settled", "n_open", "staked", "pnl", "roi", "avg_return", "expected_pnl",
             "bets_per_day", "stake_per_day", "avg_clv", "n_clv"},
   "days":  [{"date": "YYYY-MM-DD", "bets": int, "stake": float}],          # by created, Europe/Copenhagen, ascending
   "curve": [{"date", "pnl_cum", "expected_cum", "n"}],                      # settled, by kickoff (fallback created), ascending
   "by_league" | "by_market" | "by_odds": [{"label", "n", "staked", "pnl", "roi", "expected_pnl", "thin": bool}]}
  ```
- Definitions: settled = status in {won, lost, took profit}. `avg_return` = pnl / n_settled. `expected_pnl` = Σ stake·ev over settled with ev. `bets_per_day`/`stake_per_day` = averages over days with ≥ 1 bet (all bets, open included). League label = `competitions[match_key]`, else `dataset`. Market groups: "1","X","2" → "1X2"; "Over …"/"Under …" → "Over/Under"; "BTTS …" → "BTTS"; "1X","12","X2" → "Double chance"; else "Other". Odds labels: "< 1.80", "1.80–2.49", "2.50–3.99", "4.00+". `thin` = n < 10. Groups sorted by n descending.

- [ ] **Step 1: Write the failing tests** (`tests/test_stats_betting.py`), using a helper `_b(**kw)` that fills a bet dict with defaults (`dataset="EUROPE"`, `match_key=None`, `kickoff=None`, `created="2026-11-01T08:00:00+00:00"`, `ev=0.05`, `closing_odds=None`):
  - `test_tiles_count_settled_only`: won(stake 10, odds 2.0, pnl 10), lost(10, pnl −10), took profit(10, pnl 4), void(10, pnl 0), open(10) → `n_settled == 3`, `n_open == 1`, `staked == 30`, `pnl == 4`, `roi == pytest.approx(4/30)`, `avg_return == pytest.approx(4/3)`.
  - `test_manual_bet_without_ev_or_kickoff`: one won bet with `ev=None`, `kickoff=None`, `match_key=None` → no exception, `expected_pnl == 0`, `curve[0]["date"] == "2026-11-01"`, `by_league[0]["label"] == "EUROPE"`.
  - `test_days_use_copenhagen_date`: created `2026-11-01T23:30:00+00:00` → day `"2026-11-02"`.
  - `test_per_day_averages_over_active_days`: 3 bets on day A (stake 10 each), 1 on day B (stake 20) → `bets_per_day == 2.0`, `stake_per_day == 25.0`.
  - `test_curve_cumulative_and_expected`: two settled bets in kickoff order, pnl +10 then −5, ev 0.1 stake 10 each → `[c["pnl_cum"] for c in curve] == [10, 5]`, `[c["expected_cum"] ...] == [1.0, 2.0]`.
  - `test_groups_market_odds_league`: markets "1", "Over 2.5", "BTTS No", "X2", "Handicap +1" map to the five labels; odds 1.79/1.80/2.50/4.00 map to the four labels; a bet whose `match_key` is in `competitions` uses that competition; `thin` true for n < 10.
- [ ] **Step 2:** Run `.venv/bin/pytest tests/test_stats_betting.py -q` — Expected: FAIL (`ModuleNotFoundError: ninety_five.stats`).
- [ ] **Step 3:** Implement `stats.betting` and `HistoryStore.competitions` (one `SELECT key, competition FROM matches`); add `GET /api/stats/betting` in `app.py`. Add `test_stats_betting_endpoint` to `tests/test_app.py` (empty DB → 200, `tiles.n_settled == 0`, `curve == []`).
- [ ] **Step 4:** Run `.venv/bin/pytest tests/test_stats_betting.py tests/test_app.py -q` — Expected: PASS.
- [ ] **Step 5:** Commit `stats: betting statistics endpoint`.

### Task 2: Model insight (teams and leagues)

**Files:**
- Modify: `ninety_five/history.py` (`finished` adds `"xg"`), `ninety_five/stats.py`, `ninety_five/app.py`
- Test: `tests/test_stats_model.py`, `tests/test_history.py`

**Interfaces:**
- Consumes: `HistoryStore.finished()` rows (now also `"xg": [home, away] | None` from the same pre-kick-off snapshot); `track._group_summary(group, matches)`.
- Produces: `stats.model_insight(matches: list[dict]) -> dict`; `GET /api/stats/model`.
  ```
  {"teams":   [{"team", "n", "exp_pts", "pts", "sd", "xg_for", "goals_for", "xg_against", "goals_against",
                "verdict": "overrates" | "underrates" | "in line" | "too few"}],   # sorted by |pts-exp_pts|/sd desc
   "leagues": [{"competition", "n", "exp_pts_home", "pts_home", "track": <track._group_summary("1X2", …)>}]}
  ```
- Points per match from the team's side: win 3, draw 1. `exp_pts` = Σ(3·p_win + p_draw); `sd` = sqrt(Σ Var) with Var = E[pts²] − E[pts]². Verdict "too few" if n < 15; else "overrates" if pts < exp_pts − 2·sd, "underrates" if pts > exp_pts + 2·sd, else "in line". Matches without all of "1","X","2" in probabilities are skipped; xg sums skip matches without `xg` (goal columns then cover only matches with xg).

- [ ] **Step 1: Write failing tests:**
  - `tests/test_history.py::test_finished_includes_xg`: save a board row with `xg [1.2, 0.8]`, set result → `finished()[0]["xg"] == [1.2, 0.8]`.
  - `test_expected_points_and_sd`: one match, home p=(0.5,0.3,0.2), result 1-0 → home team `exp_pts == 1.8`, `pts == 3`, `sd == pytest.approx(sqrt(0.5*9+0.3*1 - 1.8**2))`; away team `exp_pts == 0.9`.
  - `test_verdicts`: 15 matches where a team has p_win 0.6, p_draw 0.2 and loses all → "overrates"; same team with 14 matches → "too few"; 15 matches winning 9, drawing 3, losing 3 → "in line".
  - `test_skips_partial_probabilities_and_missing_xg`: a match with probabilities lacking "X" is ignored (n unchanged); a match with `xg None` counts for points but not for `xg_for`/`goals_for`.
  - `test_leagues_grouped_by_competition`: matches in two competitions → two league rows with correct n.
- [ ] **Step 2:** Run `.venv/bin/pytest tests/test_stats_model.py tests/test_history.py -q` — Expected: FAIL.
- [ ] **Step 3:** Implement; add `GET /api/stats/model` returning `stats.model_insight(history.finished())`; `test_stats_model_endpoint` in `tests/test_app.py` (empty → `{"teams": [], "leagues": []}`).
- [ ] **Step 4:** Run the same tests plus `tests/test_track.py` — Expected: PASS.
- [ ] **Step 5:** Commit `stats: model insight per team and league`.

### Task 3: Tuning store and the α decision

**Files:**
- Create: `ninety_five/tuning.py`
- Modify: `ninety_five/history.py` (schema + methods)
- Test: `tests/test_tuning_alpha.py`, `tests/test_history.py`

**Interfaces:**
- Schema (in `history.SCHEMA`):
  ```
  tuned (setting TEXT, scope TEXT, value REAL, updated TEXT, PRIMARY KEY (setting, scope))
  tuning_log (id INTEGER PK, at TEXT, setting TEXT, scope TEXT, old REAL, new REAL,
              action TEXT,   -- 'changed' | 'kept' | 'reverted'
              n INTEGER, loss_old REAL, loss_new REAL, se REAL, note TEXT, reverted_at TEXT)
  ```
  Settings: `"alpha"` (scope `"all"` or a competition name), `"half_life"` (scope `"club"`).
- `HistoryStore`: `tuned() -> dict[tuple[str,str], float]`; `set_tuned(setting, scope, value, at)`; `log_tuning(entry: dict) -> int`; `tuning_log(limit: int = 50) -> list[dict]` (newest first); `last_tuning(setting, scope, action=None) -> dict | None`; `revert(log_id: int, now: str) -> dict` (only for action 'changed' and not yet reverted; sets value back to `old`, stamps `reverted_at`, logs a 'reverted' row; raises `ValueError` otherwise); `blocked(setting, scope, now: pd.Timestamp) -> bool` (a 'reverted' row within 30 days).
- `tuning.py`:
  - `ALPHA_DEFAULT = 0.5`, `ALPHA_BOUNDS = (0.2, 0.8)`, `ALPHA_STEP = 0.10`, `ALPHA_MIN_N = 100`, `CONFIRM_SHARE = 0.3`.
  - `@dataclass Decision(setting, scope, old, new, action, n, loss_old, loss_new, se, note)`.
  - `alpha_evidence(matches: list[dict]) -> list[dict]`: from `history.finished()` rows keep those with odds for all of "1","X","2" and probabilities for all three; each → `{"kickoff", "competition", "pm": p_model[actual], "pk": p_market[actual]}` (market via `value.market_probabilities`).
  - `decide_alpha(evidence: list[dict], current: float, scope: str) -> Decision`: sort by kickoff; if n < 100 → action "waiting" (not logged). Else split older 70% / newest 30%; on older pick best α on grid 0.20..0.80 step 0.05 by mean log loss of `α·pm + (1−α)·pk`; clamp candidate to `current ± 0.10` and bounds; on confirm compute per-match `loss(current) − loss(candidate)`; adopt if mean > se (= std(ddof=1)/sqrt(n_confirm)) and mean > 0 → "changed", else "kept" (new = current). Round α to 2 decimals.

- [ ] **Step 1: Write failing tests** (`tests/test_tuning_alpha.py`, synthetic evidence built from a seeded RNG):
  - `test_waits_below_100`: 99 rows → action "waiting".
  - `test_moves_toward_model_when_model_better`: 400 rows where outcomes are drawn from pm (pk noisy) → action "changed", `new == 0.6` (step-capped from 0.5).
  - `test_keeps_when_no_clear_gain`: pm == pk for all rows → "kept", `new == 0.5`.
  - `test_respects_bounds`: current 0.8, model clearly better → new ≤ 0.8 and action "kept" (no change possible).
  - `test_evidence_skips_partial_odds`: finished rows with odds only for "1","X" are dropped.
  - `tests/test_history.py::test_revert_restores_and_blocks`: set alpha all 0.5→0.6 with a 'changed' log; `revert(id)` → `tuned()[("alpha","all")] == 0.5`, `blocked("alpha","all", now+29d)` True, `+31d` False; reverting twice raises `ValueError`.
- [ ] **Step 2:** Run `.venv/bin/pytest tests/test_tuning_alpha.py tests/test_history.py -q` — Expected: FAIL.
- [ ] **Step 3:** Implement schema, store methods, `tuning.py`.
- [ ] **Step 4:** Run same — Expected: PASS.
- [ ] **Step 5:** Commit `tuning: store, change log and the alpha decision`.

### Task 4: Server-owned α and the tuning API

**Files:**
- Modify: `ninety_five/app.py`, `ninety_five/tuning.py`
- Test: `tests/test_app.py`, `tests/test_tuning_alpha.py`

**Interfaces:**
- Consumes: Task 3 store methods, `decide_alpha`, `alpha_evidence`.
- Produces:
  - `tuning.alpha_for(tuned: dict, competition: str | None) -> tuple[float, str]` → (value, scope used): league value if present, else `("all")` value, else `ALPHA_DEFAULT` with scope "default".
  - `SettingsIn.alpha` removed (extra fields ignored). `/api/evaluate` resolves competition from `history.match(match_key)["competition"]` when `match_key` given, else `None`; passes `Settings(alpha=…, **rest)`; response adds `"alpha": {"value", "scope"}`.
  - `tuning.run_alpha(history, now: pd.Timestamp) -> list[Decision]`: overall decision, then one per competition with ≥ 100 rows; skips blocked (setting, scope); for "changed" writes `set_tuned` + log; for "kept" writes a log row; "waiting" writes nothing.
  - `GET /api/tuning` → `{"settings": [{"setting","scope","value","default": bool}], "progress": [{"setting","scope","n","needed","last_test": at|null,"last_action"}], "log": tuning_log(50), "deferred": ["Team corrections"]}`.
  - `POST /api/tuning/{log_id}/revert` → 200 with the new log row; 400 on `ValueError`.

- [ ] **Step 1: Write failing tests:**
  - `test_evaluate_uses_league_alpha` (`tests/test_app.py`): store a board match in competition "England 2", `set_tuned("alpha","England 2",0.7)`, evaluate with that `match_key` and full 1X2 odds → `resp["alpha"] == {"value": 0.7, "scope": "England 2"}` and a selection's `p_blend == pytest.approx(0.7*p_model + 0.3*p_market)`.
  - `test_evaluate_manual_falls_back_to_overall`: no `match_key`, `set_tuned("alpha","all",0.4)` → scope "all", value 0.4; with nothing tuned → `{"value": 0.5, "scope": "default"}`.
  - `test_client_alpha_ignored`: posting `settings.alpha = 1.0` → value still the tuned/default one.
  - `test_run_alpha_skips_blocked` (`tests/test_tuning_alpha.py`, with a tmp HistoryStore seeded with 150 finished matches that favour the model): after a revert today, `run_alpha` returns no decision for scope "all".
  - `test_tuning_endpoint_and_revert`: GET shape as above; revert of a 'changed' row → 200; second revert → 400.
- [ ] **Step 2:** Run `.venv/bin/pytest tests/test_app.py tests/test_tuning_alpha.py -q` — Expected: FAIL.
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** Run full suite `.venv/bin/pytest -q` — Expected: all pass (update `tests/test_value.py` only if it relied on `SettingsIn`; `value.Settings.alpha` stays).
- [ ] **Step 5:** Commit `alpha is owned by the server and resolved per league`.

### Task 5: Half-life tuning and the daily hook

**Files:**
- Modify: `ninety_five/model.py` (per-match loss), `ninety_five/service.py`, `ninety_five/tuning.py`, `ninety_five/scheduler.py`, `ninety_five/app.py` (load tuned values in `lifespan`)
- Test: `tests/test_tuning_half_life.py`, `tests/test_scheduler.py`

**Interfaces:**
- `model.match_losses_1x2(model: DixonColes, df: pd.DataFrame) -> np.ndarray` (per-row −log p(actual)); `log_loss_1x2` becomes its mean.
- `service.CLUB_HALF_LIFE: float` (module global, default `CLUB_PARAMS["half_life_days"]`); `service.club_params() -> dict` used everywhere `_fit` used `CLUB_PARAMS`; `service.set_club_half_life(days: float) -> None` sets it and clears the fitted-model cache.
- `tuning.HL_BOUNDS = (180, 1095)`, `HL_FACTOR = 1.5`, `HL_MONTHS = 6`, `HL_EVERY_DAYS = 30`.
- `tuning.decide_half_life(losses: dict[float, np.ndarray], kickoffs: np.ndarray, current: float) -> Decision`: `losses[h]` are per-match walk-forward losses for candidates {current/1.5, current, current·1.5} clamped to bounds, aligned with `kickoffs`; older 70% picks the best candidate, newest 30% confirms with the same SE rule; round to whole days.
- `tuning.half_life_losses(current: float, months: int = HL_MONTHS) -> tuple[dict[float, np.ndarray], np.ndarray]`: walk-forward over each `data.COUNTRIES` key for the last `months` monthly cuts (as in `validate.validate_country`, but every division, per-match losses); slow — never called in tests (tests monkeypatch it).
- `tuning.run_half_life(history, now) -> Decision | None`: None if the last half-life log row (any action) is < 30 days old or the setting is blocked; else decide, log, and on "changed" `set_tuned` + `service.set_club_half_life`.
- `tuning.run_all(history, now) -> list[Decision]`: `run_alpha` then `run_half_life`, under lease `"tuning"`; any exception is caught, logged as warning, returns what finished.
- `scheduler.start` loop: after a successful auto `run_refresh`, call `tuning.run_all(history, now)`.
- `app.lifespan`: on startup apply a stored half-life via `service.set_club_half_life`.

- [ ] **Step 1: Write failing tests:**
  - `test_decide_half_life_moves_one_step`: candidate 547.5 has clearly lower losses → "changed", new 548; ties → "kept".
  - `test_half_life_bounds`: current 1095 → candidates exclude > 1095.
  - `test_run_half_life_monthly` (monkeypatch `half_life_losses`): first call decides; second call 10 days later returns None; 31 days later decides again.
  - `test_run_all_swallows_errors`: monkeypatch `run_alpha` to raise → `run_all` returns `[]`, no exception.
  - `tests/test_scheduler.py::test_tuning_runs_after_auto_refresh` (monkeypatch `service.run_refresh` and `tuning.run_all`): one loop iteration calls `run_all` once; when `run_refresh` returns None it is not called.
  - `test_set_club_half_life_clears_cache`: after `set_club_half_life(500)`, `service.club_params()["half_life_days"] == 500` and the cache is empty.
- [ ] **Step 2:** Run `.venv/bin/pytest tests/test_tuning_half_life.py tests/test_scheduler.py -q` — Expected: FAIL.
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** Run full suite `.venv/bin/pytest -q` — Expected: all pass.
- [ ] **Step 5:** Commit `tuning: monthly half-life test and daily run after the refresh`.

### Task 6: Statistics tab on the page

**Files:**
- Modify: `ninety_five/static/index.html`, `README.md` (short "Statistics and tuning" section)
- Test: `tests/test_app.py` (page assertions) + browser check on the test server (:8001, copy of the DB)

**Interfaces:**
- Consumes: `/api/stats/betting`, `/api/stats/model`, `/api/track-record`, `/api/tuning`, `/api/tuning/{id}/revert`, `/api/evaluate` response `alpha`.
- Produces (page):
  - Top-level tab `<button class="tab" data-tab="stats">Statistics</button>` and pane `#tab-stats`; league chips bar hidden on this tab.
  - Sub-tabs styled like `.chip`: `data-sub="betting" | "model" | "tuning"`, last choice remembered in `localStorage` key `statsSub` (wrapped in try/catch like the existing helper).
  - My betting: KPI tiles (`.kpis`) for every `tiles` field (with n); SVG profit curve (actual solid, expected dashed, stroke-width 1.5, `currentColor`/theme tokens, zero line); SVG stake-per-day bars with bets-per-day dots; three tables (league, market, odds range) with `thin` rows `opacity: .45`.
  - Model: teams table (Team, Matches, Expected pts, Actual pts, xG for/against vs goals, verdict badge), leagues table; the existing Track record block moves here (remove it from Bet log).
  - Tuning: settings table, progress bars "n of needed", log table with Revert buttons (no `confirm()` dialog — button turns into "Revert? Yes / No" inline), "Team corrections — not yet active" line.
  - Settings dialog: the `#alpha` input is removed; a read-only line `Model weight: 0.50 (tuned automatically — Statistics → Tuning)`, listing league values when any differ, filled from `/api/tuning`. `alpha` dropped from the settings payload and from the persisted settings list.

- [ ] **Step 1: Write failing page tests** in `tests/test_app.py`:
  - `test_page_has_statistics_tab`: HTML contains `data-tab="stats"`, `data-sub="betting"`, `data-sub="model"`, `data-sub="tuning"`.
  - `test_page_has_no_alpha_input`: `'id="alpha"'` not in HTML; `"tuned automatically"` in HTML.
  - `test_track_record_lives_in_statistics`: the `id="track"` element appears after `id="tab-stats"` and not inside `#tab-bets` (check index order).
- [ ] **Step 2:** Run `.venv/bin/pytest tests/test_app.py -q` — Expected: FAIL.
- [ ] **Step 3:** Implement the page changes and the README section.
- [ ] **Step 4:** Run `.venv/bin/pytest -q` — Expected: all pass.
- [ ] **Step 5: Browser check** on `scratchpad/test_server.py` (:8001, fresh copy of `data/bets.sqlite`): each sub-tab renders with the real data without console errors; at 360 px width `document.documentElement.scrollWidth <= 360`; dark and light theme both legible; Revert flow works on a seeded 'changed' row in the copy; evaluating a match shows the server α. Do not type odds into the live app.
- [ ] **Step 6:** Commit `Statistics tab: my betting, model, tuning`.
