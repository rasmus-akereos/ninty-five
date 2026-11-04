# Statistics tab and self-tuning model — design

Date: 2026-11-04

## Why

The user wants to understand their betting decisions and, above all, wants
ninety-five's model to get better over time. Data is thin today (6 settled
bets, 33 logged; ~277 board matches with model snapshots, 472 odds entries),
but it grows every day. The Statistics tab is both a mirror (how am I and the
model doing?) and the visible side of an automatic tuning loop that uses the
growing record to adjust the model's settings.

Success: in a few months the user can open Statistics and see (1) whether
they are winning or losing, and whether that is luck or edge, (2) where the
model misjudges teams or leagues, and (3) what the model has changed about
itself, why, and undo any change.

## Goals

- A third top-level tab, **Statistics**, with three sub-tabs (user picked
  layout B from mockups): **My betting · Model · Tuning**.
- **My betting** (from `bets`):
  - Tiles: settled bets, profit, ROI, average return per bet, expected profit
    (sum of stake × EV over settled bets), bets per day, stake per day,
    average CLV.
  - Graph: cumulative profit over time with cumulative expected profit as a
    second, dashed line ("luck vs edge").
  - Graph: stake per day (bars) with bets per day.
  - Tables: by league, by market group (1X2, Over/Under, BTTS, double chance,
    other), by odds range (e.g. <1.8, 1.8–2.5, 2.5–4, >4). Columns: bets,
    staked, profit, ROI, expected profit.
- **Model** (from `snapshots` + `matches` + `odds_entries`, all finished
  board matches, not just bet ones):
  - Per team: matches, expected points vs actual points (from the 1X2
    probabilities), expected goals for/against vs actual (from snapshot
    `xg`). Flag "model overrates / underrates" only when the gap is
    statistically clear (see Rules); otherwise "in line" or "too few".
  - Per league: the same, plus model log loss vs Danske Spil log loss on
    matches with odds, and "who was closer" share.
  - The existing **Track record** section (currently in the Bet log tab)
    moves here, unchanged.
- **Tuning**: current value of every tuned setting, progress toward its next
  test ("64 of 100 matches"), and a change log (date, setting, scope, old →
  new, the test numbers, Revert button).
- **Automatic tuning** runs with the daily refresh and changes settings live
  without asking (user's decision), inside the guard rails below.

## Non-goals

- No fetching odds automatically, no "top bets of the day" (separate idea).
- No machine learning beyond the settings listed; the model stays
  Dixon–Coles.
- No changes to already-logged bets: they keep the numbers they were placed
  with.
- No multi-user, no auth changes, nothing exposed beyond the tailnet.

## Anti-goals (would be failure even if it "works")

- Settings that flip back and forth on noise.
- A team flagged as "bad" from five matches, nudging the user to avoid it.
- Statistics that hide how small the sample is.
- The model silently getting worse with no way to see or undo it.

## What gets tuned, in order

1. **Model weight α** (model vs de-margined Danske Spil). Today a fixed 0.5,
   set in the browser's Settings dialog. Tuned first overall, then per league
   once that league has enough data. Evidence: finished board matches with
   entered odds — for a candidate α, log loss of the blended probability on
   the actual 1X2 (and O/U 2.5 when odds exist) outcome.
2. **Half-life of old results** (`half_life_days`, now 365 for clubs).
   Evidence: walk-forward log loss as in `validate.py`. Expensive, so tested
   at most monthly.
3. **Team-level corrections: deferred to a later round** (user's decision).
   This round only shows team flags on the Model sub-tab; automatic
   adjustment of a team's ratings comes once teams have ~30 board matches
   to test on. The Tuning sub-tab may list it as "not yet active".

## Rules (guard rails)

- **Runs**: inside the daily refresh (`service.run_refresh`, auto runs), after
  results are in. No new scheduler. A manual "Run tuning now" is not needed.
- **Minimum evidence**: α overall needs ≥100 finished matches with odds;
  α per league ≥100 in that league.
  Below that, the Tuning tab shows progress only.
- **Better means**: the candidate beats the current value on matches it was
  not chosen on. Split the evidence by time: choose the candidate on the
  older part, confirm on the newest ~30%. Adopt only if the confirm-set log
  loss improves by more than its standard error (paired per-match
  differences). Ties keep the current value.
- **Small steps**: α moves at most 0.10 per change; half-life at most ×/÷1.5.
- **Bounds**: α stays within [0.2, 0.8]; half-life within [180, 1095] days.
- **Log every change** (and every test that kept the value, so "tested
  today, kept" can be shown) in a new table.
- **Revert**: restores the old value and blocks that setting/scope from
  changing again for 30 days.
- **Flags on the Model sub-tab** follow the same honesty: a team is flagged
  only when |actual − expected points| exceeds 2 standard deviations
  (variance from the predicted 1X2 probabilities) and n ≥ 15.

## Sample size everywhere

Every figure shows its n. Rows or tiles below a threshold (bets: 10; teams:
15 matches) are shown faded, not hidden.

## Look

- Charts: thin lines (user remark: mockup lines were too thick), dashed for
  expected. Must work in light and dark theme and on the phone layout
  (360 px, no sideways scroll), like the rest of the page.
- Sub-tabs styled like the existing league chips.

## Model weight α: server-owned

The editable "Model weight α" input in the Settings dialog is removed. In its
place the dialog shows the value in use, read-only ("Model weight: 0.50,
tuned automatically — see Statistics → Tuning"); per-league values listed when
they differ. The server resolves α per match (league-specific value if one
exists, else the overall value) inside `/api/evaluate`; the client no longer
sends α. New bets store the `p_blend` computed with that α.

## Half-life tuning

One `half_life_days` shared by all club data sets; internationals (INTL) are
not tuned. Evidence is the walk-forward log loss on football-data history
(as in `validate.py`), not board matches, so the 100-board-match minimum does
not apply; the "better means" rule (beat current by more than its standard
error on held-out matches) and step/bound rules do. Tested at most monthly,
and not while holding the refresh lease for long (builder decides: after the
refresh, or in its own lease).

## Independence

Each tuned setting is tested on its own as soon as it has enough evidence;
"in order" is build order, not a gate. α (board odds) and half-life (match
history) use different evidence.

## Decided by the user

- Statistics tab with sub-tabs (option B).
- Team insight is for checking whether the model misjudges a team.
- Model should improve over time; tuning changes go live automatically.
- Guard rails as listed above (playback approved).
- Remove the α input; show the value in use instead.
- Automatic team corrections wait for a later round, when there is data.

## Designer's calls (check these)

- Team/league analysis uses all finished board matches, not only bet ones.
- Track record moves from Bet log into Statistics → Model.
- The last snapshot taken before kick-off is the model's prediction for a
  match.
- Thresholds: 100 / 100 matches, n ≥ 15 for team flags, 30-day revert
  block, step and bound sizes above.
- Odds-range buckets as listed.

## Left to the builder

- Chart implementation (inline SVG or a small pinned charting library from
  an allowed CDN; no build step exists today).
- Endpoint shapes, table schema for tuned settings and the change log, and
  how tuned values are applied in `evaluate` and model fitting.
- Exact candidate grid for α (e.g. 0.05 steps) and how the half-life
  walk-forward is cached.
