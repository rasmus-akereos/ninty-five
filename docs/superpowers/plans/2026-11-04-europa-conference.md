# Europa League and Conference League Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Put Europa League and Conference League matches on the board, priced by a new `UEFA` club model trained on 20 domestic top flights plus three seasons of European results, with a "thin data" tag for clubs rated from European matches only.

**Architecture:** A new module `ninty_five/uefa.py` loads domestic results (football-data.co.uk, via `data.load_club_league`) and European results (ESPN scoreboards, cached on disk per month), maps ESPN club names to domestic names, and returns `(df, groups, info)` for `DixonColes`. `service` fits it as data set `UEFA`, adds a `uefa_board` source and the EL/ECL result slugs. `validate.py` gains a walk-forward check stored like the leagues' and read by `unproven`.

**Tech Stack:** Python 3, pandas, numpy, FastAPI, pytest; single-page `static/index.html`.

**Spec:** `docs/superpowers/specs/2026-11-04-europa-conference-design.md`

## Global Constraints

- Free sources only: football-data.co.uk files and ESPN's public scoreboard JSON (`dates=YYYYMM&limit=1000`). No scraping, no paid APIs.
- Never print the football-data.org token. Never send the user's email anywhere.
- Champions League board rows keep their source (football-data.org) and model (`EUROPE`) unchanged.
- No qualifiers on the board; qualifiers are training data only.
- The 13 new domestic leagues are training data only: no board chips.
- Bets settle on the 90-minute score (`intl_fixtures.goals_90`).
- A club from a covered country that can't be matched is left out of training and off the board, with a warning naming it. Never merged loosely, never kept as a European-only club.
- Thin-data threshold: 10 matches. A European-only club with fewer than 10 matches is left off the board (with its match).
- Board labels exactly "Europa League" and "Conference League"; data set key `UEFA`.
- Tests never touch the network (conftest stubs, as for cups and lower leagues).
- Restart the app server after Python changes before checking the UI.

## Review Focus

1. An ESPN European club whose name loosely matches clubs in two covered countries (e.g. "Sporting" / "Sporting CP" vs "Sporting Gijón"-style names; "Olympiacos" vs "Olympiakos"): expect it unresolved with a warning, never a guess. → test in Task 3.
2. A European-only club with the same name as a domestic club in another country ("Dinamo", "Rapid"): must not merge with it. → test in Task 3.
3. ESPN month that fails to load during the daily refit: previous cached months still used; current month failure → warning, model kept. → test in Task 2.
4. A knockout tie decided in extra time or on penalties: training and settlement use the 90-minute score. → test in Task 2 (training) and Task 5 (settlement).
5. A league with calendar-year seasons (NOR, SWE, FIN, IRL) mid-winter: three-year window by date still includes last season. → test in Task 1.

---

### Task 1: Domestic training data for the UEFA model

**Files:**
- Create: `ninty_five/uefa.py`
- Test: `tests/test_uefa_data.py`

**Interfaces:**
- Consumes: `data.load_club_league(code, n_seasons, refresh_current)`, `data.COLUMNS`.
- Produces:
  - `UEFA_LEAGUES: dict[str, tuple[str, str]]` — code → (country key, label). Exactly:
    `E0 ENG "England 1"`, `D1 GER "Germany 1"`, `SP1 ESP "Spain 1"`, `I1 ITA "Italy 1"`, `F1 FRA "France 1"`, `N1 NED "Netherlands 1"`, `P1 POR "Portugal 1"`, `B1 BEL "Belgium 1"`, `SC0 SCO "Scotland 1"`, `T1 TUR "Turkey 1"`, `G1 GRE "Greece 1"`, `AUT AUT "Austria 1"`, `SWZ SUI "Switzerland 1"`, `DNK DEN "Denmark 1"`, `NOR NOR "Norway 1"`, `SWE SWE "Sweden 1"`, `POL POL "Poland 1"`, `ROU ROU "Romania 1"`, `IRL IRL "Ireland 1"`, `FIN FIN "Finland 1"`.
  - `N_SEASONS = 3`, `EUROPEAN_ONLY = "European only"` (group label).
  - `load_domestic(n_seasons: int = N_SEASONS, refresh_current: bool = False, start: pd.Timestamp | None = None) -> tuple[pd.DataFrame, dict[str, str], dict[str, list[str]]]` — (results with competition = label, team → label of league played most recently, country key → sorted team names seen in the last year of data). `start` (for validation) loads enough seasons to reach back to it. A league that fails to load is skipped with a logged warning (raise only if all fail).

