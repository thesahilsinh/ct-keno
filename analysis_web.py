#!/usr/bin/env python3
"""Comprehensive number analysis for the CT Keno web dashboard.

Computes decision-support metrics from the scraped store. Bounded to the most
recent `max_draws` draws (default 500) so it stays fast enough for a live
dashboard. Metrics:
  * per-number frequency, last-seen, max-gap, current-gap, due-score
  * rolling frequency (recent window)
  * top pairs / triplets / quadruplets (count + max gap + last occurrence)
  * range distribution (8 bands of 10), odd/even, high/low, sum stats
"""
from collections import Counter, defaultdict
from itertools import combinations
import math


def _norm_date(dd):
    """Normalize a draw_date to ISO (YYYY-MM-DD), or None if empty/invalid.

    The store has historically mixed two formats: ISO (YYYY-MM-DD) and
    MM/DD/YYYY. Both must resolve to the same key so today's full draw count
    is captured.
    """
    if not dd:
        return None
    dd = dd.strip()
    if len(dd) == 10 and dd[4] == "-":
        return dd
    if "/" in dd:
        parts = dd.split("/")
        if len(parts) == 3 and len(parts[2]) == 4:
            m, d, y = parts
            return f"{y}-{int(m):02d}-{int(d):02d}"
    return None


def _today_str(draws):
    """Return the newest ISO draw_date present, or None."""
    dates = [d for d in (_norm_date(x.get("draw_date")) for x in draws) if d]
    return max(dates) if dates else None


def compute_pair_prediction(draws, recent_window=50, top=10):
    """Score every pair and return the top-N with full math breakdown.

    Uses the FULL history (not bounded) for accurate z-scores. Each pair is
    scored on a composite of:
      * frequency z-score vs. the theoretical 6.01% expectation
      * recent momentum (last `recent_window` draws)
      * "due" factor (current gap / max gap)

    Returns a dict with the top pairs and the theoretical baseline.
    """
    if not draws:
        return None

    draws = sorted(draws, key=lambda d: d["game_no"], reverse=True)
    total = len(draws)
    newest = draws[0]["game_no"]

    pair_count = Counter()
    pair_draws = defaultdict(list)
    for d in draws:
        gn = d["game_no"]
        for c in combinations(sorted(d["numbers"]), 2):
            pair_count[c] += 1
            pair_draws[c].append(gn)

    recent = draws[:recent_window]
    recent_count = Counter()
    for d in recent:
        for c in combinations(sorted(d["numbers"]), 2):
            recent_count[c] += 1

    # theoretical probability of any specific pair in a draw
    p_pair = 20 * 19 / (80 * 79)  # 0.0601
    expected = p_pair * total
    std = math.sqrt(total * p_pair * (1 - p_pair))
    recent_expected = recent_window * p_pair
    recent_std = math.sqrt(recent_window * p_pair * (1 - p_pair))

    scored = []
    for c, cnt in pair_count.items():
        gns = sorted(pair_draws[c])
        max_gap = max(gns[i + 1] - gns[i] for i in range(len(gns) - 1)) if len(gns) > 1 else 0
        last = newest - max(gns)
        due = last / max_gap if max_gap > 0 else 0.0
        rec = recent_count.get(c, 0)

        z_freq = (cnt - expected) / std
        z_recent = (rec - recent_expected) / recent_std
        due_factor = min(due, 1.0)

        score = z_freq + 0.5 * z_recent + 0.8 * due_factor
        scored.append({
            "pair": list(c),
            "score": round(score, 2),
            "count": cnt,
            "expected": round(expected, 1),
            "z_freq": round(z_freq, 2),
            "z_recent": round(z_recent, 2),
            "max_gap": max_gap,
            "last": last,
            "due": round(due, 2),
            "recent": rec,
        })

    scored.sort(key=lambda x: -x["score"])

    return {
        "total": total,
        "newest": newest,
        "p_pair": round(p_pair, 4),
        "p_pair_pct": round(p_pair * 100, 2),
        "expected": round(expected, 1),
        "top": scored[:top],
    }


