# Europa League and Conference League on the board

## Why

The user bets on Danske Spil and uses the board to find matches where the
model's chances beat the odds. Europa League (EL) and Conference League (ECL)
matches are not on the board at all: football-data.org's free plan doesn't
carry them, and most of their clubs play in leagues no model here covers. With
today's EUROPE model only about 5 of a typical round's 36 matches could be
priced.

The user wants all the matches on the board, priced, and will decide per
match whether to bet. They will only bet on matches between clubs the model
knows well; matches involving a club the model barely knows must be shown but
clearly marked, so the user can skip them.

Getting the ratings right matters more than having this week's round in time.

## Goals

1. EL and ECL fixtures (league phase and knockouts, not qualifiers) appear on
   the board up to the board's usual horizon, each priced with the usual
   markets (1, X, 2, Over/Under 2.5, BTTS Yes/No), under two new league chips:
   "Europa League" and "Conference League".
2. A new club model, data set key `UEFA`, prices them. It is fitted on:
   - domestic top-flight results from football-data.co.uk for 20 countries
     (below), and
   - European results from ESPN for the Champions League, Europa League and
     Conference League, qualifiers included, which link the leagues' strengths.
3. Clubs from countries without domestic data (Czechia, Croatia, Serbia,
   Hungary, Cyprus, Israel, Bulgaria, Kazakhstan, ...) are rated from their
   European results alone and the match is shown with a **"thin data"** tag.
4. Bets on these matches settle automatically on the 90-minute score from
   ESPN, like the cups and the Superliga.
5. The model is validated before it is trusted: same standard as the lower
   leagues (beat the simple baseline).

## Non-goals

- The Champions League keeps its current source (football-data.org) and model
  (`EUROPE`). Logged bets there keep working unchanged. Moving CL to the new
  model is a possible later step once validation shows it is better.
- The 13 newly modelled domestic leagues do **not** get their own board chips.
  They are training data only.
- No qualifiers on the board (they are in the training data only).
- No paid data sources; no scraping. Free files and ESPN's public JSON only,
  as today.

## Anti-goals

- A match priced from a wrongly matched club (e.g. ESPN "Sporting" resolved
  to the wrong Portuguese club, or a European-only club silently merged with
  a domestic namesake). A wrong rating that looks confident is worse than a
  missing match.
- A thin-data match that looks as trustworthy as a well-known one.
- Breaking existing boards, bets, settlement or the daily fetch.

## Data

### Domestic leagues (football-data.co.uk)

Already used elsewhere in the app: England 1 (E0), Germany 1 (D1), Spain 1
(SP1), Italy 1 (I1), France 1 (F1), Denmark (DNK, extra format).

New for this model, checked available on 2026-11-04:

| Country | Code | Format |
|---|---|---|
| Netherlands | N1 | main (season files) |
| Portugal | P1 | main |
| Belgium | B1 | main |
| Scotland | SC0 | main |
| Turkey | T1 | main |
| Greece | G1 | main |
| Austria | AUT | extra (one file, all seasons) |
| Switzerland | SWZ | extra |
| Norway | NOR | extra |
| Sweden | SWE | extra |
| Poland | POL | extra |
| Romania | ROU | extra |
| Ireland | IRL | extra |
| Finland | FIN | extra |

Not available from football-data.co.uk (404): Czechia, Croatia, Serbia.
ESPN's history for those and for Hungary, Bulgaria, Slovakia, Slovenia,
Ukraine is missing or patchy, so they are not used.

Window: the same three-season window the EUROPE model uses, by date (the
calendar-year leagues NOR, SWE, FIN, IRL included by date). The existing
`data.load_club_league` handles both formats and the current-season refresh.

Each domestic team's group (for the model's league strengths) is the league
it played in most recently, as in `fixtures.load_europe`. Only top flights
are loaded, so a club relegated to a second tier keeps its last top-flight
group, as today.

### European results (ESPN)

ESPN scoreboards `uefa.champions`, `uefa.europa`, `uefa.europa.conf` and
their qualifying slugs (the builder confirms the qualifying slug names), via
the month form already used in `intl_fixtures` (`dates=YYYYMM&limit=1000`).
Checked on 2026-11-04: full months of events going back to at least 2023.
Scores at 90 minutes (`goals_90`); `neutralSite` for finals.

### Matching ESPN club names to the domestic files

This is the risky part (see anti-goals). A European club resolves to a
domestic team only when the match is unambiguous:

