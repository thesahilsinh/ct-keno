#!/usr/bin/env python3
"""Keno tactic simulator: 2-spot pair chase with a daily-reset wager ladder.

TACTIC (user-specified)
  * Play ONE 2-number pair at a time. Pairs come from the site's "overdue"
    section (exact port of analysis_web.compute_overdue over TODAY's draws
    so far: pairs whose draws-since-last-hit >= 16, ranked by
    (times-hit-today desc, draws-since-last-hit desc, first-seen, pair)).
    At the start of a day (nothing overdue yet in today's draws) the pick
    falls back to the trailing overnight window.
  * Chase the pair until it HITS. The moment it hits -> close the run
    (profitable by construction), pick a NEW top-overdue pair, keep playing.
    A pair that hit today is never re-picked the same day.
  * If a pair goes the whole ladder without hitting -> close the run at the
    ladder's max loss and KEEP CHASING THE SAME PAIR with a fresh $1 ladder
    ("your goal has to hit that number").
  * An un-hit pair survives midnight (carried into the next day); the LADDER
    resets every day at the first draw. Each day's P&L is saved to a
    date-wise ledger.
  * Every day resets the playing info: fresh ladder, fresh picks.

LADDER (only these wagers; strictly-profitable rule prev_spend + j*w < 11*w)
    $1 x 10 draws, $2 x 5, $3 x 4, $4 x 2, $5 x 2, $10 x 5, $20 x 5
  = 33 draws max per run, $200 max spend per run. A hit at any allowed draw
    leaves the run strictly profitable (e.g. 10 misses at $1 = $10 down, hit
    at $2 pays $22 -> +$2).

PAYOUT (user-specified): $1 on the 2-spot pays $11 total (profit $10).
Bonus multipliers in the draw data are ignored.

HONESTY: 2-spot true odds are 20*19/(80*79) = 6.0127% per draw; a fair payout
would be $16.63, the game pays $11 -> RTP 66.1%. The ladder shapes WHEN you
lose, not WHETHER: long-run EV is negative and the ledger will show it.

Determinism: sim.json is a pure function of data/draws.csv (no timestamps
inside), so the GitHub Action recomputes the newest 2 days every ~5 min and
older days stay frozen in the ledger. A config/version change forces a full
rebuild. Late-arriving draws for a date older than 2 days would be ignored
(historically draws arrive within minutes, so this never bit).
"""
import json
from itertools import combinations
from pathlib import Path

import store
import analysis_web

ROOT = Path(__file__).resolve().parent
STORE = ROOT / "data" / "draws.csv"
OUT = ROOT / "data" / "sim.json"

# ---- tactic configuration ----
TIERS = [1, 2, 3, 4, 5, 10, 20]   # wager escalation (dollars)
PAYOUT = 11                        # dollars won per $1 wagered when both hit
OVERDUE_WINDOW = 314               # trailing draws for overnight fallback picks
OVERDUE_THRESHOLD = 16            # draws-since-last-hit to count as overdue
                                  # (expected pair gap 16.63 -> floor 16, site parity)
SIM_VERSION = 1
RECOMPUTE_DAYS = 2                 # newest days recomputed on every build


# ---------------------------------------------------------------- schedule
def wager_schedule(tiers=None, payout=None):
    """The ladder: [{wager, draws, cum_spend}] per tier.

    Draws allowed at tier w = max j with prev_spend + j*w < payout*w, i.e.
    a hit at ANY allowed draw leaves the run strictly profitable.
    """
    tiers = tiers or TIERS
    payout = payout or PAYOUT
    sched = []
    prev_spend = 0
    for w in tiers:
        jmax = (payout * w - prev_spend - 1) // w   # strict <
        if jmax < 1:
            continue
        prev_spend += jmax * w
        sched.append({"wager": w, "draws": int(jmax), "cum_spend": prev_spend})
    return sched


