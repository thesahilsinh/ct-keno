#!/usr/bin/env python3
"""Number analysis for CT Keno draws (reads from data/draws.csv).

Replicates the classic keno number-analysis workflow — frequency, hot/cold,
range division, and pair/triplet/quadruplet frequencies with max-gap and
last-occurrence — but sources data from the scraped store instead of a text
file. The "current draw" is the most recent draw (highest game_no).

Run via CLI:
    python cli.py numbers --draws 100 --outdir analysis
"""
import csv
from collections import defaultdict
from itertools import combinations
from pathlib import Path

import store

RANGES = [(1, 10), (11, 20), (21, 30), (31, 40),
          (41, 50), (51, 60), (61, 70), (71, 80)]


def _load_draws(store_path, max_draws):
    """Load the most recent `max_draws` draws, newest-first (by game_no)."""
    draws = store.load_draws(store_path)
    draws = sorted(draws, key=lambda d: d["game_no"], reverse=True)
    return draws[:max_draws]


def _freq(draws):
    """Counter of number -> count across all draws."""
    freq = defaultdict(int)
    for d in draws:
        for n in d["numbers"]:
            if 1 <= n <= 80:
                freq[n] += 1
    return freq


def _combo_stats(draws, k, valid_numbers=None, min_in_range=0, require_all=False):
    """Count k-combinations and compute max-gap + last-occurrence.

    valid_numbers: if set, filter combos by how many members are in it.
    min_in_range:  minimum members that must be in valid_numbers (0 = all).
    require_all:   if True, every member must be in valid_numbers.
    Returns (counts, max_gaps, last_occ) dicts keyed by sorted tuple.
    """
    counts = defaultdict(int)
    draw_nums = defaultdict(list)
    newest = draws[0]["game_no"] if draws else 0
    for d in draws:
        gn = d["game_no"]
        for combo in combinations(sorted(d["numbers"]), k):
            if valid_numbers is not None:
                in_range = sum(1 for n in combo if n in valid_numbers)
                if require_all and in_range != k:
                    continue
                if not require_all and in_range < min_in_range:
                    continue
            counts[combo] += 1
            draw_nums[combo].append(gn)

    max_gaps, last_occ = {}, {}
    for combo, gns in draw_nums.items():
        s = sorted(gns)
        if len(s) > 1:
            max_gaps[combo] = max(s[i + 1] - s[i] for i in range(len(s) - 1))
        else:
            max_gaps[combo] = 0
        last_occ[combo] = newest - max(s)
    return counts, max_gaps, last_occ


def _write_top(path, title, counts, max_gaps, last_occ, top=20):
    """Write a top-N combo table to `path`."""
    lines = [title, "-" * 80]
    for combo, count in sorted(counts.items(), key=lambda x: (-x[1], x[0]))[:top]:
        lines.append(f"{combo}\t{count}\t{max_gaps.get(combo, 0)}\t{last_occ.get(combo, 0)}")
    Path(path).write_text("\n".join(lines), encoding="utf-8")
    return lines


