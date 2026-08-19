"""Dedup-aware CSV store for keno draws.

One row per draw, primary key = game_no. Re-running the scraper over the same
date range adds zero duplicates, so refresh is safe and idempotent.
"""
import csv
from pathlib import Path

FIELDS = ["game_no", "draw_date", "bonus", "numbers"]


def append_draws(rows: list, path: Path) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = {r["game_no"] for r in load_draws(path)}
    fresh = [r for r in rows if r["game_no"] not in existing]
    write_header = not path.exists()
    with path.open("a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        if write_header:
            w.writeheader()
        for r in fresh:
            w.writerow({**r, "numbers": "-".join(map(str, r["numbers"]))})
    return len(fresh)


def load_draws(path: Path) -> list:
    if not path.exists():
        return []
    out = []
    with path.open(newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            out.append({
                **r,
                "game_no": int(r["game_no"]),
                "numbers": [int(x) for x in r["numbers"].split("-")],
            })
    return out
