# ninety-five

A small football betting tool. It fits a goal model to historical results,
prices every common market, and compares those prices with the odds you type
in from your bookmaker (built around Danske Spil, but any decimal odds work).
It then shows the edge and a suggested stake, and keeps a log of your bets.

## Run it

```bash
python3 -m venv .venv            # Debian/Ubuntu: sudo apt install python3.X-venv (match your version)
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
ninety-five serve                 # open http://127.0.0.1:8000
```

Modern Linux distributions block `pip install` into the system Python
(PEP 668, "externally-managed-environment"), so always install into a
virtual environment. Run `source .venv/bin/activate` again in each new
terminal before using `ninety-five`. If `venv` fails with "ensurepip is not
available", delete the half-created `.venv`, install the package it names
(e.g. `python3.14-venv`) and retry, or use `uv venv` instead, which needs
no extra system package.

### Automatic fixtures (Champions League + top leagues)

Add a football-data.org token to `.env` (git-ignored). Reuse the sweepstakes
app's token straight from Secret Manager without printing it:

```bash
printf 'FOOTBALL_DATA_TOKEN=%s\n' "$(gcloud secrets versions access latest --secret=fd-token --project=<project>)" > .env
chmod 600 .env
```

or register for a free key at https://www.football-data.org/client/register
(see `.env.example`). Then click **Fetch matches**. The board lists upcoming
Champions League, Premier League, Championship, Bundesliga, La Liga, Serie A,
Ligue 1, Eredivisie and Primeira Liga games, plus national-team matches
(Nations League, World Cup and continental qualifiers, friendlies), with the
**minimum odds** worth
betting at, so you only have to check Danske Spil's prices. Filter by league
with the buttons above the list. Click a match to **fold it open** right under
its row and type the real odds: each box shows its minimum odds and turns green
when the price is worth betting, and the edge and stake update as you type.
Club bets logged from the board **settle automatically** once the result is
in (90-minute score). National-team bets settle automatically too, except
after extra time with an own goal, where you settle by hand.

National-team fixtures come from ESPN's public scoreboard data (no key needed).
It is an unofficial, undocumented feed, so it can change without notice; if it
fails, the board still shows the club matches with a warning.

### Lower leagues

The board also lists **League One, League Two, 2. Bundesliga, Segunda
División, Serie B, Ligue 2 and the Danish Superliga** (buttons *England 3*,
*England 4*, *Germany 2*, *Spain 2*, *Italy 2*, *France 2*, *Denmark 1*). They
come from football-data.co.uk (no key; the Superliga's fixtures come from ESPN,
see Cups), which publishes fixtures about twice a week, just before each
round: these matches appear **2–3 days before kick-off**, not a week ahead, and their bets settle a day or two after the
match, when the site adds the result.

Each country has its own model (`ENG`, `GER`, `ESP`, `ITA`, `FRA`, `DEN`),
trained on its divisions together so promoted and relegated clubs tell it how
strong each division is. Check them with

```bash
ninety-five validate            # walk-forward 1X2 log loss vs a home/draw/away baseline
```

A league where the model doesn't beat the baseline (or that was never
checked) shows a **model unproven** tag. If the board warns about an unknown
team name, add it to `ALIASES` in `ninety_five/league_fixtures.py`.

### Cups

