# ninety-five online: on the phone, from this PC

## Why

The user bets on Danske Spil and runs the tool every morning, mostly at the PC,
for about half an hour, picking the day's matches. When travelling (airport,
train, patchy mobile data or airport Wi-Fi) they want to do the same from their
phone. Only the user uses it.

On the phone, Danske Spil forces its app (the "desktop site" switch just opens
the app), so the copy-and-paste odds import can't be used there. The user
accepts typing odds by hand on the phone.

The user also accidentally logged the same bet twice (Watford v Lincoln). A
second identical bet only adds to the risk, so the tool must make a logged bet
obvious and refuse a duplicate.

## Goals

1. **Rename** the system to *ninety-five* (package `ninety_five`, command
   `ninety-five`, page title, README, tests, docs references going forward).
2. **Always running on this PC** (vonNeumann, which is set never to sleep on
   AC): a systemd *user* service starts the app at boot and restarts it if it
   crashes. No terminal needed. The 07:00 Copenhagen daily fetch and its
   catch-up on start keep working unchanged.
3. **Private access from the phone with Tailscale.** The app keeps listening on
   127.0.0.1 only; `tailscale serve` exposes it over HTTPS at the PC's tailnet
   address (`https://vonneumann.<tailnet>.ts.net`), reachable only by the
   user's own signed-in devices. No public exposure, no password in the app.
4. **The existing page works on a phone** (narrow screen, touch), for the
   morning flow: today's matches, open a match card, type the six odds with
   the number keypad, see the value, log the bet, check the bet log, settle or
   Took profit. The PC layout stays as it is.
5. **A logged bet shows as logged, and duplicates are refused.**
   - In the match card, a market with an open bet shows "Logged" with stake
     and odds; its Log bet button is replaced and can't log again.
   - On the board, a match with any open bet shows a small marker.
   - The server refuses a new bet when an **open** bet exists on the same
     match and market (same `match_key` when both have one, else same
     dataset, home, away). It returns an error the page shows. "Market"
     here is the selection stored in the bet's `market` column (`1`, `X`,
     `2`, `Over 2.5`, `BTTS No`, ...), not the group: the user deliberately
     holds `2` and `X` on the same match (Milan v Ferencvaros), so that
     stays allowed. Settled bets don't block.
   - Existing bets, including the two Watford v Lincoln bets, are untouched.

## Non-goals

- Google Cloud or any public hosting (the user chose this PC).
- Odds from screenshots or scraping. Odds are typed or pasted, as now.
- A separate phone app or a phone-specific design.
- "Top bets of the day" with odds fetched automatically: a later idea, its own
  conversation (needs an odds source other than scraping Danske Spil).

## Anti-goals

- Anything reachable from the open internet.
- Losing or altering the bet ledger, odds history or cached data in the rename
  or the move to a service.
- Breaking the PC layout while fixing the phone one.
- A double tap or a second tab creating the same bet twice.

## Done by the user (Claude can't install or sign in for them)

- Install Tailscale on the PC and the phone and sign in on both (the user's
  choice of account, e.g. Google). Claude writes the steps and does the PC
  side (`tailscale serve`, the service) once Tailscale is installed. Enabling
  `tailscale serve`/HTTPS may need a click in the Tailscale admin page.

## Outside the repo (ask the user before each)

- Rename the GitHub repo `ninty-five` → `ninety-five` (GitHub redirects the old
  URL), and update the local remote.
- Rename the folder `~/code/ninty-five` → `~/code/ninety-five`: **first**,
  at the end of the design session, before any building (the user decided,
  2026-11-04). The `.venv` is rebuilt and the editable install redone at the
  new path, and the bets, caches and `.env` are checked present. Building,
  including the service, then happens in a new session in
  `~/code/ninety-five`, so the service is set up on the final path.

## Decided by the user

- Run on this PC, not Google Cloud. (2026-11-04)
- Tailscale for phone access. (2026-11-04)
- Typing odds by hand on the phone is fine. (2026-11-04)
- Mostly used at the PC; the phone is for travel days. (2026-11-04)
- Rename to ninety-five. (2026-11-04)
- Show logged bets and stop duplicates. (2026-11-04)

## Designer's calls (the user can overturn)

- systemd user service with lingering enabled, so it runs without a login
  session; logs go to the journal.
- `tailscale serve` (HTTPS, app stays on 127.0.0.1) rather than binding the
  app to the tailnet address.
- Duplicate rule: refuse (not warn) while an open bet exists on the same
  match and market.
- Board marker for matches with an open bet.
- Replace the browser `prompt()` for the stake with an inline stake box with
  the number keypad, since prompts are clumsy on a phone. Settle and Took
  profit may stay as they are if they work on the phone.
- Phone layout breakpoint at the existing 720px; tables that can't fit scroll
  sideways inside their box, never the whole page.
- Validation tables and track-record details may stay laptop-shaped.

## Verification

- Tests for the rename (suite green under the new name), the duplicate rule
  (server refuses, allows other markets, allows after settle) and the logged
  state in the API data the page uses.
- The service survives a reboot-equivalent (`systemctl --user restart`) and a
  killed process.
- Browser check at phone width (Chrome device emulation): board, chips, match
  card odds entry, log bet, logged state, bet log, settle, PC width unchanged.
- The user opens it on the phone over Tailscale with Wi-Fi off.

## Left to the builder

The order of work, the exact CSS, how the page learns which markets are
logged (from the existing `/api/bets` list or a field on the board), the
service unit's details, and the written Tailscale steps.