- [ ] **Step 1: Write the failing tests** — fake `data._cached` as in `tests/test_data_countries.py` with tiny main-format (B1) and extra-format (NOR) files:
  - `test_load_domestic_labels_and_groups`: competitions are `{"Belgium 1", "Norway 1", ...}` for the faked codes; a club that played B1 then (later) another faked league has the later label as group.
  - `test_load_domestic_calendar_year_league_in_winter`: with today 2026-01-15 (patch `pd.Timestamp.today` via a `today` param or monkeypatch), NOR matches from 2025-10 are included.
  - `test_load_domestic_skips_failing_league`: one code raising `OSError` → others still returned.
  - `test_country_teams_lists_recent_teams`: `["NOR"]` lists the faked Norwegian clubs.
- [ ] **Step 2: Run** `.venv/bin/pytest tests/test_uefa_data.py -q` — Expected: FAIL (module missing).
- [ ] **Step 3: Implement** `UEFA_LEAGUES`, `load_domestic` in `ninty_five/uefa.py`, reusing `data.load_club_league` (do not add these codes to `data.COUNTRIES`).
- [ ] **Step 4: Run** the tests — Expected: PASS. Then `.venv/bin/pytest -q` — Expected: all pass.
- [ ] **Step 5: Commit** `git add ninty_five/uefa.py tests/test_uefa_data.py && git commit -m "Load the UEFA model's domestic leagues"`

### Task 2: European results from ESPN, cached on disk

**Files:**
- Modify: `ninty_five/uefa.py`
- Test: `tests/test_uefa_data.py`

**Interfaces:**
- Consumes: `intl_fixtures._get(slug, month)`, `intl_fixtures.parse_scoreboard(payload, club=True)`, `intl_fixtures.goals_90(event)`, `intl_fixtures._months`.
- Produces:
  - `BOARD_SLUGS = {"uefa.europa": "Europa League", "uefa.europa.conf": "Conference League"}`
  - `TRAIN_SLUGS = {"uefa.champions": "Champions League", **BOARD_SLUGS, "uefa.champions_qual": "Champions League qualifying", "uefa.europa_qual": "Europa League qualifying", "uefa.europa.conf_qual": "Conference League qualifying"}` (slugs confirmed live on 2026-11-04).
  - `ESPN_CACHE = data.DATA_DIR / "espn"`
  - `european_results(start: pd.Timestamp, end: pd.Timestamp, refresh_current: bool = False) -> tuple[pd.DataFrame, list[str]]` — columns `data.COLUMNS + ["event_id", "qualifier"]`, finished matches only, 90-minute goals, `neutral` from ESPN, competition = label from `TRAIN_SLUGS`; plus warnings for months that failed.
  - Disk cache: one JSON per (slug, month) under `ESPN_CACHE`; months before the current one are read from disk once cached; the current month is re-downloaded when `refresh_current` or not cached.