def run(store_path, outdir, max_draws=100):
    """Run the full number analysis and write output files to `outdir`."""
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    draws = _load_draws(store_path, max_draws)
    if not draws:
        raise SystemExit("No draws in store. Run `python cli.py scrape` first.")

    freq = _freq(draws)
    sorted_freq = sorted(freq.items(), key=lambda x: (-x[1], x[0]))

    # current draw = most recent (newest-first index 0)
    current = draws[0]
    current_nums = current["numbers"]
    current_gn = current["game_no"]

    # ---- 1. frequency ----
    (outdir / "number_frequency_sorted.txt").write_text(
        "\n".join(f"{n}: {f}" for n, f in sorted_freq), encoding="utf-8")

    # ---- 2. first-line (current draw) frequencies ----
    first_line = sorted(
        [(n, freq[n]) for n in current_nums], key=lambda x: (-x[1], x[0]))
    (outdir / "first_line_frequencies_sorted.txt").write_text(
        f"Frequencies for current draw (game #{current_gn}), sorted high→low:\n"
        + "\n".join(f"{n}: {f}" for n, f in first_line), encoding="utf-8")

    # ---- 3. filtered frequencies (0 or 1 number in current draw) ----
    present = [freq[n] for n in current_nums]
    lo, hi = min(present), max(present)
    freq_count = defaultdict(int)
    for n in current_nums:
        freq_count[freq[n]] += 1
    filtered_freqs = [f for f in range(lo, hi + 1) if freq_count.get(f, 0) in (0, 1)]
    numbers_by_freq = {f: [n for n, c in sorted_freq if c == f] for f in filtered_freqs}
    filtered_numbers = []
    lines = ["Numbers you can pick from (frequencies with 0 or 1 number in current draw):"]
    for f in sorted(filtered_freqs, reverse=True):
        nums = numbers_by_freq.get(f, [])
        lines.append(f"{f} - {','.join(map(str, nums)) if nums else 'None'}")
        filtered_numbers.extend(nums)
    (outdir / "filtered_frequencies.txt").write_text("\n".join(lines), encoding="utf-8")

    # ---- 4. frequency number counts ----
    number_counts = defaultdict(list)
    for f in present:
        nums = [n for n in current_nums if freq[n] == f]
        number_counts[freq_count.get(f, 0)].append((f, nums))
    lines = ["Number counts per frequency:"]
    for cnt in sorted(number_counts):
        freqs = sorted(number_counts[cnt], key=lambda x: -x[0])
        lines.append(f"{cnt} number{'s' if cnt != 1 else ''}: "
                     + ", ".join(f"{f} ({','.join(map(str, ns))})" for f, ns in freqs))
    (outdir / "frequency_number_counts.txt").write_text("\n".join(lines), encoding="utf-8")

    # ---- 5. remaining numbers (exclude current draw) ----
    remaining = sorted(n for n in filtered_numbers if n not in current_nums)
    (outdir / "remaining_numbers.txt").write_text(
        "Remaining numbers after excluding current draw:\n"
        + (",".join(map(str, remaining)) if remaining else "None"), encoding="utf-8")

    # ---- 6. range division of current draw ----
    range_counts = {}
    lines = [f"Range division of current draw (game #{current_gn}):"]
    for s, e in RANGES:
        rn = sorted(n for n in current_nums if s <= n <= e)
        range_counts[(s, e)] = len(rn)
        lines.append(f"{s}-{e}: {','.join(map(str, rn)) if rn else 'None'}")
    (outdir / "range_division.txt").write_text("\n".join(lines), encoding="utf-8")

    # ---- 7. selected numbers (ranges with <=2 numbers) ----
    selected_ranges = [r for r, c in range_counts.items() if c <= 2]
    selected = sorted(n for n in remaining
                      if any(s <= n <= e for s, e in selected_ranges))
    labels = ", ".join(f"{s}-{e}" for s, e in selected_ranges)
    (outdir / "selected_numbers.txt").write_text(
        f"Selected numbers from ranges with 0/1/2 numbers ({labels}):\n"
        + (",".join(map(str, selected)) if selected else "None"), encoding="utf-8")

    # ---- 8. range frequency (ranges with 0 or 1 numbers) ----
    selected_01 = [r for r, c in range_counts.items() if c in (0, 1)]
    valid_numbers = set()
    lines = [f"Frequency analysis for ranges with 0/1 numbers (game #{current_gn}):"]
    for s, e in selected_01:
        rn = [n for n in range(s, e + 1)]
        valid_numbers.update(rn)
        lines.append(f"{s}-{e}:")
        lines.extend(f"  {n}: {freq[n]}" for n in sorted(rn, key=lambda x: -freq[x]))
    (outdir / "range_frequency_analysis.txt").write_text("\n".join(lines), encoding="utf-8")

    # ---- 9. pairs / triplets / quadruplets ----
    newest = draws[0]["game_no"]
    rng_label = ", ".join(f"{s}-{e}" for s, e in selected_01)

    # pairs
    c, g, l = _combo_stats(draws, 2)
    _write_top(outdir / "pair_frequencies.txt", "Top 20 Pairs\tOccurrences\tMax Gap\tLast Occurrence", c, g, l)
    c, g, l = _combo_stats(draws, 2, valid_numbers, min_in_range=1)
    _write_top(outdir / "filtered_pair_frequencies.txt",
               f"Top 20 Pairs (≥1 in {rng_label})\tOccurrences\tMax Gap\tLast Occurrence", c, g, l)
    c, g, l = _combo_stats(draws, 2, valid_numbers, require_all=True)
    _write_top(outdir / "strict_filtered_pair_frequencies.txt",
               f"Top 20 Pairs (both in {rng_label})\tOccurrences\tMax Gap\tLast Occurrence", c, g, l)

    # triplets
    c, g, l = _combo_stats(draws, 3)
    _write_top(outdir / "triplet_frequencies.txt", "Top 20 Triplets\tOccurrences\tMax Gap\tLast Occurrence", c, g, l)
    c, g, l = _combo_stats(draws, 3, valid_numbers, min_in_range=1)
    _write_top(outdir / "filtered_triplet_frequencies.txt",
               f"Top 20 Triplets (≥1 in {rng_label})\tOccurrences\tMax Gap\tLast Occurrence", c, g, l)
    c, g, l = _combo_stats(draws, 3, valid_numbers, min_in_range=2)
    _write_top(outdir / "two_filtered_triplet_frequencies.txt",
               f"Top 20 Triplets (≥2 in {rng_label})\tOccurrences\tMax Gap\tLast Occurrence", c, g, l)
    c, g, l = _combo_stats(draws, 3, valid_numbers, require_all=True)
    _write_top(outdir / "strict_filtered_triplet_frequencies.txt",
               f"Top 20 Triplets (all 3 in {rng_label})\tOccurrences\tMax Gap\tLast Occurrence", c, g, l)

    # quadruplets
    c, g, l = _combo_stats(draws, 4)
    _write_top(outdir / "quadruplet_frequencies.txt", "Top 20 Quadruplets\tOccurrences\tMax Gap\tLast Occurrence", c, g, l)
    c, g, l = _combo_stats(draws, 4, valid_numbers, min_in_range=1)
    _write_top(outdir / "filtered_quadruplet_frequencies.txt",
               f"Top 20 Quadruplets (≥1 in {rng_label})\tOccurrences\tMax Gap\tLast Occurrence", c, g, l)
    c, g, l = _combo_stats(draws, 4, valid_numbers, min_in_range=2)
    _write_top(outdir / "two_filtered_quadruplet_frequencies.txt",
               f"Top 20 Quadruplets (≥2 in {rng_label})\tOccurrences\tMax Gap\tLast Occurrence", c, g, l)
    c, g, l = _combo_stats(draws, 4, valid_numbers, min_in_range=3)
    _write_top(outdir / "three_filtered_quadruplet_frequencies.txt",
               f"Top 20 Quadruplets (≥3 in {rng_label})\tOccurrences\tMax Gap\tLast Occurrence", c, g, l)
    c, g, l = _combo_stats(draws, 4, valid_numbers, require_all=True)
    _write_top(outdir / "strict_filtered_quadruplet_frequencies.txt",
               f"Top 20 Quadruplets (all 4 in {rng_label})\tOccurrences\tMax Gap\tLast Occurrence", c, g, l)

    # ---- 10. common_4 (quadruplets containing top pairs) ----
    pair_counts, _, _ = _combo_stats(draws, 2)
    top_pairs = [p for p, _ in sorted(pair_counts.items(), key=lambda x: -x[1])[:10]]
    quad_counts, quad_gaps, quad_last = _combo_stats(draws, 4)
    matching = []
    for quad, count in quad_counts.items():
        qp = list(combinations(quad, 2))
        mp = [p for p in qp if p in top_pairs]
        if len(mp) >= 2:
            matching.append((quad, count, quad_gaps.get(quad, 0),
                             quad_last.get(quad, 0), mp))
    matching.sort(key=lambda x: (-len(x[4]), -x[1]))
    lines = ["Quadruplets containing Top Pairs", "=" * 80,
             f"\nTop 10 Pairs: {top_pairs}\n", "-" * 80,
             "\nQuadruplets containing 2+ top pairs:\n",
             "Quadruplet\tOccurrences\tMax Gap\tLast Occurrence\tContains Pairs", "-" * 80]
    for quad, count, mg, lo, pairs in matching:
        lines.append(f"{quad}\t{count}\t{mg}\t{lo}\t{', '.join(map(str, pairs))}")
    (outdir / "common_4.txt").write_text("\n".join(lines), encoding="utf-8")

    # ---- summary to stdout ----
    print(f"Analyzed {len(draws)} draws (newest game #{newest}).")
    print(f"Current draw numbers: {sorted(current_nums)}")
    print(f"Hot (top 10): {[n for n, _ in sorted_freq[:10]]}")
    print(f"Cold (bottom 10): {[n for n, _ in sorted_freq[-10:]]}")
    print(f"Selected numbers: {selected if selected else 'None'}")
    print(f"Output written to {outdir}/")
    return outdir
