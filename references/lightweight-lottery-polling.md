# Lightweight Scraping for Daily Lottery Draws

When you need a **simple, always-on scraper** for daily lottery draws (CT Keno, Powerball, etc.) that:
- Starts fresh and bootstraps historical data
- Polls every few minutes during drawing windows
- Works as a standalone Python script

Use `scraper_worker.py` — a minimal alternative to the full CLI/cron setup.

## Key Pattern: 2-Minute Polling

Lottery draws appear at regular intervals (CT Keno: ~every 4-5 minutes after the last draw of the day). Polling every 2 minutes catches them reliably.

```python
def search_today():
    today = date.today()
    today_str = today.strftime("%m/%d/%Y")
    seen = load_game_numbers()

    # Check both yesterday and today to catch late draws
    prev_day = today - timedelta(days=1)
    prev_str = prev_day.strftime("%m/%d/%Y")

    for day in [prev_str, today_str]:
        rows = scraper.fetch_day(day, draw_date=day)
        new = [r for r in rows if r["game_no"] not in seen]
        # append and track...
```

## Pattern: Bootstrap + Poll

```python
def main():
    seen = load_game_numbers()

    # Bootstrap: if store is empty, scrape last N days
    if len(seen) < 100:
        init_90_days(seen)

    # Always poll for today's draws
    while True:
        added = search_today()
        time.sleep(120)  # 2-minute poll interval
```

## Scheduling Options

**Option A: Hermes cron (recommended)**
```bash
hermes cron create --schedule "0 2 * * *" \
  --script "$HOME/ctx/keno/scraper_worker.py"
```

**Option B: Windows Task Scheduler / cron**
```bash
0 */2 * * * /usr/bin/python3 /path/to/scraper_worker.py
```

**Option C: Run directly in a tmux/screen session**
```bash
python scraper_worker.py  # Ctrl+B, D to detach
```

## Challenge: "No Draws Today" is Normal

Unlike typical webs scraping where empty means "not done", lottery sites often return:
- HTTP 200 with empty or old data
- Yesterday's draws mixed in
- No data at all for non-drawing days

**Solution:** Always check `game_no` against your store. The fetch_day() function returns ALL draws for that day from the server — dedupe handles the rest.

## Related

- [`scrape-public-data-no-api`](../scrape-public-data-no-api) — full probe ladder for discovering endpoints
- [`references/ct-lottery-keno-analysis-sim.md`](ct-lottery-keno-analysis-sim.md) — downstream pipeline (simulation, visualization)
- [`scraper_cron.py`](../../scraper_cron.py) — CLI-based alternative with hermes cron integration