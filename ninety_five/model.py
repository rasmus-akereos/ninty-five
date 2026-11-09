"""Dixon-Coles goal model.

Method
------
Each team i has an attack strength a_i and a defence strength d_i. For a match
home team h vs away team v the expected goals are

    lambda_home = exp(mu + gamma * H + a_h - d_v)
    lambda_away = exp(mu +             a_v - d_h)

where mu is the league-average log scoring rate, gamma the home advantage and
H = 0 for neutral venues (most World Cup / tournament matches), 1 otherwise.

Goals are modelled as independent Poisson variables, with the Dixon & Coles
(1997) correction tau(x, y) that adjusts the four low-score cells 0-0, 1-0,
0-1, 1-1 (independent Poisson under-predicts draws):

    tau(0,0) = 1 - lambda_h * lambda_a * rho
    tau(0,1) = 1 + lambda_h * rho
    tau(1,0) = 1 + lambda_a * rho
    tau(1,1) = 1 - rho

Fitting maximises a weighted log-likelihood

    sum_k w_k * [log P(x_k | lambda_h,k) + log P(y_k | lambda_a,k)]
        - (kappa / 2) * sum_i (a_i^2 + d_i^2)

* w_k = exp(-ln2 * age_days / half_life) * competition_weight, so old results
  fade out and friendlies count less than competitive matches.
* The ridge penalty kappa replaces the usual sum-to-zero constraint. It
  identifies the model and shrinks teams with few matches toward average,
  which matters a lot for national teams that play ~10 games a year.

Multi-league fits (e.g. Champions League + domestic leagues) pass `groups`,
mapping each team to its domestic league. Strengths then decompose as

    a_i = A_g(i) + u_i,      d_i = D_g(i) + v_i

with the ridge kappa on the team deviations (u, v) and a weaker ridge
kappa_group on the league offsets (A, D). Domestic matches only inform u, v
(league offsets cancel within a league); European matches identify A, D. A
club with few matches is shrunk to its *league's* level rather than to the
overall average, which is what makes cross-league prices sensible.

The Poisson part has a closed-form gradient and is fitted with L-BFGS. rho is
then fitted as a 1-D problem holding the lambdas fixed (two-stage
approximation; rho barely interacts with the strength parameters).
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from scipy.optimize import minimize, minimize_scalar
from scipy.stats import poisson

MAX_GOALS = 10

# Walk-forward tests on internationals (2023-2026) found down-weighting
# friendlies did not improve 1X2 log loss, so all competitions count equally by
# default. Override per dataset if needed.
DEFAULT_COMPETITION_WEIGHTS: dict[str, float] = {}

# Defaults per data universe. International: tuned by walk-forward log loss
# (refit every 6 months 2023-2026, scored on competitive matches); the surface
# is flat for half-life 3-4 years and kappa <= 0.5. Clubs: the Dixon-Coles
# paper's xi = 0.0065 per half-week, i.e. a half-life of roughly one year.
INTL_PARAMS = {"half_life_days": 1095.0, "kappa": 0.2}
CLUB_PARAMS = {"half_life_days": 365.0, "kappa": 0.2}


@dataclass
class DixonColes:
    half_life_days: float = 1095.0
    kappa: float = 0.2
    kappa_group: float = 0.05
    competition_weights: dict[str, float] = field(
        default_factory=lambda: dict(DEFAULT_COMPETITION_WEIGHTS)
    )

    # fitted state
    teams: list[str] = field(default_factory=list)
    attack: np.ndarray | None = None
    defence: np.ndarray | None = None
    mu: float = 0.0
    home_adv: float = 0.0
    rho: float = 0.0
    as_of: pd.Timestamp | None = None
    n_matches: dict[str, float] = field(default_factory=dict)
    groups: dict[str, str] = field(default_factory=dict)
    group_strength: dict[str, tuple[float, float]] = field(default_factory=dict)

    # ------------------------------------------------------------------ fit
    def _weights(self, df: pd.DataFrame, as_of: pd.Timestamp) -> np.ndarray:
        age = (as_of - df["date"]).dt.days.to_numpy(dtype=float)
        decay = np.exp(-np.log(2) * np.clip(age, 0, None) / self.half_life_days)
        comp = df["competition"].map(self.competition_weights).fillna(1.0).to_numpy()
        return decay * comp

    def fit(
        self,
        df: pd.DataFrame,
        as_of: pd.Timestamp | None = None,
        groups: dict[str, str] | None = None,
    ) -> "DixonColes":
        as_of = pd.Timestamp(as_of) if as_of is not None else df["date"].max()
        df = df[df["date"] <= as_of]
        w = self._weights(df, as_of)
        keep = w > 1e-4
        df, w = df[keep], w[keep]

        self.teams = sorted(set(df["home"]) | set(df["away"]))
        idx = {t: i for i, t in enumerate(self.teams)}
        n = len(self.teams)
        hi = df["home"].map(idx).to_numpy()
        ai = df["away"].map(idx).to_numpy()
        x = df["hg"].to_numpy(dtype=float)
        y = df["ag"].to_numpy(dtype=float)
        home = (~df["neutral"].to_numpy(dtype=bool)).astype(float)
        kappa, kappa_g = self.kappa, self.kappa_group

        # league groups (a single group == the plain model with an extra
        # offset absorbed by mu, so it is skipped)
        groups = groups or {}
        group_names = sorted({groups.get(t, "OTHER") for t in self.teams}) if groups else []
        G = len(group_names)
        gidx = {g: k for k, g in enumerate(group_names)}
        team_g = np.array([gidx[groups.get(t, "OTHER")] for t in self.teams], dtype=int) if G else np.zeros(n, int)

        def unpack(theta):
            u, v = theta[2 : 2 + n], theta[2 + n : 2 + 2 * n]
            A, D = theta[2 + 2 * n : 2 + 2 * n + G], theta[2 + 2 * n + G :]
            a = u + (A[team_g] if G else 0.0)
            d = v + (D[team_g] if G else 0.0)
            return u, v, A, D, a, d

        def nll(theta: np.ndarray) -> tuple[float, np.ndarray]:
            mu, gamma = theta[0], theta[1]
            u, v, A, D, a, d = unpack(theta)
            log_lh = mu + gamma * home + a[hi] - d[ai]
            log_la = mu + a[ai] - d[hi]
            lh, la = np.exp(log_lh), np.exp(log_la)
            ll = np.sum(w * (x * log_lh - lh + y * log_la - la))
            ll -= 0.5 * kappa * (u @ u + v @ v) + 0.5 * kappa_g * (A @ A + D @ D)

            rh, ra = w * (x - lh), w * (y - la)
            g = np.empty_like(theta)
            g[0] = rh.sum() + ra.sum()
            g[1] = (rh * home).sum()
            ga = np.bincount(hi, rh, n) + np.bincount(ai, ra, n)   # d ll / d a_i
            gd = -np.bincount(ai, rh, n) - np.bincount(hi, ra, n)  # d ll / d d_i
            g[2 : 2 + n] = ga - kappa * u
            g[2 + n : 2 + 2 * n] = gd - kappa * v
            if G:
                g[2 + 2 * n : 2 + 2 * n + G] = np.bincount(team_g, ga, G) - kappa_g * A
                g[2 + 2 * n + G :] = np.bincount(team_g, gd, G) - kappa_g * D
            return -ll, -g

        theta0 = np.zeros(2 + 2 * n + 2 * G)
        theta0[0] = np.log(max((x.mean() + y.mean()) / 2, 0.1))
        res = minimize(nll, theta0, jac=True, method="L-BFGS-B", options={"maxiter": 5000})
        theta = res.x
        self.mu, self.home_adv = float(theta[0]), float(theta[1])
        _, _, A, D, self.attack, self.defence = unpack(theta)
        self.groups = {t: groups.get(t, "OTHER") for t in self.teams} if G else {}
        self.group_strength = {g: (float(A[k]), float(D[k])) for g, k in gidx.items()}

        # stage 2: rho
        lh = np.exp(self.mu + self.home_adv * home + self.attack[hi] - self.defence[ai])
        la = np.exp(self.mu + self.attack[ai] - self.defence[hi])

        def neg_ll_rho(rho: float) -> float:
            t = np.ones_like(lh)
            m00, m01 = (x == 0) & (y == 0), (x == 0) & (y == 1)
            m10, m11 = (x == 1) & (y == 0), (x == 1) & (y == 1)
            t[m00] = 1 - lh[m00] * la[m00] * rho
            t[m01] = 1 + lh[m01] * rho
            t[m10] = 1 + la[m10] * rho
            t[m11] = 1 - rho
            if np.any(t <= 0):
                return 1e12
            return -float(np.sum(w * np.log(t)))

        self.rho = float(minimize_scalar(neg_ll_rho, bounds=(-0.2, 0.2), method="bounded").x)
        self.as_of = as_of
        counts = pd.concat([df["home"], df["away"]]).value_counts()
        self.n_matches = counts.to_dict()
        return self

    # -------------------------------------------------------------- predict
    def _team(self, name: str) -> int:
        try:
            return self.teams.index(name)
        except ValueError:
            raise KeyError(f"Unknown team {name!r}") from None

    def expected_goals(self, home: str, away: str, neutral: bool = False) -> tuple[float, float]:
        h, a = self._team(home), self._team(away)
        g = 0.0 if neutral else self.home_adv
        lh = np.exp(self.mu + g + self.attack[h] - self.defence[a])
        la = np.exp(self.mu + self.attack[a] - self.defence[h])
        return float(lh), float(la)

    def score_matrix(self, home: str, away: str, neutral: bool = False) -> np.ndarray:
        lh, la = self.expected_goals(home, away, neutral)
        return score_matrix(lh, la, self.rho)

    def ratings(self) -> pd.DataFrame:
        df = pd.DataFrame(
            {
                "team": self.teams,
                "attack": self.attack,
                "defence": self.defence,
                "matches": [self.n_matches.get(t, 0) for t in self.teams],
            }
        )
        if self.groups:
            df["league"] = [self.groups.get(t, "") for t in self.teams]
        df["overall"] = df["attack"] + df["defence"]
        return df.sort_values("overall", ascending=False).reset_index(drop=True)


def score_matrix(lh: float, la: float, rho: float, max_goals: int = MAX_GOALS) -> np.ndarray:
    """P(home = i, away = j) for i, j in 0..max_goals, with DC correction."""
    g = np.arange(max_goals + 1)
    m = np.outer(poisson.pmf(g, lh), poisson.pmf(g, la))
    m[0, 0] *= 1 - lh * la * rho
    m[0, 1] *= 1 + lh * rho
    m[1, 0] *= 1 + la * rho
    m[1, 1] *= 1 - rho
    return m / m.sum()


def markets(m: np.ndarray) -> dict[str, float]:
    """Derive betting-market probabilities from a score matrix."""
    n = m.shape[0]
    i, j = np.indices(m.shape)
    total = i + j
    p_home, p_draw, p_away = m[i > j].sum(), np.trace(m), m[i < j].sum()
    out = {
        "1": p_home,
        "X": p_draw,
        "2": p_away,
        "1X": p_home + p_draw,
        "12": p_home + p_away,
        "X2": p_draw + p_away,
        "BTTS Yes": m[1:, 1:].sum(),
        "BTTS No": 1 - m[1:, 1:].sum(),
    }
    for line in (0.5, 1.5, 2.5, 3.5, 4.5):
        over = m[total > line].sum()
        out[f"Over {line}"] = over
        out[f"Under {line}"] = 1 - over
    return {k: float(v) for k, v in out.items()}


COMBO_SEP = " + "  # a Bet Builder bet: "X + Under 2.5" wins when every leg does


def market_mask(market: str, shape: tuple[int, int]) -> np.ndarray:
    """Boolean score grid: True where `market` wins. ValueError for markets
    the app doesn't know."""
    i, j = np.indices(shape)
    total = i + j
    masks = {"1": i > j, "X": i == j, "2": i < j, "1X": i >= j, "12": i != j, "X2": i <= j,
             "BTTS Yes": (i > 0) & (j > 0), "BTTS No": (i == 0) | (j == 0)}
    if market in masks:
        return masks[market]
    side, _, line = market.partition(" ")
    if side in ("Over", "Under"):
        try:
            x = float(line)
        except ValueError:
            pass
        else:
            return total > x if side == "Over" else total < x
    raise ValueError(f"Unknown market {market!r}")


