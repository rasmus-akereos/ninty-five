# Lower leagues and a daily fetch — design

Date: 2026-11-03 · Branch: `feat/lower-leagues` (starts from `feat/tabs`)

## Why

The user bets on second- and third-division football in England, France, Germany, Italy and Spain, and Danske Spil lists those matches. Today the board only covers the Champions League, eight top divisions and the Championship, plus national teams. The user also wants fixtures and results fetched automatically every day instead of depending on opening the page.

Success: the board lists the new leagues' matches with model prices, each under its own league button; bets on them settle automatically; and a fresh board and results are in place every morning without the user doing anything.

## Goals

- Add six leagues to the board: **England League One, England League Two, Germany 2. Bundesliga, Spain Segunda División, Italy Serie B, France Ligue 2.**
- Price them with a model that knows those teams.
- Fetch their results so bets settle automatically and they enter the track record like every other board match.
- Fetch the whole board (all sources) and results **once a day automatically** while the app is running.

## Non-goals

- The four non-English third tiers (3. Liga, Primera Federación, Serie C, National): no free source exists (checked 2026-11-03 against football-data.org's free tier, ESPN and football-data.co.uk). Not attempted.
- Cup competitions (FA Cup, DFB-Pokal, …), play-offs.
- Running the app online (a later, separate project). The daily fetch must not assume anything that would stop it working on a server later.
- Changing how existing leagues, national teams, the Champions League or any calculation work.

## Anti-goals

- A wrong match or wrong team on the board (a fixture priced with the wrong team's rating) — worse than an unrated row.
- The daily fetch running twice at once with a manual refresh, or failing silently forever.
- Slower page loads (the stored board must still appear instantly).

## Decisions made with the user

- The six leagues above; third tiers outside England dropped.
- Layout: each new league gets its own league button.
- Daily fetch happens inside the app while it runs (the user's computer is usually on); hosting online comes later.
- The design below was played back to and confirmed by the user.
- A division that fails validation is shown with a "model unproven" tag rather than hidden.

## Data sources (verified 2026-11-03)

| League | Board label (my call) | Fixtures + results (ESPN slug) | History CSV (football-data.co.uk) |
|---|---|---|---|
| League One | England 3 | `eng.3` | `E2` |
| League Two | England 4 | `eng.4` | `E3` |
| 2. Bundesliga | Germany 2 | `ger.2` | `D2` |
| Segunda División | Spain 2 | `esp.2` | `SP2` |
| Serie B | Italy 2 | `ita.2` | `I2` |
| Ligue 2 | France 2 | `fra.2` | `F2` |

- ESPN: same keyless scoreboard JSON used for national teams (`intl_fixtures.py`): month form `dates=YYYYMM&limit=1000` works, range form is broken. These leagues have no extra time, so full-time score = 90-minute score.
- football-data.co.uk: regular-season CSVs for 2023/24 onwards confirmed for all six (the `www.` host now redirects; urllib follows it). Top-division codes for the same countries: `E0`, `E1`, `D1`, `SP1`, `I1`, `F1`.
- Team names differ between the two sources for most clubs (e.g. ESPN "Sheffield Wednesday" vs CSV "Sheffield Weds", "1. FC Nürnberg" vs "Nurnberg", "RC Celta Fortuna" vs "Celta B"). A fuzzy pairing found the right partner for every club in the check.

## Design

### Models: one per country

A new data set per country — keys `ENG`, `GER`, `ESP`, `ITA`, `FRA` (my call) — trained on that country's divisions from football-data.co.uk, three seasons plus the current one, using the model's existing league-group strengths (as the EUROPE model does with CL links):

- `ENG`: E0, E1, E2, E3 · `GER`: D1, D2 · `ESP`: SP1, SP2 · `ITA`: I1, I2 · `FRA`: F1, F2.

Promoted and relegated clubs link the divisions, so the model learns how much stronger each division is. Each team's group is the division it played in most recently.

**Which model prices what:** matches already on the board (top divisions, Championship, CL, national teams) keep their current model (EUROPE / INTL), so nothing existing changes. The six new leagues are priced by their country model. (The Championship stays on EUROPE; ENG also includes it only for training.)

### Fixtures and results

- Generalise the ESPN client in `intl_fixtures.py` so the same fetching/caching/parsing serves both national teams and club leagues (slug list per use). Club rows get key `espn:<event id>`, dataset = the country key, competition = the board label above, no neutral venue.
- Results for these keys come from ESPN in `update_results` exactly like national-team results (keys already start with `espn:`), so auto-settlement and the track record need no new logic.

### Team names

At board-building time, map each ESPN team name to the country model's team name:
1. an explicit alias table per league (for names the matcher gets wrong or can't decide),
2. otherwise the existing lenient matcher (`import_odds.same_team`) against that model's teams **of the same division** (restricting the candidates avoids "Sporting"-style cross-matches),
3. a name that matches no team or more than one → the fixture is shown **unrated**, and the board info line carries a warning naming it, so the alias table can be extended.

The board, bet log and track record show the ESPN (long-form) names. The model name is stored alongside on every board row and in the `matches` table (`model_home`, `model_away`), and every path that prices a match (fold-down evaluation, odds history, logged bets) sends the model names, so `/api/evaluate` and the bet log keep working (my call).

### Validation

Run the existing walk-forward backtest per country model and record 1X2 log loss vs the naive baseline (the league's home/draw/away frequencies) for each new division. The results are stored with the model and reported to the user. **Decided with the user:** a division where the model does not beat the baseline is still shown, but every match card in it carries a "model unproven" tag (styled like "thin data", with a tooltip giving the two log-loss numbers), and the fold-down repeats it as a note. Divisions that pass show no tag.

### Daily fetch

- A background thread in the server process runs `service.refresh_board` once a day at **07:00 Danish time (Europe/Copenhagen)** regardless of the machine's time zone (my call), with the same day window as the default board (7 days).
- The daily run also re-downloads the **current-season** CSVs of the five country models and refits them, so their ratings and form stay current (my call). INTL and EUROPE keep their existing refresh behaviour.
- On server start, if the last stored fetch (manual or automatic) is older than 24 hours (or none exists), it fetches straight away — this covers the computer having been off or asleep at 07:00.
- Overlap protection is a **lease stored in the SQLite database** (my call), taken by both the daily run and a manual refresh: whoever holds it runs, the other is skipped (manual refresh returns the stored board with a "refresh already running" note). A lease older than 30 minutes counts as abandoned. This works for today's single process and still holds if the app later runs as several processes. On a host that pauses idle processes, the online project will need an external trigger calling the same refresh; that is out of scope here.
- Each run's outcome (time, number of matches, warnings or error) is stored, and the board info line shows the last automatic fetch time; a failure is logged and retried at the next hourly check (the thread wakes hourly), never silently given up.
- It must keep working unchanged when the app later runs on a server (no OS scheduler, no desktop-only features).

## Left to the builder

- Module layout (e.g. a shared `espn.py` used by `intl_fixtures.py` and a new `league_fixtures.py`), the alias table format, table/column names for storing daily-run outcomes.
- How the lock is shared between the thread and the refresh endpoint.
- Tests in the existing pytest style (monkeypatched sources, no network), including name mapping, unrated-on-ambiguity, the start-up catch-up rule and the 07:00 schedule.