# ------------------------------------------------------------- pair ranking
class PairTracker:
    """Incremental pair statistics over a sliding set of draws.

    Mirrors the site's overdue algorithm (analysis_web.compute_overdue):
    only pairs that APPEARED in the window are ranked; a pair is overdue when
    draws-since-last-appearance >= threshold. Ranking key:
        (-count, -since, first_seen, pair)
    first_seen/pair make the order fully deterministic (the site's stable
    sort reproduces this for every case that matters in real data).
    """

    def __init__(self):
        self.n = 0
        self.counts = {}
        self.first_seen = {}
        self.last_seen = {}

    def add_draw(self, nums):
        self.n += 1
        i = self.n
        for c in combinations(sorted(nums), 2):
            if c in self.counts:
                self.counts[c] += 1
            else:
                self.counts[c] = 1
                self.first_seen[c] = i
            self.last_seen[c] = i

    def rank(self, threshold=None, exclude=None):
        """Best overdue pair [a, b], or None when nothing qualifies."""
        threshold = OVERDUE_THRESHOLD if threshold is None else threshold
        n = self.n
        best = None
        for c, cnt in self.counts.items():
            if n - self.last_seen[c] < threshold:
                continue
            if exclude and c in exclude:
                continue
            key = (-cnt, -(n - self.last_seen[c]), self.first_seen[c], c)
            if best is None or key < best:
                best = key
        return list(best[3]) if best else None


# ---------------------------------------------------------------- replay
def replay_day(day_draws, window_before, sched, carry_pair=None,
               payout=None):
    """Simulate one day (draws ASCENDING by game_no).

    window_before: trailing draws (number-lists) ending just before this day
    -- the overnight fallback for the first picks of the day.
    carry_pair: the un-hit pair chasing across midnight (or None).

    Returns dict with runs / open_run / events / carry_out / totals.
    """
    payout = payout or PAYOUT
    day = PairTracker()          # today's draws so far
    overnight = PairTracker()    # frozen trailing window (fallback picks)
    for nums in window_before:
        overnight.add_draw(nums)

    runs = []                    # closed runs
    events = []                  # per-draw log (today only)
    staked = won = 0
    run = None
    pending = carry_pair         # pair to open the next run with (forced)
    hit_today = set()            # pairs that hit today -> never re-picked today

    def pick_new():
        p = day.rank(exclude=hit_today)
        if p is None:
            p = overnight.rank(exclude=hit_today)
        return p

    for d in day_draws:
        g = d["game_no"]
        nums = sorted(set(d["numbers"]))
        day.add_draw(nums)

        # open a run if none is active
        if run is None:
            pair = pending or pick_new()
            pending = None
            if pair is None:
                events.append({"game": g, "pair": None, "wager": 0,
                               "result": "skip"})
                continue
            run = {"pair": pair, "tier": 0, "draws_at_tier": 0, "spend": 0,
                   "draws": 0, "hits": 0, "first_game": g, "last_game": None}

        pair = run["pair"]
        w = sched[run["tier"]]["wager"]
        hit = pair[0] in nums and pair[1] in nums

        run["spend"] += w
        run["draws"] += 1
        run["draws_at_tier"] += 1
        run["last_game"] = g
        staked += w

        if hit:
            prize = payout * w
            won += prize
            run["hits"] += 1
            run["result"] = "hit"
            run["pnl"] = prize - run["spend"]
            run["end_tier"] = run["tier"]
            runs.append(run)
            events.append({"game": g, "pair": pair, "wager": w, "hit": True,
                           "prize": prize, "run_spend": run["spend"],
                           "run_pnl": run["pnl"], "closed_run": True})
            hit_today.add(tuple(pair))
            run = None                    # next draw picks a NEW overdue pair
        else:
            events.append({"game": g, "pair": pair, "wager": w, "hit": False,
                           "prize": 0, "run_spend": run["spend"],
                           "run_pnl": -run["spend"]})
            tier = sched[run["tier"]]
            if run["draws_at_tier"] >= tier["draws"]:
                if run["tier"] + 1 >= len(sched):
                    # ladder exhausted -> close run, KEEP CHASING the same pair
                    run["result"] = "exhausted"
                    run["pnl"] = -run["spend"]
                    run["end_tier"] = run["tier"]
                    runs.append(run)
                    events.append({"game": g, "pair": pair, "wager": 0,
                                   "result": "exhausted",
                                   "run_spend": run["spend"],
                                   "run_pnl": -run["spend"], "closed_run": True})
                    pending = pair
                    run = None
                else:
                    run["tier"] += 1
                    run["draws_at_tier"] = 0

    # carry-out: pair still being chased at day end (None after a final hit)
    if run is not None:
        carry_out = run["pair"]
        open_run = dict(run)
        open_run["result"] = "open"
        open_run["pnl"] = -run["spend"]            # unrealized
        open_run["end_tier"] = run["tier"]
        open_run["wager"] = sched[run["tier"]]["wager"]
    else:
        carry_out = pending
        open_run = None

    closed_pnl = sum(r["pnl"] for r in runs)
    return {
        "runs": runs,
        "open_run": open_run,
        "events": events,
        "carry_out": carry_out,
        "staked": staked,
        "won": won,
        "pnl": closed_pnl + (open_run["pnl"] if open_run else 0),
        "draws_played": sum(1 for e in events if e.get("wager")),
        "hits": sum(r["hits"] for r in runs),
    }


