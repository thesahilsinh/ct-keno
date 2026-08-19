# CT Keno Analysis & Simulation

Scrape real Connecticut Keno draws from ctlottery.org and simulate how any
playing strategy would have performed — historically (exact) and probabilistically
(Monte-Carlo risk distribution).

> **Honest note:** Keno is a negative-expectancy game. *Every* strategy loses money
> on average over a large sample. This tool compares strategies and shows the swing
> (variance, losing streaks, chance a session ends profitable) — it does **not** find
> a winning system. The "theoretical house edge" line makes this explicit.

## Setup
```bash
pip install pytest          # only needed to run tests
```

## Usage
```bash
# 1) Pull real draws (one request per day; ~chunks of 90 days recommended)
python cli.py scrape --start 05/15/2026 --end 08/13/2026

# 2) Simulate a strategy over the stored draws
python cli.py sim --config config.example.json

# 3) Inspect the stored dataset
python cli.py analyze
```

## Configure your strategy (`config.example.json`)
| field | meaning |
|-------|---------|
| `spots` | how many numbers you pick (1–10) |
| `mode` | `"quickpick"` (random each draw) or `"fixed"` (your lucky numbers) |
| `picks` | the numbers, required when `mode="fixed"` |
| `wager` | base wager per draw ($1–$20) |
| `bonus` | `true` = play the Bonus multiplier (doubles cost; won prizes ×draw's multiplier) |
| `draws` | how many recent stored draws to replay |
| `sessions`, `session_draws` | Monte-Carlo size (e.g. 1000 sessions × 20 draws) |

Copy `config.example.json` → `config.json`, edit, and run `python cli.py sim --config config.json`.

## Real-time dashboard (recommended)
A single self-contained server with an inline dashboard — no build step, no
static file. It auto-scrapes and the browser updates live every 2 seconds:
```bash
python server.py                 # http://localhost:8000  (scrape every 60s)
python server.py --interval 30   # scrape every 30s
python server.py --no-scrape     # serve only, no background scraper
```
Open **http://localhost:8000** in your browser. You'll see, updating in real time:
- **Latest draws feed** — newest first; new draws flash in as they land.
- **Number frequency heat-board** (1–80, blue=cold → red=hot).
- **Hot / Cold / Overdue** lists.
- **Draws per day** bar chart (last 14 days).
- **Number Analysis — Decision Support** (see below).
- Live status: total draws, newest game #, last scrape time.

### Number Analysis (decision support)
The dashboard includes a full analysis panel (served at `/api/analysis`):
- **Metric board** — re-color all 80 numbers by **Frequency**, **Overdue**
  (current gap ÷ max gap), **Recent** (last 50 draws), or **Max Gap**. Hover any
  number for its full stats (freq, last-seen, max gap, due score, recent count).
- **Top Combinations** — top 20 pairs, triplets, and quadruplets with their
  occurrence count and max gap.
- **Distribution** — numbers by 10s range, odd/even split, high/low split, and
  draw-sum stats (min/avg/max).
- **Pair Prediction — Top Scored** — ranks every pair by a composite score
  (frequency z-score vs. the 6.01% theoretical expectation + recent momentum +
  "due" factor), and shows the #1 pick with its full math breakdown.
- **Today's Trend — vs 3-Month Baseline** — compares today's draws against the
  last 90 days, showing hot numbers and pairs with trend z-scores, plus a
  "today's pick" (top 4 numbers + top pair).

The analysis is computed over the most recent 500 draws (bounded for speed) and
refreshes with the live feed. The pair prediction uses the full history and
recomputes only when a new draw lands.

### Today-only page
A separate page at **http://localhost:8000/today** shows the same analysis
panels (Number Analysis, Top Combinations, Distribution, Today's Trend) but
computed on **today's draws only**. A nav link on the main dashboard
("today's analysis →") jumps to it.

The background thread scrapes today + yesterday from ctlottery.org every
`--interval` seconds and appends to `data/draws.csv` (dedup-safe). Keno draws
land every ~4 minutes, so with a 30–60s interval you'll see each new draw
appear within a minute of it being posted.

## Static dashboard (optional, no server)
A self-contained static file is also generated from the store — no server, no
CDN, just open the file (but it does **not** auto-update):
```bash
python build_site.py          # regenerates site/index.html from data/draws.csv
# then open site/index.html in a browser
```
It shows: summary cards, frequency heat-board, hot/cold/overdue lists, bonus
distribution, number trend, draws-per-day, and 7-day/weekly/monthly views.

### Auto scraping (daily time-based)
For hands-free updates, run `scraper_cron.py`:
```bash
python scraper_cron.py --days 7      # scrape last 7 days
python scraper_cron.py --today       # scrape only today

# Optional: schedule via hermes cron for daily updates:
hermes cron create --schedule "0 2 * * *" \
  --script "$HOME/ctx/keno/scraper_cron.py" -d 1
```

The daily scraper now stamps `draw_date` on scraped draws, so the "Draws per Day"
chart and "7-day/Weekly/Monthly" views fill in automatically as you scrape.

## Number analysis (frequency, pairs, triplets, quadruplets)
A full number-analysis workflow that reads from the scraped store (no text file
needed). It treats the most recent draw as the "current draw" and produces the
same outputs as a classic keno number-analysis script:
```bash
python cli.py numbers --draws 100 --outdir analysis
```
Writes 21 files to `analysis/`:
- `number_frequency_sorted.txt` — all 80 numbers by frequency.
- `first_line_frequencies_sorted.txt` — current draw's numbers by frequency.
- `filtered_frequencies.txt` — frequencies with 0/1 number in the current draw.
- `frequency_number_counts.txt`, `remaining_numbers.txt`, `range_division.txt`,
  `selected_numbers.txt`, `range_frequency_analysis.txt`.
- `pair_frequencies.txt` + filtered/strict variants (top 20, with max gap + last occurrence).
- `triplet_frequencies.txt` + filtered/two/strict variants.
- `quadruplet_frequencies.txt` + filtered/two/three/strict variants.
- `common_4.txt` — quadruplets containing 2+ of the top-10 pairs.

## Files
- `payouts.py` — parses official paytable.
- `scraper.py` — fetches draws from ctlottery.org AJAX.
- `store.py` — dedup-aware CSV (`data/draws.csv`).
- `simulate.py` — `replay_history()` + `monte_carlo()`.
- `analyze.py` — stats, house edge theory.
- `number_analysis.py` — frequency / pair / triplet / quadruplet analysis (CLI).
- `analysis_web.py` — decision-support metrics for the live dashboard.
- `server.py` — **real-time dashboard + auto-scraper (single file)**.
- `build_site.py` — regenerates the static dashboard.
- `scraper_cron.py` — cron-friendly scraper for daily runs.

## Tests
```bash
python -m pytest -q
```
Parsing/store/sim tests run fully offline against `tests/fixtures/` (one real day of
draws + the paytable page), so they're fast and deterministic.

## Limitations
- The site exposes no per-draw clock time, so time-of-day ("best hour") analysis is impossible.
- Scraping is date-only (one calendar day per request). Be polite: 1 req/sec is built in.
