#!/usr/bin/env python3
"""
CT Keno scraper worker: polls every 2 minutes for today's draw.

- On startup: scrapes last 90 days of draws (to bootstrap the store)
- Then enters a 2-minute polling loop checking for today's new draws
- Keno draws appear ~every 4-5 minutes after the last draw of the day;
  polling every 2 minutes catches them.
- Uses the same parsing as scraper.py

Usage:
    python scraper_worker.py
"""
import time, sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import scraper
import store as store_mod

STORE = ROOT / "data" / "draws.csv"


def load_game_numbers():
    """Load existing game numbers from store for dedup."""
    draws = store_mod.load_draws(STORE)
    return {d["game_no"] for d in draws}


def search_today():
    """Check for today's draws every 2 minutes."""
    today = date.today()
    today_str = today.strftime("%m/%d/%Y")
    seen = load_game_numbers()

    prev_day = today - timedelta(days=1)
    prev_str = prev_day.strftime("%m/%d/%Y")

    total_added = 0
    for day in [prev_str, today_str]:
        try:
            # day is MM/DD/YYYY; convert to ISO for the draw_date stamp
            m, d, y = day.split("/")
            iso = f"{y}-{int(m):02d}-{int(d):02d}"
            rows = scraper.fetch_day(day, draw_date=iso)
            new = [r for r in rows if r["game_no"] not in seen]
            for dr in new:
                seen.add(dr["game_no"])
            if new:
                store_mod.append_draws(new, STORE)
                total_added += len(new)
        except Exception as e:
            pass  # Skip on error

    return total_added


def init_90_days(seen):
    """On first run: scrape last ~90 days to bootstrap the store."""
    today = date.today()
    for delta in range(90):
        d = today - timedelta(days=delta)
        d_str = d.strftime("%m/%d/%Y")
        try:
            rows = scraper.fetch_day(d_str, draw_date=d.isoformat())
            new = [r for r in rows if r["game_no"] not in seen]
            for dr in new:
                seen.add(dr["game_no"])
            if new:
                store_mod.append_draws(new, STORE)
                print(f"[{d.isoformat()}] Added {len(new)} draws")
        except:
            pass
        time.sleep(0.3)


def main():
    print("CT Keno Scraper Worker starting...")
    print("Loading existing games from store...")
    seen = load_game_numbers()
    print(f"Found {len(seen)} games already in store.")

    if len(seen) < 100:
        print("Bootstrapping last 90 days...")
        init_90_days(seen)
        print(f"Initial load complete. Store now has {len(seen)} games.")
    else:
        print(f"Store already has {len(seen)} games. Skipping bootstrap.")

    print("\nEntering 2-minute polling loop for today's draws... (Ctrl+C to stop)")
    while True:
        try:
            added = search_today()
            if added:
                seen = load_game_numbers()
                print(f"[{date.today().isoformat()}] Found {added} new draws. Total: {len(seen)}")
            else:
                print(f"[{date.today().isoformat()}] No new draws yet. Next poll in 2 min...")
        except Exception as e:
            print(f"Error during poll: {e}")

        time.sleep(120)


if __name__ == "__main__":
    main()