# ------------------------------------------------------------------ build
def build_sim(store_path=STORE, out_path=OUT, full=False, today=None,
              quiet=False):
    """Compute the ledger. Days older than the newest RECOMPUTE_DAYS are
    frozen from a previous sim.json; today + yesterday always recompute.
    full=True rebuilds everything from scratch."""
    draws = store.load_draws(store_path)
    if not draws:
        raise SystemExit("no draws in store")

    # group by normalized ISO date, ascending; undated rows dropped (3 legacy
    # rows of ~43k — same filter the site's today view applies)
    by_date = {}
    for d in draws:
        nd = analysis_web._norm_date(d.get("draw_date"))
        if not nd:
            continue
        by_date.setdefault(nd, []).append(d)
    dates = sorted(by_date.keys())
    for nd in dates:
        by_date[nd].sort(key=lambda x: x["game_no"])   # ascending within day

    if today is None:
        today = dates[-1]
    recompute = set(dates[-RECOMPUTE_DAYS:]) if not full else set(dates)

    # previous ledger -> frozen past days
    prev_days = {}
    if not full and out_path.exists():
        try:
            old = json.loads(out_path.read_text(encoding="utf-8"))
            old_cfg = old.get("config", {})
            same_cfg = (
                old.get("sim_version") == SIM_VERSION
                and old_cfg.get("tiers") == TIERS
                and old_cfg.get("payout") == PAYOUT
                and old_cfg.get("overdue_window") == OVERDUE_WINDOW
                and old_cfg.get("overdue_threshold") == OVERDUE_THRESHOLD
            )
            if same_cfg:
                for row in old.get("ledger", []):
                    if row["date"] < today and row["date"] not in recompute:
                        row.pop("events", None)
                        row.pop("open_run", None)
                        prev_days[row["date"]] = row
            elif not quiet:
                print("  [sim] config/version changed -> full rebuild")
        except Exception as e:
            if not quiet:
                print(f"  [sim] previous sim.json unreadable ({e}); full rebuild")

    sched = wager_schedule()
    all_ascending = sorted(draws, key=lambda x: x["game_no"])

    ledger = []
    carry = None
    n_replayed = 0
    for nd in dates:
        if nd in prev_days:
            carry = prev_days[nd].get("carry")   # continue the chase chain
            ledger.append(prev_days[nd])
            continue
        day_draws = by_date[nd]
        first_g = day_draws[0]["game_no"]
        window_before = [d["numbers"] for d in all_ascending
                         if d["game_no"] < first_g][-OVERDUE_WINDOW:]
        res = replay_day(day_draws, window_before, sched, carry_pair=carry)
        carry = res["carry_out"]
        n_replayed += 1

        max_wager = 0
        pairs_played = []
        for r in res["runs"]:
            max_wager = max(max_wager, sched[r["end_tier"]]["wager"])
            pairs_played.append({
                "pair": r["pair"], "result": r["result"], "draws": r["draws"],
                "spend": r["spend"], "pnl": r["pnl"],
                "first_game": r["first_game"], "last_game": r["last_game"],
            })
        op = res["open_run"]
        if op:
            max_wager = max(max_wager, sched[op["end_tier"]]["wager"])
            pairs_played.append({
                "pair": op["pair"], "result": "open", "draws": op["draws"],
                "spend": op["spend"], "pnl": op["pnl"],
                "first_game": op["first_game"], "last_game": op["last_game"],
            })
        row = {
            "date": nd,
            "draws_played": res["draws_played"],
            "runs": len(res["runs"]),
            "hits": res["hits"],
            "staked": res["staked"],
            "won": res["won"],
            "pnl": res["pnl"],
            "max_wager": max_wager,
            "pairs": pairs_played,
            "carry": res["carry_out"],
        }
        if nd == today:
            row["events"] = res["events"]
            row["open_run"] = op
        ledger.append(row)
        if not quiet and n_replayed % 20 == 0:
            print(f"  [sim] replayed {n_replayed} days... ({nd})")

    # ---- totals over the full ledger ----
    tot = {
        "days": len(ledger),
        "draws_played": sum(r["draws_played"] for r in ledger),
        "runs": sum(r["runs"] for r in ledger),
        "hits": sum(r["hits"] for r in ledger),
        "staked": sum(r["staked"] for r in ledger),
        "won": sum(r["won"] for r in ledger),
        "pnl": sum(r["pnl"] for r in ledger),
        "runs_exhausted": sum(1 for r in ledger
                              for p in r.get("pairs", [])
                              if p["result"] == "exhausted"),
    }
    tot["roi_pct"] = round(tot["pnl"] / tot["staked"] * 100, 2) if tot["staked"] else 0.0
    if tot["runs"]:
        tot["hit_rate_pct"] = round(tot["hits"] / tot["runs"] * 100, 2)

    sim = {
        "sim_version": SIM_VERSION,
        "config": {
            "tiers": TIERS,
            "payout": PAYOUT,
            "overdue_window": OVERDUE_WINDOW,
            "overdue_threshold": OVERDUE_THRESHOLD,
            "p_hit_pct": round(20 * 19 / (80 * 79) * 100, 4),   # 6.0127
            "ev_per_dollar_pct": round((20 * 19 / (80 * 79) * PAYOUT - 1) * 100, 1),
        },
        "schedule": sched,
        "today": today,
        "totals": tot,
        "ledger": ledger,
    }

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(sim, separators=(",", ":")),
                        encoding="utf-8")
    if not quiet:
        cur = next((r for r in ledger if r["date"] == today), None)
        opn = (cur or {}).get("open_run") or {}
        print(f"  [sim] wrote {out_path.name}: {tot['days']} days, "
              f"pnl {tot['pnl']:+.0f}, today {today} "
              f"pnl {(cur or {}).get('pnl', 0):+.0f}, "
              f"chasing {opn.get('pair') or (cur or {}).get('carry')}")
    return sim


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--full", action="store_true",
                    help="recompute every day from scratch (slow first backfill)")
    ap.add_argument("--store", type=Path, default=STORE)
    ap.add_argument("--out", type=Path, default=OUT)
    a = ap.parse_args()
    build_sim(a.store, a.out, full=a.full)