The **FA Cup, Carabao Cup, DFB-Pokal, Copa del Rey, Coppa Italia and Coupe de
France** come from ESPN (no key) and are priced by the country models. Only
ties between two clubs the model knows are listed: England's top four
divisions and the top two in Germany, Spain, Italy and France. A non-league
or lower-division side is skipped. A finals venue marked neutral is priced
without home advantage. Bets settle on the **90-minute score** (extra time and
penalties don't count). If the board warns that a league club has no model
team, add its ESPN name to `ALIASES` in `ninety_five/cup_fixtures.py`. The
Danish cup isn't covered: no free source carries it. The **Danish Superliga's** fixtures and results
also come from ESPN, a week ahead (football-data.co.uk lists Danish fixtures
too late); its model still trains on football-data.co.uk results.

### Europa League and Conference League

Fixtures and results come from ESPN (no key); bets settle on the 90-minute
score. They are priced by their own model, data set `UEFA`, trained on three
seasons of:

- the top flight of 20 countries from football-data.co.uk (England, Germany,
  Spain, Italy, France, Netherlands, Portugal, Belgium, Scotland, Turkey,
  Greece, Austria, Switzerland, Denmark, Norway, Sweden, Poland, Romania,
  Ireland, Finland), and
- every Champions, Europa and Conference League match, qualifiers included
  (ESPN), which ties the leagues' strengths together.

Clubs from other countries (Czechia, Croatia, Serbia, Hungary, Cyprus, ...)
are rated from their European matches alone. Their matches carry a **thin
data** badge ("Rated from European matches only"). One with fewer than 10
European matches is left off the board.

ESPN's club names are matched to football-data.co.uk's carefully: a club in
ESPN's list of a covered league matches within that country; any other club
only matches a team strictly (every word equal) in a country ESPN doesn't
list. A league club that can't be matched is left out, with a warning on the
board; add it to `ALIASES` in `ninety_five/uefa.py`. `ninety-five uefa-names`
lists how every club was matched.

`ninety-five validate --uefa` tests the model month by month from August 2024
and stores the result for the board. On 4 Nov 2026 (1X2 log loss, lower is
better):

| Matches | Model | Baseline | Matches scored |
|---|---|---|---|
| Europa + Conference League, both clubs with domestic data | 0.965 | 1.019 | 411 |
| Europa League, a thin-data club | 0.960 | 1.065 | 139 |
| Conference League, a thin-data club | 0.978 | 1.045 | 219 |
| Champions League | 0.942 | 1.008 | 357 |

On Champions League matches since August 2025 it also beat the `EUROPE` model
that prices the Champions League on the board (0.896 vs 0.939). The Champions
League still uses `EUROPE`.

### Daily fetch

While the app runs it fetches the board, results and the lower leagues'
latest results by itself **every day at 07:00 Danish time**. If the computer
was off at 07:00, it fetches when the app starts (when the last fetch is over
24 hours old). A failed fetch is retried every hour. The line above the
matches shows when it last ran. A Refresh pressed during a fetch says
"Refresh already running" instead of fetching twice. Switch it off with
`NINETY_FIVE_SCHEDULER=0`.

### Danske Spil button

Drag the **→ ninety-five** button at the top of the board to your browser's
bookmarks bar. On a Danske Spil match page (logged in or not), click it: it
reads the prices shown on that page (1X2, over/under, both teams to score)
and opens the app with that match folded open and the boxes filled in. It
only reads the page you have open; nothing is sent to Danske Spil. If the
match isn't on the board, it opens "Add a match" with the prices filled in.
It reads the page's text, so a redesign of Danske Spil's match page may need
an update to `ninety_five/import_odds.py`.

### History and track record

The board is kept between visits: opening the page shows the last fetch at
once, and it refreshes by itself once it is more than 6 hours old (or when you
click *Fetch matches*). Every fetch stores a snapshot of every match on the
board (the model's prices and both teams' last five results, shown as form in
the fold-down), and every Danske Spil price you type is stored too. Reopening
a match shows your last prices as grey hints under empty boxes, so a stale
price never produces a stake. Results are fetched automatically, which also
settles national-team bets.

The **Track record** section compares the model and Danske Spil on finished
matches: log loss (lower is better), how often each gave the actual result
more probability, and whether the model's percentages come true about as often
as it says.

The first fetch downloads three seasons (~27 requests). The free tier allows
10 requests a minute, so expect about 3 minutes. After that only the current
season is refreshed, at most every 6 hours. For a morning list in the terminal:

```bash
ninety-five fixtures --days 3
```

### Matches not on the list

Click **+ Add a match that isn't listed** at the top of the board. It folds
open the same way, with team pickers:

1. Pick a data set: **International** (every national team worldwide, so
   World Cup, Euros, qualifiers and Nations League) or a club league.
2. Type the two teams. Tick *Neutral venue* for tournament matches.
3. Copy the odds from Danske Spil. Commas (`2,15`) are fine. Enter **every
   outcome** of a market (all of 1/X/2, both Over and Under) so the
   bookmaker's margin can be removed.
4. Green boxes and rows clear your minimum edge. *Log bet* stores them.
5. After the match, *Settle* with the final score. Add the **closing odds**
   (the last price before kick-off) whenever you can.

Other commands:

```bash
ninety-five ratings --dataset INTL --top 30   # team strength table
ninety-five backtest --dataset INTL           # walk-forward model test
pytest
```

## Running it all the time

On this PC the app runs as a systemd user service (`deploy/ninety-five.service`),
listening on 127.0.0.1:8000 only. It starts at boot and restarts itself within
seconds if it crashes. The daily 07:00 fetch runs inside it as before.

    # once: install and start
    mkdir -p ~/.config/systemd/user
    ln -sf ~/code/ninety-five/deploy/ninety-five.service ~/.config/systemd/user/
    systemctl --user daemon-reload && systemctl --user enable --now ninety-five
    sudo loginctl enable-linger $USER   # start at boot, without logging in

    systemctl --user restart ninety-five       # after changing Python code
    journalctl --user -u ninety-five -f        # follow the log
    systemctl --user stop ninety-five          # stop it (e.g. to run it by hand)

To use it from your phone, see [docs/phone-access.md](docs/phone-access.md).

## Data

| Data set | Source | Notes |
|---|---|---|
| `INTL` | [martj42/international_results](https://github.com/martj42/international_results) | All senior men's internationals; the model uses 2014 onward |
| `E0`, `D1`, `SP1`, ... | football-data.co.uk season CSVs | Big European leagues, last 4 seasons |
| `DNK`, `SWE`, `BRA`, `USA`, ... | football-data.co.uk "extra leagues" | Worldwide leagues |
| `ENG`, `GER`, `ESP`, `ITA`, `FRA`, `DEN` | football-data.co.uk | One model per country: its divisions together (lower-league board) |

Files are cached in `data/`. The refresh endpoint (`POST /api/refresh?dataset=INTL`)
downloads them again. Fixtures are not downloaded. You enter the match yourself.

## Method

### 1. Goal model (Dixon-Coles)

Each team *i* has an attack strength `a_i` and a defence strength `d_i`:

```
λ_home = exp(μ + γ·H + a_home − d_away)
λ_away = exp(μ +       a_away − d_home)
```

`μ` is the average log scoring rate and `γ` is home advantage. `H = 0` at a
neutral venue. Goals are treated as Poisson, and the Dixon-Coles factor
`τ(x, y; ρ)` corrects the 0-0, 1-0, 0-1 and 1-1 cells, because independent
Poisson under-predicts draws.

The parameters maximise a **weighted, penalised log-likelihood**:

```
Σ_k w_k · log P(score_k) − (κ/2) · Σ_i (a_i² + d_i²)
w_k = 0.5 ^ (age_days / half_life)
```

* The time decay lets the model follow form without discarding history.
* The ridge penalty `κ` ties the parameters down and pulls teams with little
  data toward average. That matters for national teams, which play only about
  10 matches a year.
* The Poisson part has an analytic gradient and is solved with L-BFGS. `ρ` is
  then fitted in 1-D with the λ's held fixed.

The score matrix (0–10 goals each) gives every market: 1X2, double chance,
O/U 0.5–4.5 and BTTS.

**Hyper-parameters (international).** These were chosen by walk-forward
testing: refit every 6 months from 2023-01 to 2026-07 and score competitive
matches in the next 6 months by 1X2 log loss. The surface is flat for a
half-life of 3–4 years and κ ≤ 0.5. We use half-life 1095 days and κ = 0.2.
Down-weighting friendlies did not help. Result (`ninety-five backtest`):

| | 1X2 log loss |
|---|---|
| Model | **0.839** |
| Baseline (historical H/D/A frequencies) | 1.056 |
| Uniform guess (ln 3) | 1.099 |

The baseline is weak, so beating it is only a sanity check. The test that
counts is beating the bookmaker's own probabilities, which this data set
cannot do because it has no odds. The bet log (CLV, below) is how you find
out.

**Europe (Champions League) data set.** Domestic leagues never play each
other, so fitted separately every league averages to "0" and a mid-table
Bundesliga side looks as strong as a mid-table Premier League side. The
EUROPE data set fits all leagues and the Champions League **jointly**, with
strengths split into a league offset plus a team deviation:

```
a_i = A_league(i) + u_i        d_i = D_league(i) + v_i
penalty = (κ/2)·Σ(u² + v²) + (κ_league/2)·Σ(A² + D²)
```

Domestic games only inform the team deviations, because the league offset
cancels within a league. Champions League games identify the league
offsets. A club with little data is pulled toward its own league's level,
not the overall average. Clubs from leagues outside the free tier (Denmark,
Scotland, Belgium, ...) appear only through their CL games, are grouped as
"OTHER" and flagged ⚠ for thin data. The CL final is treated as neutral.

**Club leagues** use the Dixon-Coles paper's decay (half-life ≈ 1 year). They
have not been tuned yet.

### 2. Removing the bookmaker margin

Implied probabilities `q_i = 1/odds_i` add up to more than 1. The excess is
the margin, shown on screen. The **power method** finds `k` such that
`Σ q_i^k = 1` and sets `p_i = q_i^k`. Compared with proportional scaling it
takes more margin off longshots, which matches the favourite-longshot bias.
Double-chance market probabilities are built from the de-margined 1X2 prices.

### 3. Blending model and market

```
p = α · p_model + (1 − α) · p_market        (α = 0.5 by default)
```

The model knows nothing about injuries, line-ups, rotation or motivation, and
the market does. With α = 1 the tool flags many false "value" longshots.
Raise α only once your logged bets show positive CLV.

### 4. Edge and stake

```
EV    = p · odds − 1
Kelly = (p · odds − 1) / (odds − 1)
stake = min(kelly_fraction · Kelly, max_stake_pct) · bankroll
```

The defaults are ¼-Kelly, a 3 % cap per bet and a 3 % minimum EV. Full
Kelly assumes `p` is exact, which it never is.

### 5. Measuring whether it works

* **ROI** is noisy. At odds around 2.0, a few hundred bets are needed before
  ROI separates skill from luck.
* **CLV (closing line value)** = `your_odds / closing_odds − 1`. It converges
  much faster and is the best early sign of a real edge. If average CLV stays
  negative, the model is not beating the market, whatever the P/L says.

## Known limitations

* No accumulators. Bookmaker margins multiply across legs, and so do model
  errors.
* Team names must match the data source's spelling. The input box suggests
  names as you type.
* No line-up, injury or xG data, only scorelines.
* Automatic fixtures cover football-data.org's free-tier competitions only.
  Internationals (qualifiers, Nations League) and the Danish Superliga stay
  manual.
* The EUROPE model's settings (half-life, κ, κ_league) are untuned
  defaults. Run a backtest once a token is configured.
