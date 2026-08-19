#!/usr/bin/env python3
"""Scrape today's draws and build a compact data/draws.json for the static site.

Runs in GitHub Actions (or locally). Uses only the Python standard library.
The frontend (web/index.html) fetches data/draws.json and renders the dashboard.
"""
import json
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import scraper
import store
import analysis_web

ROOT = Path(__file__).resolve().parent
STORE = ROOT / "data" / "draws.csv"
OUT = ROOT / "data" / "draws.json"


def main():
    # Scrape today + yesterday (Eastern time via TZ env in the workflow).
    today = date.today()
    added = 0
    for d in (today, today - timedelta(days=1)):
        try:
            rows = scraper.fetch_day(d.strftime("%m/%d/%Y"), draw_date=d.isoformat())
            added += store.append_draws(rows, STORE)
        except Exception as e:
            print(f"  [scrape] {d.isoformat()} failed: {e}")

    draws = store.load_draws(STORE)
    if not draws:
        print("no draws in store; aborting")
        return

    draws = sorted(draws, key=lambda d: d["game_no"], reverse=True)
    total = len(draws)
    newest = draws[0]["game_no"]

    # Rich analysis (bounded for speed) + today trend + pair prediction.
    analysis = analysis_web.compute_analysis(draws, recent_window=50, max_draws=500)
    today_trend = analysis_web.compute_today_trend(draws, baseline_days=90)
    prediction = analysis_web.compute_pair_prediction(draws, recent_window=50)
    today_analysis = analysis_web.compute_today_analysis(draws)

    # Compact per-number stats for the board + hot/cold/overdue.
    freq = Counter()
    last_seen = {}
    for idx, d in enumerate(draws):
        for n in d["numbers"]:
            freq[n] += 1
            last_seen[n] = idx

    per_day = Counter()
    for d in draws:
        nd = analysis_web._norm_date(d.get("draw_date"))
        if nd:
            per_day[nd] += 1
    day_trend = [{"date": k, "count": per_day[k]} for k in sorted(per_day)]

    data = {
        "generated": date.today().isoformat(),
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "total_draws": total,
        "newest_game": newest,
        "expected_per_num": round(total * 20 / 80, 1),
        "freq": {str(n): freq.get(n, 0) for n in range(1, 81)},
        "last_seen": {str(n): last_seen.get(n, total) for n in range(1, 81)},
        "day_trend": day_trend[-14:],
        "latest_draws": [
            {"game_no": d["game_no"], "bonus": d["bonus"], "numbers": d["numbers"]}
            for d in draws[:20]
        ],
        "analysis": analysis,
        "today_trend": today_trend,
        "today_analysis": today_analysis,
        "prediction": prediction,
    }

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(data), encoding="utf-8")
    print(f"wrote {OUT}  ({total} draws, +{added} new, newest #{newest})")


if __name__ == "__main__":
    main()
