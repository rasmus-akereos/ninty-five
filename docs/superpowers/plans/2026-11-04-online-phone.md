# ninety-five online (phone, from this PC) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rename to ninety-five, refuse duplicate bets and show logged ones, make the page work on a phone, run the app as an always-on service on this PC, and reach it privately from the phone over Tailscale.

**Architecture:** Same FastAPI app and single page. The duplicate rule lives in `BetStore.add` (one lock, so concurrent requests can't both pass) and surfaces as HTTP 409. The page derives "logged" state from the `/api/bets` list it already loads. A systemd user unit runs `ninety-five serve` on 127.0.0.1:8000; `tailscale serve` publishes it over HTTPS to the user's tailnet only.

**Tech Stack:** Python 3.14, FastAPI, SQLite, vanilla JS/CSS in `static/index.html`, systemd (user), Tailscale.

**Spec:** `docs/superpowers/specs/2026-11-04-online-phone-design.md`

**Starting point:** the folder was already renamed to `~/code/ninety-five` and `.venv` rebuilt at the end of the design session (still with the old package name installed). Work happens on the branch `ninety-five-online` (created with this plan; the spec and plan are its first commit).

## Global Constraints

- The app listens on 127.0.0.1 only. Nothing is reachable from the open internet.
- Never alter or delete existing bets; the two Watford v Lincoln bets stay.
- Never print the football-data.org token; it stays in `.env`.
- PC layout (> 720px) looks as before; phone rules live under `@media (max-width: 720px)`.
- Duplicate = an **open** bet with the same `market` (the selection: `1`, `X`, `2`, `Over 2.5`, `BTTS No`, ...) on the same match: same `match_key` when both bets have one, else same `dataset`, `home`, `away`. Other selections on the same match are allowed; settled bets don't block.
- Ask the user before: renaming the GitHub repo, pushing, merging, anything needing `sudo` (the user runs those with `! <command>`).
- Restart the app (now: `systemctl --user restart ninety-five`) after Python changes.
- Commits end with the session's attribution lines.

## Review Focus

1. Double tap on "Log": two POSTs arrive together → exactly one bet is stored (Task 2 test `test_concurrent_duplicates_store_one`).
2. A bet logged from a manual card (no `match_key`) then the same selection from the board card (with `match_key`) → refused (Task 2 test `test_duplicate_across_manual_and_board`).
3. Narrowest common phone (360px wide) → no horizontal page scroll; wide tables scroll inside their box (Task 4 check).
4. The service is killed or the PC reboots → the app is back within seconds without anyone logging in (Task 5 checks, linger enabled).
5. The 409 from a refused duplicate shows as a readable message on the page, not a silent failure (Task 3 browser check).

---

### Task 1: Rename the package to ninety-five

**Files:**
- Rename: `ninty_five/` → `ninety_five/` (`git mv`)
- Modify: every `ninty_five` / `ninty-five` / `NINTY_FIVE` in `ninety_five/**`, `tests/**`, `pyproject.toml`, `README.md` (not the dated files under `docs/`, which are history)
- Test: `tests/test_app.py`

**Interfaces:**
- Produces: package `ninety_five`, console script `ninety-five`, env switch `NINETY_FIVE_SCHEDULER`, page `<title>ninety-five</title>`, bookmarklet label "→ ninety-five", User-Agent `ninety-five/0.1`.

- [ ] **Step 1:** `git switch ninety-five-online` (already exists).
- [ ] **Step 2: Write the failing test** in `tests/test_app.py`:

```python
def test_page_is_named_ninety_five(client):
    html = client.get("/").text
    assert "<title>ninety-five</title>" in html and "ninty" not in html
```

- [ ] **Step 3:** Run `.venv/bin/pytest tests/test_app.py -q -k ninety`. Expected: FAIL (title is `ninty-five`).
- [ ] **Step 4:** `git mv ninty_five ninety_five`, then replace `ninty_five`→`ninety_five`, `ninty-five`→`ninety-five`, `NINTY_FIVE`→`NINETY_FIVE` in the files above. Reinstall: `.venv/bin/pip uninstall -y ninty-five && .venv/bin/pip install -e '.[dev]'`.
- [ ] **Step 5:** `git grep -n -i ninty -- ':!docs'` → no output. `.venv/bin/pytest -q` → all pass (191 + 1). `.venv/bin/ninety-five --help` lists `serve`.
- [ ] **Step 6:** Check the data survived: `.venv/bin/python -c "from ninety_five.store import BetStore; print(len(BetStore().list()))"` prints `29` (29 bets, 23 open, counted before the folder rename on 2026-11-04, plus any the user logged since).
- [ ] **Step 7:** Commit "Rename to ninety-five".

### Task 2: Refuse duplicate open bets (server)

**Files:**
- Modify: `ninety_five/store.py` (`BetStore.add`, new `DuplicateBet`), `ninety_five/app.py` (`add_bet`)
- Test: `tests/test_store_duplicates.py` (new), `tests/test_app.py`

**Interfaces:**
- Produces: `class DuplicateBet(ValueError)` in `store.py`; `BetStore.add(bet: dict) -> int` raises `DuplicateBet` per the Global Constraints rule; `POST /api/bets` returns **409** with `{"detail": "Already logged: <market> on <home> – <away>"}`.

- [ ] **Step 1: Write the failing tests** (`tests/test_store_duplicates.py`, store at `tmp_path / "b.sqlite"`, base bet `{"dataset": "E0", "home": "Watford", "away": "Lincoln", "market": "1", "odds": 2.0, "stake": 10, "match_key": "fd:9"}`):
  - `test_same_selection_same_match_refused`: second `add` of the base bet raises `DuplicateBet`; `len(list()) == 1`.
  - `test_other_selection_allowed`: base, then `market="X"` → two bets.
  - `test_allowed_after_settle`: base, `settle(id, home_goals=1, away_goals=0)`, base again → two bets.
  - `test_duplicate_across_manual_and_board`: base without `match_key`, then base with `match_key="fd:9"` → `DuplicateBet`.
  - `test_different_match_keys_allowed`: `match_key="fd:9"` then `match_key="fd:10"` (same teams, a later fixture) → two bets.
  - `test_existing_duplicates_untouched`: insert two identical open rows with raw SQL, then `list()` returns both and `add` of a third raises.
  - `test_concurrent_duplicates_store_one`: 8 threads `add` the base bet at once (`threading.Barrier`) → exactly one succeeds, 7 raise `DuplicateBet`, `len(list()) == 1`.
  - In `tests/test_app.py`, `test_duplicate_bet_is_409`: POST the same bet twice → second status 409 and `"Already logged: 1 on" in detail`.
- [ ] **Step 2:** `.venv/bin/pytest tests/test_store_duplicates.py tests/test_app.py -q`. Expected: the new tests FAIL (no `DuplicateBet`).
- [ ] **Step 3:** Implement: in `add`, inside a module-level `threading.Lock` and one connection, `SELECT` open bets with the same `market` matching the rule, raise `DuplicateBet(f"Already logged: {market} on {home} – {away}")`, else insert. In `app.add_bet`, catch `DuplicateBet` → `HTTPException(409, str(exc))`.
- [ ] **Step 4:** Same command. Expected: PASS. Full suite `.venv/bin/pytest -q` green.
- [ ] **Step 5:** Commit "Refuse a second open bet on the same selection".

### Task 3: Show logged bets on the card and board; inline stake

**Files:**
- Modify: `ninety_five/static/index.html`

**Interfaces:**
- Consumes: `GET /api/bets` (`bets[]` with `status`, `market`, `stake`, `odds`, `match_key`, `dataset`, `home`, `away`); 409 from Task 2.
- Produces (JS): global `openBets` (set in `loadBets()` to bets with `status === "open"`); `openBetsFor({key, dataset, home, away}) -> bet[]` using the same match rule as the server.

- [ ] **Step 1:** `loadBets()` stores `openBets`, then re-renders the board (and the open card's value table if one is open).
- [ ] **Step 2:** Board row: when `openBetsFor(row)` is non-empty, a badge `bet placed` (tooltip lists `market · stake kr @ odds` for each) next to the other badges.
- [ ] **Step 3:** Card value table (`renderResult`): for a selection with an open bet, the last cell shows `Logged · <stake> kr @ <odds>` instead of the Log control, for recommended and non-recommended rows alike. Above the table, a line "Your open bets on this match: …" when any exist (covers selections not in the table).
- [ ] **Step 4:** Replace `prompt()` in `logBet`: a recommended row's last cell has a stake input (`inputmode="decimal"`, prefilled with the recommended stake, ~5em wide) and a `Log` button. On success: `loadBets()` (the row turns to Logged). On error: show the server's message in a `.note` under the table (409 text included), no `alert`. Disable the button while the request is in flight.
- [ ] **Step 5: Browser check** (Chrome, the running app; ask before typing odds into a real fixture's card, since evaluating stores odds history; use a **manual card** with test teams otherwise, and delete the test bet from the bet log afterwards):
  - log a bet → row shows Logged; board row shows "bet placed";
  - press Log again for the same selection via a second tab → note "Already logged: …";
  - another selection on the same match can still be logged;
  - reload → Logged state persists.
- [ ] **Step 6:** `.venv/bin/pytest -q` green. Commit "Show logged bets and log stakes inline".

### Task 4: Phone layout

**Files:**
- Modify: `ninety_five/static/index.html` (CSS under `@media (max-width: 720px)`, markup only where CSS can't do it)

- [ ] **Step 1: Measure first.** In Chrome, resize to 360×780 and 390×844 (`resize_window`), open each tab (board, bet log, track record, settings) and a match card, and run `document.documentElement.scrollWidth - innerWidth` (must be `0`). Note each overflow and each control smaller than ~40px tall (screenshot).
- [ ] **Step 2:** Fix what Step 1 found: header and tabs wrap; settings grid one column; league chips wrap (already) and fit; match card odds boxes full width with the keypad (`inputmode="decimal"` already present); value table and bet log stay inside `.scroll` boxes; buttons (Log, Settle, Took profit, ✕) at least 40px tall on phone. Settle/Took profit keep their current `prompt()` only if it works on a phone browser; otherwise inline, like Task 3.
- [ ] **Step 3:** Repeat Step 1 → `0` overflow on every tab at both widths. At 1280px wide, screenshots of board, card and bet log match the pre-change look.
- [ ] **Step 4:** Commit "Make the page work on a phone".

### Task 5: Always-on service

**Files:**
- Create: `deploy/ninety-five.service`
- Modify: `README.md` (section "Running it all the time")

- [ ] **Step 1:** Write the unit:

```ini
[Unit]
Description=ninety-five betting board
After=network-online.target

[Service]
WorkingDirectory=%h/code/ninety-five
ExecStart=%h/code/ninety-five/.venv/bin/ninety-five serve --host 127.0.0.1 --port 8000
Restart=always
RestartSec=5
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=default.target
```

- [ ] **Step 2:** Stop any manually started server (TaskStop / Ctrl-C), then: `mkdir -p ~/.config/systemd/user && ln -sf ~/code/ninety-five/deploy/ninety-five.service ~/.config/systemd/user/ && systemctl --user daemon-reload && systemctl --user enable --now ninety-five`.
- [ ] **Step 3:** Ask the user to run `! sudo loginctl enable-linger lunde` (starts the service at boot without a login). Check `loginctl show-user lunde -p Linger` → `Linger=yes`.
- [ ] **Step 4: Verify:** `curl -s -o /dev/null -w '%{http_code}' localhost:8000/` → `200`. `systemctl --user kill -s KILL ninety-five`; within 10s the curl is `200` again and `systemctl --user show -p NRestarts ninety-five` ≥ 1. `journalctl --user -u ninety-five -n 30` shows startup without errors.
- [ ] **Step 5:** README: how to restart (`systemctl --user restart ninety-five`), see logs (`journalctl --user -u ninety-five -f`), stop. Commit "Run ninety-five as a user service".

### Task 6: Private access over Tailscale

**Files:**
- Create: `docs/phone-access.md` (the user's steps)
- Modify: `README.md` (link to it)

- [ ] **Step 1:** Write `docs/phone-access.md`: install Tailscale on the PC (`curl -fsSL https://tailscale.com/install.sh | sh`, user runs it), `sudo tailscale up` and sign in; install the Tailscale app on the phone and sign in with the same account; in the admin console enable MagicDNS and HTTPS certificates if asked. Then `sudo tailscale set --operator=$USER` once, and `tailscale serve --bg 8000`. Phone address: the `https://…ts.net` URL `tailscale serve status` prints. Add the page to the phone's home screen.
- [ ] **Step 2:** Hand those steps to the user; wait until `tailscale status` works on the PC.
- [ ] **Step 3:** Run `tailscale serve --bg 8000`; `tailscale serve status` shows `https://<host>.<tailnet>.ts.net` → `http://127.0.0.1:8000`, tailnet only (no "Funnel"). `curl -s -o /dev/null -w '%{http_code}' https://<that host>/` → `200`. `tailscale funnel status` shows nothing public.
- [ ] **Step 4:** The user opens the URL on the phone **with Wi-Fi off**: board loads, a card opens, the bet log shows. Record the result in the ledger.
- [ ] **Step 5:** Commit "Document phone access over Tailscale".

### Task 7: Finish

- [ ] **Step 1:** Final review and fix pass per the executing skill; suite green.
- [ ] **Step 2:** Ask the user: merge to `main` and push? Then ask: rename the GitHub repo to `ninety-five` (`gh repo rename ninety-five`) and update `origin` (`git remote set-url origin git@github.com:rasmus-akereos/ninety-five.git`)?
