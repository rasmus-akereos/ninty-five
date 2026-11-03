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

    # ------------------------------------------------------------------ fit
    def _weights(self, df: pd.DataFrame, as_of: pd.Timestamp) -> np.ndarray:
        age = (as_of - df["date"]).dt.days.to_numpy(dtype=float)
        decay = np.exp(-np.log(2) * np.clip(age, 0, None) / self.half_life_days)
        comp = df["competition"].map(self.competition_weights).fillna(1.0).to_numpy()
        return decay * comp

    def fit(self, df: pd.DataFrame, as_of: pd.Timestamp | None = None) -> "DixonColes":
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
        kappa = self.kappa

        def nll(theta: np.ndarray) -> tuple[float, np.ndarray]:
            mu, gamma = theta[0], theta[1]
            a, d = theta[2 : 2 + n], theta[2 + n :]
            log_lh = mu + gamma * home + a[hi] - d[ai]
            log_la = mu + a[ai] - d[hi]
            lh, la = np.exp(log_lh), np.exp(log_la)
            ll = np.sum(w * (x * log_lh - lh + y * log_la - la))
            ll -= 0.5 * kappa * (a @ a + d @ d)

            rh, ra = w * (x - lh), w * (y - la)
            g = np.empty_like(theta)
            g[0] = rh.sum() + ra.sum()
            g[1] = (rh * home).sum()
            ga = np.bincount(hi, rh, n) + np.bincount(ai, ra, n) - kappa * a
            gd = -np.bincount(ai, rh, n) - np.bincount(hi, ra, n) - kappa * d
            g[2 : 2 + n], g[2 + n :] = ga, gd
            return -ll, -g

        theta0 = np.zeros(2 + 2 * n)
        theta0[0] = np.log(max((x.mean() + y.mean()) / 2, 0.1))
        res = minimize(nll, theta0, jac=True, method="L-BFGS-B", options={"maxiter": 2000})
        theta = res.x
        self.mu, self.home_adv = float(theta[0]), float(theta[1])
        self.attack, self.defence = theta[2 : 2 + n], theta[2 + n :]

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


def settle(market: str, hg: int, ag: int) -> bool | None:
    """True if `market` wins for final score hg-ag, False if it loses."""
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
    return None


def log_loss_1x2(model: DixonColes, df: pd.DataFrame) -> float:
    """Mean multiclass log loss of 1X2 predictions on matches in df."""
    losses = []
    for r in df.itertuples():
        try:
            mk = markets(model.score_matrix(r.home, r.away, r.neutral))
        except KeyError:
            continue
        outcome = "1" if r.hg > r.ag else ("X" if r.hg == r.ag else "2")
        losses.append(-np.log(max(mk[outcome], 1e-12)))
    return float(np.mean(losses)) if losses else float("nan")

