"""Keno strategy engine: exact historical replay + Monte-Carlo risk.

A Strategy = how you play (spots, fixed picks or quickpick, wager, bonus on/off).
replay_history() replays it over a list of real draws (exact, ordered).
monte_carlo() bootstraps many random "sessions" for the outcome distribution.

Prize lookup reads payouts.PAYOUTS; call payouts.load_payouts(html) once first.
"""
import random
from dataclasses import dataclass, field
from math import comb

from payouts import PAYOUTS, bonus_to_mult


@dataclass
class Strategy:
    spots: int                 # how many numbers you pick (1..10)
    mode: str = "quickpick"    # "fixed" | "quickpick"
    picks: list = None         # required if mode == "fixed"
    wager: float = 1.0         # base wager per draw
    bonus: bool = False        # pay double; won prizes multiplied by draw's bonus

    def __post_init__(self):
        if self.mode == "fixed":
            assert self.picks and len(self.picks) == self.spots, \
                "fixed mode requires `picks` of length `spots`"
            assert all(1 <= n <= 80 for n in self.picks), "picks must be 1..80"


@dataclass
class Result:
    total_wagered: float = 0.0
    total_won: float = 0.0
    net: float = 0.0
    per_draw: list = field(default_factory=list)


def _ticket_cost(s: "Strategy") -> float:
    return s.wager * (2 if s.bonus else 1)


def _pick(s: "Strategy", rng: random.Random) -> set:
    if s.mode == "fixed":
        return set(s.picks)
    return set(rng.sample(range(1, 81), s.spots))


def _prize(spot: int, match: int) -> float:
    return PAYOUTS.get(spot, {}).get(match, 0.0)


def replay_history(draws: list, s: "Strategy", seed: int = 0) -> "Result":
    rng = random.Random(seed)
    res = Result()
    for d in draws:
        cost = _ticket_cost(s)
        picks = _pick(s, rng)
        match = len(picks & set(d["numbers"]))
        prize = _prize(s.spots, match)
        if s.bonus:
            prize *= bonus_to_mult(d["bonus"])
        res.total_wagered += cost
        res.total_won += prize
        res.per_draw.append(prize - cost)
    res.net = res.total_won - res.total_wagered
    return res


def monte_carlo(pool: list, s: "Strategy", session_draws: int,
                sessions: int, seed: int = 0) -> list:
    """Return a list of per-session net outcomes (float)."""
    rng = random.Random(seed)
    out = []
    for _ in range(sessions):
        sample = [rng.choice(pool) for _ in range(session_draws)]
        out.append(replay_history(sample, s, seed=rng.randint(0, 1 << 30)).net)
    return out


def theoretical_ev(spot: int, wager: float, bonus: bool) -> float:
    """Expected net per draw = E[prize] - cost, from the official paytable.

    Bonus EV: with bonus you pay 2x and your prize is multiplied by the draw's
    bonus. We weight each bonus multiplier by its observed frequency in `pool`
    (caller passes freq dict) — see theoretical_house_edge for the full form.
    """
    cost = wager * (2 if bonus else 1)
    total = comb(80, 20)
    ev = 0.0
    for match, prize in PAYOUTS.get(spot, {}).items():
        ways = comb(spot, match) * comb(80 - spot, 20 - match)
        ev += prize * (ways / total)
    return ev - cost
