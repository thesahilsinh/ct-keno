"""Summary statistics + theoretical house edge for keno strategies."""
from math import comb
from collections import Counter

from payouts import PAYOUTS, bonus_to_mult


def summarize(res) -> dict:
    """Turn a simulate.Result into human-readable stats."""
    cum = 0
    streak = 0
    worst = 0
    maxdd = 0
    hits = 0
    for x in res.per_draw:
        cum += x
        if x > 0:
            hits += 1
            streak = 0
        else:
            streak += 1
            worst = max(worst, streak)
        maxdd = min(maxdd, cum)
    n = len(res.per_draw)
    roi = round(100 * res.net / res.total_wagered, 2) if res.total_wagered else 0.0
    return {
        "draws": n,
        "total_wagered": round(res.total_wagered, 2),
        "total_won": round(res.total_won, 2),
        "net": round(res.net, 2),
        "roi_pct": roi,
        "hit_rate_pct": round(100 * hits / n, 2) if n else 0.0,
        "longest_losing_streak": worst,
        "max_drawdown": round(maxdd, 2),
    }


def distribution_stats(nets: list) -> dict:
    """Percentiles + profitable-session rate for a Monte-Carlo net list."""
    if not nets:
        return {}
    s = sorted(nets)
    n = len(s)
    pct = lambda q: s[min(n - 1, int(q * n))]
    profitable = sum(1 for x in nets if x > 0)
    return {
        "sessions": n,
        "mean_net": round(sum(nets) / n, 2),
        "p10": round(pct(0.10), 2),
        "p50": round(pct(0.50), 2),
        "p90": round(pct(0.90), 2),
        "min": round(s[0], 2),
        "max": round(s[-1], 2),
        "pct_profitable": round(100 * profitable / n, 2),
    }


def bonus_frequency(draws: list) -> dict:
    """Observed frequency of each bonus multiplier in a draw list."""
    c = Counter(bonus_to_mult(d["bonus"]) for d in draws)
    total = sum(c.values()) or 1
    return {k: v / total for k, v in sorted(c.items())}


def theoretical_house_edge(spot: int, wager: float, bonus: bool, draws: list = None) -> float:
    """Expected net per draw.

    Non-bonus: E[prize] - wager, from combinatorics.
    Bonus: E[prize * bonus_mult] - 2*wager, weighting each multiplier by its
    observed frequency in `draws` (pass a real draw list for accuracy).
    """
    total = comb(80, 20)
    base_ev = 0.0
    for match, prize in PAYOUTS.get(spot, {}).items():
        ways = comb(spot, match) * comb(80 - spot, 20 - match)
        base_ev += prize * (ways / total)
    if not bonus:
        return base_ev - wager
    if draws:
        freq = bonus_frequency(draws)
        mult_ev = sum(m * f for m, f in freq.items())
    else:
        mult_ev = sum(PAYOUTS and m for m in [1, 2, 3, 4, 5, 10]) / 6.0  # rough fallback
    return base_ev * mult_ev - 2 * wager
