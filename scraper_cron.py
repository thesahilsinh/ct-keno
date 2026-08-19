#!/usr/bin/env python3
"""Keno cron job: scrape today's draws and rebuild EVERYTHING.

Run this at 02:00 AM daily via hermes cron:
  hermes cron create --schedule "0 2 * * *" \
    --script "$HOME/ctx/kenomap/scraper_cron.py" -d 2

Or run weekly for a 7-day rolling window:
  hermes cron create --schedule "0 3 * * 1" \
    --script "$HOME/ctx/kenomap/scraper_cron.py" -d 7
"""
import sys, argparse
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).parent.resolve()

def main(days_back: int = 7):
    """Scrape draws from N days ago up to today and rebuild site."""
    import subprocess
    
    today = date.today()
    start = today - timedelta(days=days_back)
    end = today

    # npx cannot be used; use subprocess with python module
    print(f"[{date.today().isoformat()}] Scraping draws from {start} to {end}...")
    
    # Run scraper with --today for daily cron
    result = subprocess.run(
        ["python3", "cli.py", "scrape", "--today"],
        cwd=ROOT, capture_output=True, text=True
    )
    print(result.stdout)
    if result.returncode != 0:
        print("SCRAPE ERROR:", result.stderr, file=sys.stderr)
        return 1

    # Rebuild the site
    print("Rebuilding site...")
    result2 = subprocess.run(
        ["python3", "build_site.py"], cwd=ROOT, capture_output=True, text=True
    )
    print(result2.stdout)
    if result2.returncode != 0:
        print("BUILD ERROR:", result2.stderr, file=sys.stderr)
        return 1

    print("Done. Next run will detect new draws.")
    return 0

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", "-d", type=int, default=7, help="Days to look back")
    args = parser.parse_args()
    sys.exit(main(days_back=args.days))