- [ ] **Step 1: Write the failing tests** (fake `intl_fixtures._get` with event dicts like `tests/test_cup_fixtures.py::ev`, `tmp_path` for `ESPN_CACHE`):
  - `test_european_results_use_90_minutes`: an event with `STATUS_FINAL_AET` whose 90-minute score is 1-1 (details as in `tests/test_intl_fixtures.py`'s AET case) gives hg=1, ag=1.
  - `test_european_results_label_and_qualifier`: `uefa.europa.conf_qual` event → competition "Conference League qualifying", qualifier True; `uefa.europa` → "Europa League", qualifier False.
  - `test_past_months_read_from_disk`: second call for a past month doesn't call `_get`.
  - `test_failed_month_is_a_warning`: `_get` raising for one (slug, month) → result still has other months, warning names slug and month.
  - `test_unfinished_events_are_dropped`: `STATUS_SCHEDULED` and `STATUS_POSTPONED` absent.
- [ ] **Step 2: Run** `.venv/bin/pytest tests/test_uefa_data.py -q` — Expected: new tests FAIL.
- [ ] **Step 3: Implement** `european_results` and the disk cache in `ninty_five/uefa.py`. Scores via `goals_90` on the raw event (parse_scoreboard has no scores); skip events where it returns None.
- [ ] **Step 4: Run** — Expected: PASS; full suite passes.
- [ ] **Step 5: Commit** `git commit -am "Read European results from ESPN for the UEFA model"` (add the test file).

### Task 3: Matching ESPN European clubs to domestic teams

**Files:**
- Modify: `ninty_five/uefa.py`
- Test: `tests/test_uefa_names.py`

**Interfaces:**
- Consumes: `league_fixtures.resolve_name(name, division, teams)`; `cup_fixtures.league_clubs`-style ESPN league scoreboards.
- Produces:
  - `LEAGUE_SLUGS: dict[str, str]` — country key → ESPN league slug, where ESPN has a usable one: `ENG eng.1, GER ger.1, ESP esp.1, ITA ita.1, FRA fra.1, NED ned.1, POR por.1, BEL bel.1, SCO sco.1, TUR tur.1, GRE gre.1, AUT aut.1, DEN den.1, NOR nor.1, SWE swe.1, IRL irl.1`. (SUI, ROU, FIN, POL have no usable ESPN scoreboard: checked 2026-11-04.)
  - `ALIASES: dict[tuple[str, str], str]` — (country, ESPN name) → domestic name; starts with the entries `cup_fixtures.ALIASES` already has for ENG/GER/ESP/FRA/DEN, extended in Task 4's live check.
  - `@dataclass Resolution: mapping: dict[str, str]` (ESPN name → model name; European-only clubs map to their ESPN name), `excluded: set[str]` (covered-country clubs that failed), `european_only: set[str]`, `warnings: list[str]`.
  - `resolve_clubs(names: Iterable[str], teams: dict[str, list[str]], league_clubs: dict[str, set[str]]) -> Resolution` — for each ESPN name: alias → if in a country's `league_clubs`, `resolve_name` within that country only (fail → excluded + warning) → else try every country without an ESPN league slug and every other country's team list; exactly one country with exactly one hit → that team; hits in more than one country → excluded + warning; no hit → European-only.
  - Warning text: `"UEFA: no domestic team for {name} (add it to uefa.ALIASES)"`.

- [ ] **Step 1: Write the failing tests:**
  - `test_club_in_espn_league_resolves_within_country`: "Club Brugge" in `league_clubs["BEL"]`, BEL teams `["Club Brugge", "Cercle Brugge"]` → mapped "Club Brugge".
  - `test_ambiguous_across_countries_is_excluded`: name hitting a team in two countries → in `excluded`, one warning, not in `mapping`.
  - `test_league_club_without_match_is_excluded_not_european_only`: in `league_clubs["SCO"]` but no SCO team matches → excluded.
  - `test_unknown_country_club_is_european_only`: "Dinamo Zagreb" with no hits → `european_only`, maps to itself.
  - `test_european_only_namesake_does_not_merge`: "Dinamo Zagreb" vs ROU team "Dinamo Bucuresti" → European-only (lenient match must not join different clubs; if `resolve_name` would match, the test fails and the rule must be tightened to require the distinguishing token).
  - `test_alias_wins`: alias entry used even if the matcher disagrees.
- [ ] **Step 2: Run** `.venv/bin/pytest tests/test_uefa_names.py -q` — Expected: FAIL.
- [ ] **Step 3: Implement** `LEAGUE_SLUGS`, `ALIASES`, `Resolution`, `resolve_clubs`, plus `espn_league_clubs(now) -> dict[str, set[str]]` (this month and last, like `cup_fixtures.league_clubs`; a failing slug is skipped).
- [ ] **Step 4: Run** — Expected: PASS; full suite passes.
- [ ] **Step 5: Commit** `git commit -m "Match ESPN European clubs to domestic teams"`

### Task 4: The UEFA model data set

**Files:**
- Modify: `ninty_five/uefa.py`, `ninty_five/service.py` (`datasets`, `_fit`, `_cached`, `run_refresh`), `ninty_five/cli.py` (new `uefa-names` subcommand)
- Test: `tests/test_uefa_data.py`, `tests/test_service_board.py`

**Interfaces:**
- Consumes: Tasks 1–3.
- Produces:
  - `load_uefa(refresh_current: bool = False, now: pd.Timestamp | None = None, start: pd.Timestamp | None = None) -> tuple[pd.DataFrame, dict[str, str], Resolution]` — domestic + European results (European names replaced by `mapping`; matches with an `excluded` club dropped), groups (domestic labels; European-only clubs → `EUROPEAN_ONLY`), and the resolution. Window: `now - 3 years`, or from `start - 3 years` when `start` is given (validation).
  - `service._fit("UEFA")` fits `DixonColes(**CLUB_PARAMS).fit(df, groups=groups)`; `"UEFA"` is seasonal in `_cached` and refitted with `refresh="current"` in `run_refresh(..., refit_countries=True)` (warning on failure, previous model kept); `load_uefa` also stores its inputs and result on the module as `uefa.last_resolution: Resolution | None` and `uefa.last_inputs: tuple[dict[str, list[str]], dict[str, set[str]]] | None` (teams, league clubs) for Task 5; the resolution's warnings are added to the daily run's warnings.
  - `service.datasets()` gains `"UEFA": "Europe: Europa + Conference League (20 leagues + European results)"`.
  - CLI `ninty-five uefa-names`: prints every European club of the window with its resolution (domestic name + country, European-only, or EXCLUDED) for a by-eye check.

- [ ] **Step 1: Write the failing tests:**
  - `test_load_uefa_links_european_and_domestic` (fakes for Tasks 1–3 inputs): a European match between two domestic clubs appears under their domestic names; a match with an excluded club is absent; a European-only club's group is "European only".
  - `test_uefa_is_a_dataset_and_refits_daily` in `tests/test_service_board.py`: monkeypatch `uefa.load_uefa` to a tiny frame; `service.datasets()` has "UEFA"; `run_refresh(..., refit_countries=True)` calls it with `refresh_current=True`.
- [ ] **Step 2: Run** — Expected: FAIL.
- [ ] **Step 3: Implement** `load_uefa`, the service wiring and the CLI subcommand.
- [ ] **Step 4: Run** tests — Expected: PASS; full suite passes.
- [ ] **Step 5: Live check (network, not a test).** Run `time .venv/bin/ninty-five uefa-names > .superpowers/uefa-names.txt` and read it. Expected: every club of the 20 covered leagues that played in Europe resolves to the right domestic team; EXCLUDED entries fixed by adding `ALIASES` (rerun until none remain that are real clubs of covered leagues); European-only list contains only clubs from uncovered countries. Then time a fit: `.venv/bin/python -c "import time;from ninty_five import service;t=time.time();service.get_model('UEFA');print(time.time()-t)"`. Expected: under ~120 s. If slower, ledger a ruling (e.g. lower `n_seasons` for the extra leagues) before continuing.
- [ ] **Step 6: Commit** `git commit -m "Fit the UEFA model on domestic and European results"`

### Task 5: Europa League and Conference League on the board

**Files:**
- Modify: `ninty_five/uefa.py`, `ninty_five/service.py` (`uefa_board`, `BOARD_SOURCES`, `update_results` slugs), `tests/conftest.py`
- Test: `tests/test_uefa_board.py`

**Interfaces:**
- Consumes: `load_uefa`'s `Resolution`, `service._cached("UEFA")`, `service._priced`.
- Produces:
  - `uefa.upcoming(days: int = 7, now: pd.Timestamp | None = None) -> tuple[pd.DataFrame, list[str]]` — EL/ECL scheduled fixtures from `BOARD_SLUGS` (`intl_fixtures.upcoming(..., club=True)`), names mapped via `uefa.last_resolution` (ensure the UEFA model is fitted first via `service._cached("UEFA")`; a fixture name not in the mapping is resolved with `resolve_clubs([name], *uefa.last_inputs)`), columns `["key", "dataset", "competition", "kickoff", "home", "away", "neutral", "european_only"]`; key `espn:<event id>`, dataset `"UEFA"`. Listed only when both clubs are model teams; dropped when either club is excluded, or European-only with fewer than 10 matches (`model.n_matches`). `european_only` true when either club is European-only. `REAL_UPCOMING = upcoming`.
  - `service.uefa_board(days) -> tuple[list[dict], list[str]]`, rows as in `cup_board` plus `"european_only"`; appended to `BOARD_SOURCES` as `("European fixtures", "uefa_board")`.
  - `update_results`: ESPN slug list becomes `intl_fixtures.SLUGS + list(cup_fixtures.ESPN_SLUGS) + list(uefa.BOARD_SLUGS)`.
  - conftest: autouse stub `uefa.upcoming` → empty frame + no warnings.

- [ ] **Step 1: Write the failing tests:**
  - `test_uefa_upcoming_lists_known_and_tags_european_only`: fixtures Celtic v Beşiktaş (both domestic), Sparta Prague (European-only, 30 matches) v Bournemouth, Kairat (European-only, 6 matches) v Atalanta, Riga (excluded) v Jablonec → first two listed, second has `european_only`; others absent.
  - `test_uefa_upcoming_skips_qualifier_and_champions_slugs`: only `uefa.europa` and `uefa.europa.conf` requested.
  - `test_uefa_board_rows_priced`: with a fitted tiny UEFA model, rows have `rated`, `competition` "Europa League", `dataset` "UEFA".
  - `test_update_results_settles_europa_bet_on_90_minutes`: a pending `espn:` key with an AET result settles on the 90-minute score (pattern of the existing cup settlement test).
- [ ] **Step 2: Run** `.venv/bin/pytest tests/test_uefa_board.py -q` — Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** — Expected: PASS; full suite passes.
- [ ] **Step 5: Commit** `git commit -m "Put Europa and Conference League matches on the board"`

### Task 6: Validation and "model unproven"

**Files:**
- Modify: `ninty_five/validate.py`, `ninty_five/cli.py`
- Test: `tests/test_validate.py`

**Interfaces:**
- Consumes: `uefa.load_uefa(start=...)`, `model.log_loss_1x2`, `history.set_validation`.
- Produces:
  - `validate_uefa(start: str = "2024-08-01", step_months: int = 1) -> dict[str, dict]` — walk-forward like `validate_country`: per cut-off fit on everything before (groups as of the cut-off), score next month's non-qualifier European matches. Keys: `"Europa League"`, `"Conference League"`, each `{"model", "baseline", "matches"}` over matches where both clubs have domestic groups; plus report-only keys `"Europa League (thin data)"`, `"Conference League (thin data)"`, `"Champions League"`, and `"Champions League vs EUROPE"` (`{"model", "europe", "matches"}` on CL matches both models price). Baseline: home/draw/away frequency of the previous three years' non-qualifier European matches. If either board competition has fewer than 150 scored domestic-both matches, both keys get the pooled EL+ECL numbers.
  - Data reaches back to July 2021 (`start - 3 years`); if ESPN has no events that early, the first cut-off moves to the first month with three seasons behind it and the report says so.
  - `unproven(row, validation)` also judges rows whose competition is "Europa League" or "Conference League".
  - CLI: `ninty-five validate --uefa` runs `validate_uefa`, prints every key, stores the result with `history.set_validation`.

- [ ] **Step 1: Write the failing tests:**
  - `test_unproven_judges_europa_rows`: validation `{"Europa League": {"model": 1.05, "baseline": 1.04, ...}}` → note; `model < baseline` → None; missing → note with None values.
  - `test_validate_uefa_scores_only_domestic_pairs_and_pools_small_samples`: tiny synthetic data (patch `uefa.load_uefa`), ECL under 150 → both keys equal pooled numbers; thin-data key present.
- [ ] **Step 2: Run** — Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** tests — PASS; full suite passes.
- [ ] **Step 5: Live run.** `time .venv/bin/ninty-five validate --uefa > .superpowers/uefa-validation.txt 2>&1` and read it. Expected: numbers for every key. If the thin-data subset is clearly worse than baseline while the domestic subset beats it, try splitting `EUROPEAN_ONLY` into "European only (league phase)" and "European only (qualifiers)" by the furthest stage reached, rerun, keep the better; ledger the ruling with both sets of numbers.
- [ ] **Step 6: Commit** `git commit -m "Validate the UEFA model and flag it unproven if it loses"`

### Task 7: Page, odds import and docs

**Files:**
- Modify: `ninty_five/static/index.html`, `ninty_five/import_odds.py` (only if needed), `README.md`
- Test: `tests/test_import_odds.py`

**Interfaces:**
- Consumes: board rows' `european_only`, `unproven`.
- Produces: thin-data badge tooltip "Rated from European matches only: treat with caution" when `f.european_only`, otherwise unchanged; badge shown when `f.european_only` even if both sides have ≥ 10 matches.

- [ ] **Step 1: Write the failing test** `test_danske_spil_names_match_european_clubs` in `tests/test_import_odds.py`: `same_team` (or the import's matcher) pairs Danske Spil-style names with board names for at least: "Beşiktaş"/"Besiktas", "Union SG"/"St. Gilloise" board name, "FC Midtjylland"/"Midtjylland", "Sparta Prag"/"Sparta Prague", "Crvena Zvezda"/"Red Star Belgrade". Board names here are whatever Task 4's resolution produces — read them from `.superpowers/uefa-names.txt`. Expected strings that the matcher can't handle become entries in an alias map in `import_odds` (as `DANISH_CLUBS`).
- [ ] **Step 2: Run** — Expected: FAIL for the names needing aliases.
- [ ] **Step 3: Implement** the aliases; the badge change in `matchCard`; README section "European cups" (sources, thin data, validation result, CL unchanged).
- [ ] **Step 4: Run** full suite — PASS.
- [ ] **Step 5: Commit** `git commit -m "Show why European matches have thin data; Danske Spil names"`

### Task 8: Check the page in the browser (user request)

**Files:** none (fixes found go in the owning task's files, with a test where the fault is in Python).

- [ ] **Step 1:** Restart the server (stop the running `ninty-five serve` task, start it again). Press refresh on the page (or `POST /api/fixtures/refresh?days=7`) and wait for it to finish.
- [ ] **Step 2:** In the app tab (127.0.0.1:8000 only; do not open or click anything on Danske Spil), check with screenshots:
  - chips "Europa League" and "Conference League" exist, wrap with the others, and filter correctly;
  - EL/ECL cards show both teams, kick-off in Danish time, priced pills, form dots;
  - "thin data" badge on European-only matches with the new tooltip; "model unproven" badge only if validation said so;
  - opening a card works (odds entry, evaluate) for a UEFA match;
  - the Champions League chip and its matches look as before; the info line has no new warnings, or only alias warnings worth reporting;
  - the bet log still loads (log a test bet only if the user agrees).
- [ ] **Step 3:** Fix anything wrong (with a failing test first for Python faults), rerun the suite, recheck, commit.
- [ ] **Step 4:** Report what was checked, with a screenshot, to the user.
