#!/usr/bin/env python3
"""CT Keno analysis & simulation CLI.

Commands:
  scrape   pull real draws from ctlottery.org into data/draws.csv
  sim      replay a strategy (from config) over real draws + Monte-Carlo
  analyze  describe the stored dataset (size, date range, bonus frequency)

Examples:
  python cli.py scrape --start 05/15/2026 --end 08/13/2026
  python cli.py sim --config config.example.json
  python cli.py analyze
"""
import argparse
import json
import sys
from datetime import date, datetime
from pathlib import Path

import payouts
import scraper
import store
import simulate
import analyze
import number_analysis

ROOT = Path(__file__).resolve().parent
STORE = ROOT / "data" / "draws.csv"
PAYTABLE_FIXTURE = ROOT / "tests" / "fixtures" / "keno_htp.html"


def _load_payouts():
    """Load the official paytable (from fixture if present, else fetch live)."""
    if PAYTABLE_FIXTURE.exists():
        html = PAYTABLE_FIXTURE.read_text(encoding="utf-8", errors="ignore")
    else:
        import urllib.request
        req = urllib.request.Request(
            payouts.PAYTABLE_URL,
            headers={"User-Agent": scraper.UA})
        html = urllib.request.urlopen(req, timeout=30).read().decode("utf-8", "ignore")
    return payouts.load_payouts(html)


def _parse_date(s: str) -> date:
    return datetime.strptime(s, "%m/%d/%Y").date()


def cmd_scrape(args):
    if args.today:
        from datetime import date
        today = date.today()
        start = end = today.strftime("%m/%d/%Y")
    else:
        start = args.start
        end = args.end
    print(f"Scraping CT Keno {start} .. {end}")
    added = scraper.scrape_range(start, end, STORE, delay=args.delay)
    store_len = len(store.load_draws(STORE))
    print(f"Done. Added {added} new draws. Store total: {store_len}")
    try:
        import build_site
        build_site.build()
        print("Visualization refreshed -> site/index.html")
    except Exception as e:
        print(f"(skipped site rebuild: {e})")


def cmd_sim(args):
    cfg = json.loads(Path(args.config).read_text())
    _load_payouts()
    draws = store.load_draws(STORE)
    if not draws:
        print("No draws in store. Run `python cli.py scrape` first.", file=sys.stderr)
        return 1
    # If the user plays a fixed number of draws/day, sample that many per day;
    # simplest faithful model: take the most recent N draws from the store.
    n_draws = cfg.get("draws", len(draws))
    window = draws[-n_draws:] if cfg.get("recent_only", True) else draws
    strat = simulate.Strategy(
        spots=cfg["spots"],
        mode=cfg.get("mode", "quickpick"),
        picks=cfg.get("picks"),
        wager=cfg.get("wager", 1),
        bonus=cfg.get("bonus", False),
    )
    print(f"Strategy: {strat.spots}-spot {strat.mode}"
          f"{(' picks=' + str(strat.picks)) if strat.picks else ''}"
          f", wager ${strat.wager}, bonus={'ON' if strat.bonus else 'OFF'}")
    print(f"Replaying over {len(window)} real draws ...\n")

    res = simulate.replay_history(window, strat, seed=cfg.get("seed", 0))
    s = analyze.summarize(res)
    print("=== HISTORICAL REPLAY (exact, over real draws) ===")
    for k, v in s.items():
        print(f"  {k:24}: {v}")
    he = analyze.theoretical_house_edge(strat.spots, strat.wager, strat.bonus, draws)
    print(f"\n  theoretical EV/draw  : {round(he, 4)}  (house edge "
          f"{round(-100*he/(strat.wager*(2 if strat.bonus else 1)), 2)}%)")

    sessions = cfg.get("sessions", 500)
    sd = cfg.get("session_draws", 20)
    print(f"\n=== MONTE-CARLO ({sessions} sessions x {sd} draws each) ===")
    nets = simulate.monte_carlo(window, strat, sd, sessions, seed=cfg.get("seed", 7))
    ds = analyze.distribution_stats(nets)
    for k, v in ds.items():
        print(f"  {k:18}: {v}")
    return 0


def cmd_analyze(args):
    _load_payouts()
    draws = store.load_draws(STORE)
    if not draws:
        print("No draws in store yet. Run `scrape` first.")
        return
    gnos = [d["game_no"] for d in draws]
    print(f"Draws stored     : {len(draws)}")
    print(f"Game # range     : {min(gnos)} .. {max(gnos)}")
    freq = analyze.bonus_frequency(draws)
    print("Bonus multiplier freq:")
    for m, f in freq.items():
        print(f"  x{m}: {round(100*f, 2)}%")


def cmd_numbers(args):
    """Run the number-analysis workflow (freq, pairs/triplets/quads, ranges)."""
    number_analysis.run(STORE, args.outdir, max_draws=args.draws)
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(description="CT Keno analysis & simulation")
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("scrape", help="pull real draws")
    sp.add_argument("--start", help="MM/DD/YYYY")
    sp.add_argument("--end", help="MM/DD/YYYY")
    sp.add_argument("--delay", type=float, default=1.0, help="seconds between requests")
    sp.add_argument("--today", action="store_true", help="scrape only today (requires start+end not set)")
    sp.set_defaults(func=cmd_scrape)

    sm = sub.add_parser("sim", help="replay strategy + Monte-Carlo")
    sm.add_argument("--config", default=str(ROOT / "config.example.json"))
    sm.set_defaults(func=cmd_sim)

    sa = sub.add_parser("analyze", help="describe stored dataset")
    sa.set_defaults(func=cmd_analyze)

    sn = sub.add_parser("numbers", help="number analysis: freq, pairs/triplets/quads, ranges")
    sn.add_argument("--draws", type=int, default=100, help="most recent draws to analyze (default 100)")
    sn.add_argument("--outdir", default=str(ROOT / "analysis"), help="output directory (default analysis/)")
    sn.set_defaults(func=cmd_numbers)

    args = p.parse_args(argv)
    return args.func(args) or 0


if __name__ == "__main__":
    sys.exit(main())