1. Alias table first, keyed by (country, ESPN name), extended as warnings
   show up (the cup module's `ALIASES` pattern).
2. The club's country, taken from ESPN's own current league scoreboard for
   that country (the `LEAGUE_SLUGS` pattern in `cup_fixtures`), where ESPN
   has one; then `league_fixtures.resolve_name` within that country only.
3. If ESPN has no usable league scoreboard for a covered country, resolve
   against that country's team list only when exactly one covered country's
   list matches.
4. Anything else is a **European-only club**, kept under its ESPN name.

A club from a covered country that fails to resolve is logged as a warning
naming it, so the alias table can be extended — it must not silently become a
European-only club. Until an alias is added, its European matches are left
out of training and its matches are left off the board (a missing match beats
a club split into two model teams). The fit carries on; the daily fetch shows
the warning. *(Designer's call.)*

"Covered country" for a European-only club: ESPN events don't reliably carry
a club's country, so a club counts as from a covered country when it appears
in that country's ESPN league scoreboard (step 2). A club that appears in no
covered league scoreboard and resolves to no covered team list is
European-only. The builder adds a check (CLI or test against live data)
listing every European club of the last three seasons with its resolution, to
review once by eye.

## The model

`DixonColes(**CLUB_PARAMS)` with `groups`, exactly like EUROPE: one group per
domestic league. European-only clubs start in one shared group. Because
qualifiers bring in very weak clubs (San Marino, Andorra, Gibraltar, Faroes),
one shared group can pull a Slavia Prague or Dinamo Zagreb toward a level
those minnows drag down. The builder may split the European-only group (for
example by the furthest stage a club reached: league phase or later vs
qualifiers only, or by country where ESPN gives it) if validation on the
thin-data subset shows a bias; the choice and its validation numbers are
recorded. *(Designer's call.)* The fit must finish
within a couple of minutes on this machine and is cached like the other
seasonal models (`MODEL_TTL_SECONDS`, refit on the daily run).

## On the board

- Fixtures: EL and ECL from ESPN (`intl_fixtures.upcoming(..., club=True)`),
  keys `espn:<event id>`, league chips "Europa League" and "Conference
  League", data set `UEFA`.
- A match is listed when both clubs are in the fitted model.
- **"thin data"** tag when either club is a European-only club, or has
  fewer than 10 matches in the training window. The board already has a
  "thin data" badge (index.html, "Few recent matches for one side", fewer
  than 10 matches) on every competition; that stays as it is on the other
  boards. For EL/ECL rows the server sends a flag for the European-only case
  and the badge's tooltip says why ("rated from European matches only"). A club with fewer than 10
  matches **and** no domestic league is left off the board entirely, along
  with its match (it is effectively unknown). *(10 is the designer's call; see
  below.)*
- Results and auto-settlement: add the EL and ECL slugs to the ESPN results
  pass in `service.update_results`.
- Danske Spil odds import: Danske Spil's club names must match these fixtures;
  extend `import_odds` name handling where the live board shows misses.

## Validation

Extend `ninty-five validate` with the UEFA model, rolling monthly from
2024-08 like `validate_country`: fit on everything before each cut-off,
predict the next month's **European** matches (EL and ECL, plus CL for a
larger sample, reported separately), compare 1X2 log-loss with the
frequency baseline. Report separately for matches where both clubs have
domestic data and matches with a thin-data club. Also report, on matches
both can price, the new model against the existing EUROPE model, so the user
can see whether moving CL later would help.

History for validation: to give the 2024-08 cut-off a full three-season
window, load ESPN European months and domestic seasons back to July 2021. If
ESPN has no European events that far back, start validation at the first
cut-off with three full seasons behind it and say so in the report.

"Model unproven" for EL/ECL is decided per board label ("Europa League",
"Conference League"), like `validate.unproven`, scored on league-phase and
knockout matches where **both clubs have domestic data**: those are the
matches the user bets on, and thin-data matches already carry their own tag.
If one competition has fewer than 150 such scored matches, the two are judged
pooled. Qualifiers are in training but not scored. If the model doesn't beat
the baseline for a label, every match under it gets the "model unproven" tag.
*(Designer's calls.)*

## Decided by the user

- Option 2: widen the model so (almost) all matches are priced; the user
  decides per match. (2026-11-04)
- Clubs without domestic data: rated from European matches, shown with a
  "thin data" tag. (2026-11-04)
- Correctness over getting this week's round in time. (2026-11-04)

## Designer's calls (the user can overturn)

- Champions League stays on its current source and model.
- The 13 new leagues are training-only, with no chips of their own.
- The thin-data threshold of 10 matches, and leaving off clubs below it with
  no domestic league.
- Qualifiers in training, not on the board.
- Three-season window, as EUROPE.
- An unresolved club from a covered country: warning, left out of training
  and off the board until an alias is added.
- European-only clubs in one group, which the builder may split if
  validation shows a bias.
- "Model unproven" judged per competition on matches where both clubs have
  domestic data; pooled if a competition has fewer than 150; qualifiers not
  scored; validation history back to July 2021.
- The existing "thin data" badge stays on other boards; EL/ECL rows add the
  European-only reason.

## Left to the builder

Module layout, the qualifying slug names, caching of ESPN history months on
disk, the exact alias entries, and the Danske Spil name fixes.
