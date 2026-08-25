"""Dedup-aware CSV store for keno draws.

One row per draw, primary key = game_no. Re-running the scraper over the same
date range adds zero duplicates, so refresh is safe and idempotent.

Schema (5 columns): game_no, draw_date, draw_time, bonus, numbers

The store is self-healing: `load_draws` tolerates the legacy 4-column schema
(game_no, draw_date, bonus, numbers) and `append_draws` rewrites the whole file
in the current 5-column schema, so a mixed/legacy file is normalized on the
next write.
"""
import csv
from pathlib import Path

FIELDS = ["game_no", "draw_date", "draw_time", "bonus", "numbers"]


def _read_rows(path: Path) -> dict:
    """Read all rows keyed by game_no, tolerating 4- or 5-column schemas."""
    if not path.exists():
        return {}
    rows = {}
    with path.open(newline="", encoding="utf-8") as f:
        reader = csv.reader(f)
        header = next(reader, None)
        if header is None:
            return {}
        for row in reader:
            if not row:
                continue
            if len(row) == 4:
                game_no, draw_date, bonus, numbers = row
                draw_time = ""
            elif len(row) == 5:
                game_no, draw_date, draw_time, bonus, numbers = row
            else:
                continue
            rows[game_no] = {
                "game_no": game_no,
                "draw_date": draw_date,
                "draw_time": draw_time,
                "bonus": bonus,
                "numbers": numbers,
            }
    return rows


def append_draws(rows: list, path: Path) -> int:
    """Merge `rows` into the store (deduped by game_no), rewriting in the
    current schema. Returns the number of NEW rows added."""
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = _read_rows(path)
    fresh = [r for r in rows if str(r["game_no"]) not in existing]
    for r in fresh:
        existing[str(r["game_no"])] = {
            "game_no": str(r["game_no"]),
            "draw_date": r.get("draw_date", ""),
            "draw_time": r.get("draw_time", ""),
            "bonus": r.get("bonus", "No Bonus"),
            "numbers": "-".join(map(str, r["numbers"])),
        }
    # rewrite whole file in canonical schema, newest-first
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(FIELDS)
        for game_no in sorted(existing, key=int, reverse=True):
            r = existing[game_no]
            w.writerow([r["game_no"], r["draw_date"], r["draw_time"],
                        r["bonus"], r["numbers"]])
    return len(fresh)


def load_draws(path: Path) -> list:
    """Load all draws as records (newest-first), numbers parsed to ints."""
    rows = _read_rows(path)
    out = []
    for game_no in sorted(rows, key=int, reverse=True):
        r = rows[game_no]
        out.append({
            "game_no": int(r["game_no"]),
            "draw_date": r["draw_date"],
            "draw_time": r["draw_time"],
            "bonus": r["bonus"],
            "numbers": [int(x) for x in r["numbers"].split("-") if x],
        })
    return out
