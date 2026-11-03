# Board history, form and track record — design

Date: 2026-11-03 · Branch: `feat/fold-down-board` (builds on the fold-down board and national-team fixtures)

## Why

The user checks matches on the board against Danske Spil. Two things are missing:

1. **The board is thrown away.** Every page reload empties it; the user must click *Fetch matches* again and wait. Nothing about earlier fetches is kept.
2. **There is no way to learn who is right.** On matches like Derby v Norwich (model 37.6% Derby, Danske Spil 27.5%) the user has to decide whether the model or the bookmaker knows better. Recording the model's prices, the Danske Spil odds the user typed, and the actual result, for every match, answers that over time. Seeing each team's recent form at decision time helps sanity-check big disagreements on the spot.

Success: reload shows the board instantly; after a few weeks the user can open a *Track record* view and see whether the model or Danske Spil was closer, and whether the model's probabilities are well calibrated.

## Goals

- Persist the board. Page load shows the last stored board immediately; refetch only when stale or on demand.
- Snapshot **every match on the board** at each fetch: model probabilities for all markets, expected goals, and both teams' form.
- Store **every Danske Spil price the user enters** in the fold-down, with a timestamp.
- Show each team's **form** (last five results) in the fold-down.
- Fetch **actual results** for past board matches automatically (clubs and national teams). This also lets national-team bets auto-settle.
- A **Track record** section comparing model vs Danske Spil vs result.

## Non-goals

- Fetching Danske Spil odds automatically (no scraping; prices are still typed by the user).
- Changing the model, its parameters or how recommendations are computed.
- Closing odds capture (still entered by hand when settling a bet).
- Charts. Tables and a few headline numbers are enough for a first version.

## Anti-goals (failure even if it "works")

- The board gets slower to appear than today, or a history/result fetch failure stops the board from showing.
- Recording history changes any number the user bets on.
- Existing bets in `data/bets.sqlite` are lost or altered by the migration.

## Decisions made with the user

- History covers **every match on the board**, not only the ones the user opens.
- **Entered odds are stored** whenever the user types them.
- **Form = each team's last five results**, shown as e.g. `W W D L W`, with score, opponent and date available on hover; stored with each snapshot.
- **Reopening a match shows empty odds boxes with a grey hint** under each box with the last stored price, e.g. "last entered 2.70 at 14:05". Stored prices are never fed into the evaluation; only what the user types now counts (prices move, and a stale price must not produce a stake).

## Design

### Match identity

Every fixture gets a stable key from its source: `fd:<football-data match id>` for club matches, `espn:<ESPN event id>` for national-team matches. Matches added by hand via "Add a match that isn't listed" have no key; their entered odds are stored with dataset/home/away/kick-off-less identity (see below) and are excluded from the track record in this version (my call: they cannot be matched to a result reliably).

### Storage (same SQLite file as the bet log, `data/bets.sqlite`)

- `matches` — one row per fixture key: dataset (`EUROPE`/`INTL`), competition, detail (e.g. "UEFA Nations League"), kick-off (UTC), home, away, neutral, status, 90-minute result (home goals, away goals) once known, first seen / last seen timestamps.
- `snapshots` — one row per match per fetch where something changed: match key, taken at, model "as of" date, expected goals, probabilities for **all** markets the model prices (JSON), home form and away form (JSON). If probabilities (rounded to 4 decimals) and form are identical to the match's previous snapshot, no row is written (my call: avoids thousands of duplicate rows from repeated fetches).
- `odds_entries` — one row per entered price: match key (nullable for manual matches, then dataset/home/away stored instead), entered at, market, odds, and the model, de-margined market (if the market was complete) and blended probabilities at that moment. A new row is written only when a market's price differs from the last stored price for that match (my call: typing "2", "2.", "2.7" must not create three rows — store on the debounced evaluation, and only values > 1 that changed).
- `bets` — add a nullable `match_key` column (migration, existing rows untouched). Bets logged from the board store the key; auto-settlement matches on `match_key` for both sources (existing `match_id` rows keep working).

Minimum odds are **not** stored; they are derived from stored probabilities and the current "Min edge for list" setting, so changing that setting never requires a refetch.

### Board loading

- `GET /api/fixtures` returns the stored board (upcoming matches = kick-off in the future and within the requested days, latest snapshot each) plus `fetched_at`. It does not hit external APIs.
- `POST /api/fixtures/refresh` (or a `refresh=1` flag) fetches both sources, writes matches and snapshots, fetches results (below) and auto-settles bets. The "Fetch matches" button calls it.
- On page load the page shows the stored board at once. If the stored board is older than **6 hours** (my call: matches the existing data refresh interval), the page triggers a refresh in the background and updates when done. The board info line shows "updated HH:MM".
- Failures: as today, a source that fails becomes a warning; the stored rows for that source remain visible.

### Form

For each team, the last five matches **before the fixture's kick-off** from the data set the model is trained on: date, opponent, venue (H/A/N), goals for/against, result W/D/L. Clubs: the EUROPE data (domestic leagues + Champions League). National teams: the INTL results CSV — this lags (currently up to 2026-10-06), so the UI shows the date of the most recent form game so staleness is visible. Computed at snapshot time; returned with the board row; rendered in the fold-down under the team names.

### Results

At each refresh, for stored matches whose kick-off has passed and that have no result yet (looking back at most 14 days, my call):
- Clubs: football-data.org finished matches (existing `recent_results`, 90-minute score), matched on `fd:` key.
- National teams: ESPN scoreboard for the relevant month(s), matched on `espn:` key. 90-minute score: for `STATUS_FULL_TIME` use the score; for extra time / penalties (`STATUS_FINAL_AET`, `STATUS_FINAL_PEN`) count goal events in `competitions[0].details[]` with clock ≤ 90'+stoppage. Cancelled/postponed matches get that status and no result.
- Then auto-settle open bets by `match_key`.

### Track record section

A new section below the board (above the bet log), covering finished matches with at least one snapshot before kick-off. For each match the **last snapshot before kick-off** and the **last entered odds before kick-off** are used.

- Headline numbers, for 1X2 and Over/Under 2.5 separately:
  - matches counted;
  - model log loss, and on the subset with complete entered Danske Spil prices, model vs Danske Spil (de-margined) log loss side by side, plus a **head-to-head** count: per match, which of the two put more probability on the outcome that actually happened ("model closer X%, Danske Spil closer Y%"; my call — it answers "who is closer", which is the question this feature exists for);
  - calibration table: predicted probability bands (0–10%, …, 90–100%) with count, average predicted and actual frequency.
- A table of recent finished matches: kick-off, match, result, model 1/X/2, Danske Spil 1/X/2 (if entered), the user's bet (if any).
- Filter by the same league buttons as the board (my call: keeps the UI consistent).

## Left to the builder

- Exact table/column names, JSON shapes, indexes, and API response shapes.
- Module layout (e.g. a `history.py` alongside `store.py`).
- Rendering details of form in the fold-down and of the Track record section, within the existing page style.
- Tests: follow the existing pytest style with monkeypatched sources; no network in tests.