def compute_today_trend(draws, baseline_days=90):
    """Compare today's draws against a 3-month baseline.

    Returns today's hot numbers and pairs with trend z-scores (how far today's
    rate is above/below the baseline rate), plus the top pick.
    """
    if not draws:
        return None

    draws = sorted(draws, key=lambda d: d["game_no"], reverse=True)

    # determine "today" = the newest draw_date present (normalized)
    today_str = _today_str(draws)
    if not today_str:
        return None

    from datetime import date, timedelta
    today = date.fromisoformat(today_str)
    cutoff = (today - timedelta(days=baseline_days)).isoformat()

    today_draws = [d for d in draws if _norm_date(d.get("draw_date")) == today_str]
    baseline = [d for d in draws
                if (nd := _norm_date(d.get("draw_date"))) and nd >= cutoff]

    n_today = len(today_draws)
    n_base = len(baseline)
    if n_today == 0 or n_base == 0:
        return None

    def freq_of(ds):
        c = Counter()
        for d in ds:
            for n in d["numbers"]:
                c[n] += 1
        return c

    def pair_freq_of(ds):
        c = Counter()
        for d in ds:
            for pr in combinations(sorted(d["numbers"]), 2):
                c[pr] += 1
        return c

    today_freq = freq_of(today_draws)
    base_freq = freq_of(baseline)
    today_pairs = pair_freq_of(today_draws)
    base_pairs = pair_freq_of(baseline)

    def trend_z(today_cnt, n_today, base_rate):
        exp = n_today * base_rate
        std = math.sqrt(n_today * base_rate * (1 - base_rate))
        return (today_cnt - exp) / std if std > 0 else 0.0

    # numbers
    num_trend = []
    for n in range(1, 81):
        tc = today_freq.get(n, 0)
        bc = base_freq.get(n, 0)
        br = bc / n_base
        z = trend_z(tc, n_today, br)
        num_trend.append({
            "num": n, "today": tc, "base_pct": round(br * 100, 1),
            "today_pct": round(tc / n_today * 100, 1), "z": round(z, 2),
        })
    num_trend.sort(key=lambda x: -x["z"])

    # pairs
    pair_trend = []
    for c, bc in base_pairs.items():
        tc = today_pairs.get(c, 0)
        br = bc / n_base
        z = trend_z(tc, n_today, br)
        pair_trend.append({
            "pair": list(c), "today": tc, "base_pct": round(br * 100, 2),
            "today_pct": round(tc / n_today * 100, 1), "z": round(z, 2),
        })
    pair_trend.sort(key=lambda x: -x["z"])

    return {
        "today": today_str,
        "n_today": n_today,
        "n_base": n_base,
        "baseline_days": baseline_days,
        "top_numbers": num_trend[:10],
        "top_pairs": pair_trend[:10],
        "pick_numbers": [x["num"] for x in num_trend[:4]],
        "pick_pair": pair_trend[0]["pair"] if pair_trend else [],
    }


def compute_today_analysis(draws):
    """Run compute_analysis on today's draws only (same shape as /api/analysis)."""
    if not draws:
        return None
    draws = sorted(draws, key=lambda d: d["game_no"], reverse=True)
    today_str = _today_str(draws)
    if not today_str:
        return None
    today_draws = [d for d in draws if _norm_date(d.get("draw_date")) == today_str]
    if not today_draws:
        return None
    return compute_analysis(today_draws, recent_window=len(today_draws), max_draws=500)


def compute_analysis(draws, recent_window=50, max_draws=500):
    """Return a rich analysis dict from a list of draw records (newest-first)."""
    if not draws:
        return None

    draws = sorted(draws, key=lambda d: d["game_no"], reverse=True)
    draws = draws[:max_draws]  # bound for speed
    total = len(draws)
    newest = draws[0]["game_no"]

    # ---- per-number stats ----
    freq = Counter()
    last_seen = {}
    seen_draws = defaultdict(list)  # number -> list of game_no (ascending)
    for idx, d in enumerate(draws):
        for n in d["numbers"]:
            freq[n] += 1
            last_seen[n] = idx
            seen_draws[n].append(d["game_no"])

    num_stats = {}
    for n in range(1, 81):
        gns = sorted(seen_draws.get(n, []))
        max_gap = 0
        if len(gns) > 1:
            max_gap = max(gns[i + 1] - gns[i] for i in range(len(gns) - 1))
        cur_gap = last_seen.get(n, total)
        due = cur_gap / max_gap if max_gap > 0 else 0.0
        num_stats[n] = {
            "freq": freq.get(n, 0),
            "last_seen": cur_gap,
            "max_gap": max_gap,
            "due": round(due, 2),
        }

    # ---- rolling frequency (recent window) ----
    recent = draws[:recent_window]
    recent_freq = Counter()
    for d in recent:
        for n in d["numbers"]:
            recent_freq[n] += 1
    for n in range(1, 81):
        num_stats[n]["recent"] = recent_freq.get(n, 0)

    # ---- combinations (single pass, track top-N) ----
    def combo_stats(k, top=20):
        counts = Counter()
        draw_nums = defaultdict(list)
        for d in draws:
            gn = d["game_no"]
            for c in combinations(sorted(d["numbers"]), k):
                counts[c] += 1
                draw_nums[c].append(gn)
        out = []
        for c, cnt in counts.most_common(top):
            gns = sorted(draw_nums[c])
            mg = max(gns[i + 1] - gns[i] for i in range(len(gns) - 1)) if len(gns) > 1 else 0
            lo = newest - max(gns)
            out.append({"combo": list(c), "count": cnt, "max_gap": mg, "last": lo})
        return out

    pairs = combo_stats(2, 20)
    triplets = combo_stats(3, 20)
    quads = combo_stats(4, 20)

    # ---- distributions ----
    ranges = [(1, 10), (11, 20), (21, 30), (31, 40),
              (41, 50), (51, 60), (61, 70), (71, 80)]
    range_counts = Counter()
    odd_even = Counter()
    high_low = Counter()
    sums = []
    for d in draws:
        nums = d["numbers"]
        sums.append(sum(nums))
        for n in nums:
            for s, e in ranges:
                if s <= n <= e:
                    range_counts[f"{s}-{e}"] += 1
                    break
            odd_even["odd" if n % 2 else "even"] += 1
            high_low["high" if n > 40 else "low"] += 1

    return {
        "total": total,
        "newest": newest,
        "recent_window": recent_window,
        "numbers": {str(n): num_stats[n] for n in range(1, 81)},
        "pairs": pairs,
        "triplets": triplets,
        "quads": quads,
        "ranges": {k: range_counts[k] for k in [f"{s}-{e}" for s, e in ranges]},
        "odd_even": dict(odd_even),
        "high_low": dict(high_low),
        "sum_min": min(sums) if sums else 0,
        "sum_max": max(sums) if sums else 0,
        "sum_avg": round(sum(sums) / len(sums), 1) if sums else 0,
    }
