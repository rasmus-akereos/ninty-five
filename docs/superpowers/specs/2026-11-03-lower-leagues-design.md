# Lower leagues and a daily fetch — design

Date: 2026-11-03 · Branch: `feat/lower-leagues` (starts from `feat/tabs`)

## Why

The user bets on second- and third-division football in England, France, Germany, Italy and Spain, and on the Danish Superliga, and Danske Spil lists those matches. Today the board only covers the Champions League, eight top divisions and the Championship, plus national teams. The user also wants fixtures and results fetched automatically every day instead of depending on opening the page.

Success: the board lists the new leagues' matches with model prices, each under its own league button; bets on them settle automatically; and a fresh board and results are in place every morning without the user doing anything.

## Goals

- Add six leagues to the board: **England League One, England League Two, Germany 2. Bundesliga, Spain Segunda División, Italy Serie B, France Ligue 2** — plus the **Danish Superliga** (added by the user after the first draft).
- Price them with a model that knows those teams.
- Fetch their results so bets settle automatically and they enter the track record like every other board match.
- Fetch the whole board (all sources) and results **once a day automatically** while the app is running.

## Non-goals

- The four non-English third tiers (3. Liga, Primera Federación, Serie C, National): no free source exists (checked 2026-11-03 against football-data.org's free tier, ESPN and football-data.co.uk). Not attempted.
- The Danish 1. Division: football-data.co.uk has only the Superliga, and ESPN lists the competition (`den.2`) but carries no matches for it (checked 2026-11-03). Not attempted.
- ESPN for the new leagues, and a paid football-data.org plan. The user chose football-data.co.uk alone (free); a paid plan (fixtures a week ahead, faster results) may come later if betting on these leagues turns out profitable. The design should not make that switch harder than replacing the fixtures/results source.
- Cup competitions (FA Cup, DFB-Pokal, …), play-offs.
- Running the app online (a later, separate project). The daily fetch must not assume anything that would stop it working on a server later.
- Changing how existing leagues, national teams, the Champions League or any calculation work.

## Anti-goals

- A wrong match or wrong team on the board (a fixture priced with the wrong team's rating) — worse than an unrated row.
- The daily fetch running twice at once with a manual refresh, or failing silently forever.
- Slower page loads (the stored board must still appear instantly).

## Decisions made with the user

- The six leagues above plus the Danish Superliga; third tiers outside England dropped.
- Layout: each new league gets its own league button.
- Source: football-data.co.uk only, for fixtures, results and history (no ESPN, no paid plan for now). Accepted consequences: the new leagues' matches appear on the board only about 2–3 days before kick-off, and their bets settle a day or two after the match.
- Daily fetch happens inside the app while it runs (the user's computer is usually on); hosting online comes later.
- The design below was played back to and confirmed by the user.
- A division that fails validation is shown with a "model unproven" tag rather than hidden.

## Data sources (verified 2026-11-03)

| League | Board label (my call) | Division code (football-data.co.uk) | Results + history file |
|---|---|---|---|
| League One | England 3 | `E2` | `mmz4281/<season>/E2.csv` |
| League Two | England 4 | `E3` | `mmz4281/<season>/E3.csv` |
| 2. Bundesliga | Germany 2 | `D2` | `mmz4281/<season>/D2.csv` |
| Segunda División | Spain 2 | `SP2` | `mmz4281/<season>/SP2.csv` |
| Serie B | Italy 2 | `I2` | `mmz4281/<season>/I2.csv` |
| Ligue 2 | France 2 | `F2` | `mmz4281/<season>/F2.csv` |
| Superliga | Denmark 1 | Denmark / Superliga | `new/DNK.csv` |

- Denmark: `new/DNK.csv` is football-data.co.uk's "extra leagues" format: one file with every season since 2012/13 (columns `Season, Date, Time, Home, Away, HG, AG, Res`, closing odds), not one file per season, and it holds only the Superliga. The loader needs a small reader for this format; the current season is refreshed by re-downloading the same file. It includes the championship and relegation rounds after the split, which count as ordinary Superliga matches.
- **Fixtures:** `fixtures.csv` (main-league format: `Div, Date, Time, HomeTeam, AwayTeam, …`; holds E2, E3, D2, SP2, I2, F2 among others) and `new_league_fixtures.csv` (extra-league format: `Country, League, Date, Time, Home, Away, …`; holds Denmark). Both are published roughly twice a week, just before each round, and only hold the next few days' matches (on 2026-11-03 they held 3–6 November). Dates are dd/mm/yyyy; times are UK time (to be confirmed by the builder against a known kick-off and converted).
- **Results:** the same season CSVs the model trains on, updated a day or two after each round. These leagues have no extra time, so the full-time score is the 90-minute score.
- football-data.co.uk: regular-season CSVs for 2023/24 onwards confirmed for all six (the `www.` host now redirects; urllib follows it). Top-division codes for the same countries: `E0`, `E1`, `D1`, `SP1`, `I1`, `F1`.
- Fixtures, results and history all come from one site with the same team names, so no name mapping is needed.

## Design

### Models: one per country

A new data set per country — keys `ENG`, `GER`, `ESP`, `ITA`, `FRA` (my call) — trained on that country's divisions from football-data.co.uk, three seasons plus the current one, using the model's existing league-group strengths (as the EUROPE model does with CL links):

- `ENG`: E0, E1, E2, E3 · `GER`: D1, D2 · `ESP`: SP1, SP2 · `ITA`: I1, I2 · `FRA`: F1, F2 · `DEN`: Superliga only (a single division, so no group strengths).

Promoted and relegated clubs link the divisions, so the model learns how much stronger each division is. Each team's group is the division it played in most recently.

**Which model prices what:** matches already on the board (top divisions, Championship, CL, national teams) keep their current model (EUROPE / INTL), so nothing existing changes. The seven new leagues are priced by their country model. (The Championship stays on EUROPE; ENG also includes it only for training.)

### Fixtures and results

- A new module reads the two fixtures files, keeps the seven divisions, and builds board rows: dataset = the country key, competition = the board label above, no neutral venue, team names as in the file (they are the model's names, so pricing, evaluation, odds history and bets work unchanged).
- Key: `fdcu:<division>:<kick-off date>:<home>:<away>` (my call; the files carry no match id). The date is part of it because Superliga teams can meet at the same ground twice in a season (after the split).
- Results: `update_results` looks up pending `fdcu:` matches in the division's results file by division + home + away, taking the result whose date is closest to the stored kick-off within 30 days. That way a match moved to another day still gets its result and its bets still settle. A match with no result 30 days after kick-off is marked called off, like other sources' called-off matches (my call). Auto-settlement then reuses the existing key-based path.
- The files are fetched together, a handful of downloads per run, with the same timeouts and failure-as-warning behaviour as the other sources.

### Validation

Run the existing walk-forward backtest per country model and record 1X2 log loss vs the naive baseline (the league's home/draw/away frequencies) for each new division. The results are stored with the model and reported to the user. **Decided with the user:** a division where the model does not beat the baseline is still shown, but every match card in it carries a "model unproven" tag (styled like "thin data", with a tooltip giving the two log-loss numbers), and the fold-down repeats it as a note. Divisions that pass show no tag.

### Daily fetch

- A background thread in the server process runs `service.refresh_board` once a day at **07:00 Danish time (Europe/Copenhagen)** regardless of the machine's time zone (my call), with the same day window as the default board (7 days).
- The daily run also re-downloads the **current-season** CSVs of the six country models (for `DEN`, the single DNK file) and refits them, so their ratings and form stay current (my call). INTL and EUROPE keep their existing refresh behaviour.
- On server start, if the last stored fetch (manual or automatic) is older than 24 hours (or none exists), it fetches straight away — this covers the computer having been off or asleep at 07:00.
- Overlap protection is a **lease stored in the SQLite database** (my call), taken by both the daily run and a manual refresh: whoever holds it runs, the other is skipped (manual refresh returns the stored board with a "refresh already running" note). A lease older than 30 minutes counts as abandoned. This works for today's single process and still holds if the app later runs as several processes. On a host that pauses idle processes, the online project will need an external trigger calling the same refresh; that is out of scope here.
- Each run's outcome (time, number of matches, warnings or error) is stored, and the board info line shows the last automatic fetch time; a failure is logged and retried at the next hourly check (the thread wakes hourly), never silently given up.
- It must keep working unchanged when the app later runs on a server (no OS scheduler, no desktop-only features).

## Left to the builder

- Module layout (e.g. a new `league_fixtures.py`), how the fixtures files are cached between the daily run and a manual refresh, table/column names for storing daily-run outcomes.
- How the lock is shared between the thread and the refresh endpoint.
- Tests in the existing pytest style (monkeypatched sources, no network), including both fixtures-file formats, a result found for a moved match, the 30-day called-off rule, the start-up catch-up rule and the 07:00 schedule.