def combo_probability(m: np.ndarray, legs: list[str]) -> float:
    """P(every leg wins) from a score matrix: the scores where all legs win.
    Legs of one match are correlated, so this is not the product of the singles."""
    mask = np.ones(m.shape, dtype=bool)
    for leg in legs:
        mask &= market_mask(leg, m.shape)
    return float(m[mask].sum())


TOGETHER = 1.25  # all-lose this many times likelier than for unrelated bets


def overlap(m: np.ndarray, bets: list[str]) -> dict:
    """How much several bets on one match win and lose together: the chance
    they all lose, against the chance if they were unrelated. `together` when
    the shared scorelines make losing them all clearly likelier."""
    none_win = np.ones(m.shape, dtype=bool)
    independent = 1.0
    for b in bets:
        mask = combo_mask(b, m.shape)
        none_win &= ~mask
        independent *= 1 - float(m[mask].sum())
    p_all_lose = float(m[none_win].sum())
    return {"markets": list(bets), "p_all_lose": p_all_lose, "p_all_lose_independent": independent,
            "together": len(bets) >= 2 and p_all_lose > TOGETHER * independent}


def combo_mask(market: str, shape: tuple[int, int]) -> np.ndarray:
    """market_mask for a single market or a Bet Builder combination."""
    mask = np.ones(shape, dtype=bool)
    for leg in market.split(COMBO_SEP):
        mask &= market_mask(leg, shape)
    return mask


def settle(market: str, hg: int, ag: int) -> bool | None:
    """True if `market` wins for final score hg-ag, False if it loses."""
    if COMBO_SEP in market:
        legs = [settle(leg, hg, ag) for leg in market.split(COMBO_SEP)]
        return None if None in legs else all(legs)
    total = hg + ag
    if market == "1":
        return hg > ag
    if market == "X":
        return hg == ag
    if market == "2":
        return hg < ag
    if market == "1X":
        return hg >= ag
    if market == "X2":
        return hg <= ag
    if market == "12":
        return hg != ag
    if market == "BTTS Yes":
        return hg > 0 and ag > 0
    if market == "BTTS No":
        return not (hg > 0 and ag > 0)
    if market.startswith("Over "):
        return total > float(market.split()[1])
    if market.startswith("Under "):
        return total < float(market.split()[1])
    # NFL / NBA (us_sports.py): moneyline and point spread on the final score.
    if market == "ML 1":
        return hg > ag
    if market == "ML 2":
        return hg < ag
    if market.startswith("Spread "):
        _, side, line = market.split()
        return hg + float(line) > ag if side == "1" else ag + float(line) > hg
    return None


def is_push(market: str, hg: int, ag: int) -> bool:
    """True when the stake comes back: a whole-number spread or total landing
    exactly on the line, or a tied moneyline (NFL)."""
    if market in ("ML 1", "ML 2"):
        return hg == ag
    if market.startswith("Spread "):
        _, side, line = market.split()
        return (hg + float(line) == ag) if side == "1" else (ag + float(line) == hg)
    if market.startswith(("Over ", "Under ")) and COMBO_SEP not in market:
        return hg + ag == float(market.split()[1])
    return False


def match_losses_1x2(model: DixonColes, df: pd.DataFrame) -> np.ndarray:
    """Per-row 1X2 log loss on df; NaN where a team is unknown to the model."""
    losses = []
    for r in df.itertuples():
        try:
            mk = markets(model.score_matrix(r.home, r.away, r.neutral))
        except KeyError:
            losses.append(np.nan)
            continue
        outcome = "1" if r.hg > r.ag else ("X" if r.hg == r.ag else "2")
        losses.append(-np.log(max(mk[outcome], 1e-12)))
    return np.array(losses, dtype=float)


def log_loss_1x2(model: DixonColes, df: pd.DataFrame) -> float:
    """Mean multiclass log loss of 1X2 predictions on matches in df."""
    losses = match_losses_1x2(model, df)
    losses = losses[~np.isnan(losses)]
    return float(np.mean(losses)) if len(losses) else float("nan")
