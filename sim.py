#!/usr/bin/env python3
"""Keno tactic simulator: 2-spot pair chase with doubling continuation.

Two outputs:
  * data/sim.json — full-history replay: every day since the store begins,
    frozen date-wise P&L ledger (served by /sim).
  * data/live.json — the LIVE session: starts from a fixed anchor draw
    ("now" at creation) and keeps running forward, draw by draw, forever
    (served by /live). Same engine, same rules; P&L counts only the session.

TACTIC (user-specified, v2)
  * Play ONE 2-number pair at a time. Pairs come from the site's "overdue"
    section (exact port of analysis_web.compute_overdue over TODAY's draws
    so far: pairs whose draws-since-last-hit >= 16, ranked by
    (times-hit-today desc, draws-since-last-hit desc, first-seen, pair)),
    with the trailing overnight window as fallback at day start.
  * BASE LADDER (daily reset): $1x10, $2x5, $3x4, $4x2, $5x2, $10x5, $20x5
    = 33 draws, $200. Strictly-profitable rule: spend + j*w < 11*w, so a
    hit on any allowed draw banks +$1 .. +$100.
  * DOUBLING CONTINUATION (v2): when the base ladder exhausts without a
    hit, the SAME run keeps playing the SAME pair on doubling tiers:
    $40x5, $80x5, $160x5, $320x5, $640x5, ... (wager doubles each tier,
    5 draws per tier, no limit) until the pair hits. At every doubling tier
    start, spend-so-far is exactly 5*w, so a hit banks at least +$w
    ($40 tier: $440 back on $400 spent = +$40 minimum) — "keep playing that
    number while increasing the amount and profit in mind ... until you
    hit and make profit on that pair". Doubling state carries across
    midnight; the base ladder still resets daily at $1.
  * PARALLEL PAIR (v2): at the moment a run exhausts its base ladder, a
    SECOND run opens on the current top overdue pair (if different from
    every pair already being chased) with its own fresh $1 base ladder. Both
    wager on every draw in parallel. If that run also exhausts, it starts
    doubling and spawns the next top pair, and so on — so at any moment
    there is always at least one run chasing, plus one per exhaustion.
  * On a HIT: bank the profit. A base-ladder hit spawns the next top
    overdue pair; a doubling hit just closes (goal achieved: pair hit).
  * DAILY RESET: base ladders restart at $1 at each day's first draw;
    un-hit pairs carry across midnight (doubling runs carry their state).

PAYOUT (user-specified): $1 on the 2-spot pays $11 total (profit $10).
Bonus multipliers in the draw data are ignored.

HONESTY: a specific pair hits with p = 6.0127% per draw, independent of how
"overdue" it is. $11 payout vs fair $16.63 -> house edge 33.9% of turnover.
The doubling ladder makes every RUN end in profit — the risk moves into the
BANKROLL: a run that reaches the $640 tier has $4,600 sunk before it hits.
Observed history (139 days) shows pair gaps up to 918 draws, which would
demand wagers of ~$2.6e26 — the stats therefore track peak exposure and max
drawdown, which is where this tactic actually loses.

Determinism: both JSONs are pure functions of data/draws.csv (+ the live
anchor persisted inside live.json). SIM_VERSION bumps force a full rebuild.
"""
import json
from itertools import combinations
from pathlib import Path

import store
import analysis_web

ROOT = Path(__file__).resolve().parent
STORE = ROOT / "data" / "draws.csv"
OUT = ROOT / "data" / "sim.json"
LIVE_OUT = ROOT / "data" / "live.json"
LIVE_EVENT_CAP = 3000        # recent events shipped to the /live page

# ---- tactic configuration ----
TIERS = [1, 2, 3, 4, 5, 10, 20]   # base-ladder wagers (dollars)
TIER_DRAWS = [10, 5, 4, 2, 2, 5, 5]
PAYOUT = 11                        # dollars won per $1 wagered when both hit
DOUBLE_START = 40                  # first doubling wager (2 x $20 tickets)
DOUBLE_DISPLAY = 7                 # doubling tiers shown in the UI ladder
OVERDUE_WINDOW = 314               # trailing draws for overnight fallback picks
OVERDUE_THRESHOLD = 16             # draws-since-last-hit to count as overdue
                                  # (expected pair gap 16.63 -> floor 16, site parity)
SIM_VERSION = 2
RECOMPUTE_DAYS = 2                 # newest days recomputed on every build
ABANDON_AFTER = 1000               # safety valve: close a run after this many
                                  # wagered draws (p ~ 1e-27; data-corruption
                                  # guard only, never triggers in practice)


# ---------------------------------------------------------------- schedule
def base_tiers():
    """Base ladder: [{wager, draws, cum_spend}] under the strict-profit rule
    prev_spend + j*w < payout*w (user's tier/draw counts)."""
    sched = []
    prev = 0
    for w, j in zip(TIERS, TIER_DRAWS):
        jmax = min((PAYOUT * w - prev - 1) // w, j)
        if jmax < 1:
            continue
        prev += jmax * w
        sched.append({"wager": w, "draws": int(jmax), "cum_spend": prev})
    return sched


def n_base():
    return len(base_tiers())


def tier_wager(i):
    """Wager at 0-based tier index i (base tiers, then doubling forever)."""
    bt = base_tiers()
    if i < len(bt):
        return bt[i]["wager"]
    return DOUBLE_START * (2 ** (i - len(bt)))    # 40, 80, 160, 320, ...


def tier_draws(i):
    """Draws allowed at tier i."""
    bt = base_tiers()
    if i < len(bt):
        return bt[i]["draws"]
    return 5


def wager_schedule(display_doubling=DOUBLE_DISPLAY):
    """Display schedule: base tiers + the first `display_doubling` doubling
    tiers (the engine keeps doubling without limit)."""
    sched = base_tiers()
    prev = sched[-1]["cum_spend"]
    for k in range(1, display_doubling + 1):
        w = DOUBLE_START * 2 ** (k - 1)
        prev += 5 * w
        sched.append({"wager": w, "draws": 5, "cum_spend": prev,
                      "doubling": True})
    return sched


# ------------------------------------------------------------- pair ranking
class PairTracker:
    """Incremental pair statistics over a sliding set of draws.

    Mirrors the site's overdue algorithm (analysis_web.compute_overdue):
    only pairs that APPEARED in the window are ranked; a pair is overdue when
    draws-since-last-appearance >= threshold. Ranking key:
        (-count, -since, first_seen, pair)
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
def _new_run(rid, pair, g):
    return {"rid": rid, "pair": pair, "tier": 0, "draws_at_tier": 0,
            "spend": 0, "cum_spend": 0, "draws": 0, "draws_total": 0,
            "first_game": g, "last_game": None}


def replay_day(day_draws, window_before, carry_runs=None,
               payout=None, seed_day=None, next_rid=0):
    """Simulate one day (draws ASCENDING by game_no) under the v2 tactic.

    carry_runs: open runs carried across midnight, dicts with
    pair / tier / spend / cum_spend / draws_total. Base-ladder runs reset
    to tier 0 (daily reset, spend re-zeroed); doubling runs (tier >= base
    tier count) keep their state intact.

    seed_day: same-day draws that already happened BEFORE this replay window
    (live session start) — tracked for ranking, never wagered.

    Returns dict with runs / open_runs / events / carry_out / totals.
    """
    payout = payout or PAYOUT
    nb = n_base()
    day = PairTracker()
    for nums in (seed_day or []):
        day.add_draw(nums)
    overnight = PairTracker()
    for nums in window_before:
        overnight.add_draw(nums)

    runs = []                # runs closed during this day
    events = []              # per-draw log
    staked = won = 0
    hit_today = set()
    rid = next_rid
    peak_exposure = 0        # max money simultaneously sunk in open runs

    # restore carried runs: base ladder resets daily (tier/spend re-zeroed),
    # but cum_spend (lifetime money sunk in this pair) carries for BOTH base
    # and doubling runs — yesterday's losses don't vanish at midnight.
    active = []
    for c in (carry_runs or []):
        r = _new_run(rid, c["pair"], None)
        rid += 1
        r["draws_total"] = c.get("draws_total", 0)
        r["cum_spend"] = c.get("cum_spend", 0)
        if c["tier"] < nb:                       # daily reset for base runs
            r["tier"] = 0
        else:                                    # doubling run: keep state
            r["tier"] = c["tier"]
            r["draws_at_tier"] = c.get("draws_at_tier", 0)
        active.append(r)

    to_open = []             # pairs scheduled to open at the NEXT draw

    def pick_new():
        exclude = set(hit_today)
        exclude |= {tuple(r["pair"]) for r in active}
        exclude |= {tuple(p) for p in to_open}
        p = day.rank(exclude=exclude)
        if p is None:
            p = overnight.rank(exclude=exclude)
        return p

    for d in day_draws:
        g = d["game_no"]
        nums = sorted(set(d["numbers"]))
        day.add_draw(nums)

        # open scheduled runs first
        for desc in to_open:
            r = _new_run(rid, desc["pair"], g)
            rid += 1
            active.append(r)
        to_open = []

        # fresh start when nothing is active (day start / after all-hit)
        if not active:
            pair = pick_new()
            if pair is None:
                events.append({"game": g, "rid": None, "pair": None,
                               "wager": 0, "result": "skip"})
                continue
            r = _new_run(rid, pair, g)
            rid += 1
            active.append(r)

        # every active run wagers on this draw
        still_active = []
        for run in active:
            pair = run["pair"]
            w = tier_wager(run["tier"])
            hit = pair[0] in nums and pair[1] in nums

            run["spend"] += w
            run["cum_spend"] += w
            run["draws"] += 1
            run["draws_total"] += 1
            run["last_game"] = g
            staked += w

            if hit:
                prize = payout * w
                won += prize
                closed = dict(run)
                closed["result"] = "hit"
                closed["end_tier"] = run["tier"]
                closed["pnl"] = prize - run["spend"]        # today's pnl
                closed["run_pnl_total"] = prize - run["cum_spend"]
                runs.append(closed)
                events.append({"game": g, "rid": run["rid"], "pair": pair,
                               "wager": w, "hit": True, "prize": prize,
                               "run_spend": run["spend"],
                               "cum_spend": run["cum_spend"],
                               "run_pnl": closed["pnl"], "closed_run": True,
                               "doubling": run["tier"] >= nb})
                hit_today.add(tuple(pair))
                if run["tier"] < nb:
                    # base hit -> next draw chases the current top overdue
                    np = pick_new()
                    if np:
                        to_open.append({"pair": np})
                # a doubling hit closes the run: goal achieved, nothing spawns
            else:
                events.append({"game": g, "rid": run["rid"], "pair": pair,
                               "wager": w, "hit": False, "prize": 0,
                               "run_spend": run["spend"],
                               "cum_spend": run["cum_spend"],
                               "run_pnl": -run["spend"],
                               "doubling": run["tier"] >= nb})
                if run["draws_at_tier"] + 1 >= tier_draws(run["tier"]):
                    run["draws_at_tier"] = 0
                    if run["tier"] + 1 == nb:
                        # base ladder exhausted -> doubling + spawn parallel
                        events.append({"game": g, "rid": run["rid"],
                                       "pair": pair, "wager": 0,
                                       "result": "exhausted",
                                       "run_spend": run["spend"],
                                       "cum_spend": run["cum_spend"],
                                       "run_pnl": -run["spend"],
                                       "closed_run": False})
                        run["tier"] = run["tier"] + 1      # $40 doubling
                        np = pick_new()                     # parallel top pair
                        if np and np != pair:
                            to_open.append({"pair": np})
                    else:
                        run["tier"] += 1
                else:
                    run["draws_at_tier"] += 1

                if run["draws_total"] >= ABANDON_AFTER:
                    closed = dict(run)
                    closed["result"] = "abandoned"          # safety valve
                    closed["pnl"] = -run["spend"]
                    closed["run_pnl_total"] = -run["cum_spend"]
                    closed["end_tier"] = run["tier"]
                    runs.append(closed)
                    events.append({"game": g, "rid": run["rid"],
                                   "pair": pair, "wager": 0,
                                   "result": "abandoned",
                                   "run_spend": run["spend"],
                                   "cum_spend": run["cum_spend"],
                                   "run_pnl": -run["spend"],
                                   "closed_run": True})
                    continue                                # drop run

                still_active.append(run)

        active = still_active
        # per-draw exposure: money currently sunk across all open runs
        exposure = sum(r["cum_spend"] for r in active)
        peak_exposure = max(peak_exposure, exposure)

    closed_pnl = sum(r["pnl"] for r in runs)
    open_runs = active
    wager_events = [e for e in events if e.get("wager")]
    return {
        "runs": runs,
        "open_runs": open_runs,
        "events": events,
        "carry_out": [{"pair": r["pair"], "tier": r["tier"],
                       "draws_at_tier": r["draws_at_tier"],
                       "spend": r["spend"], "cum_spend": r["cum_spend"],
                       "draws_total": r["draws_total"]} for r in open_runs],
        "staked": staked,
        "won": won,
        "pnl": closed_pnl + sum(-r["spend"] for r in open_runs),
        "draws_played": len({e["game"] for e in wager_events}),
        "wagers": len(wager_events),
        "hits": sum(1 for r in runs if r["result"] == "hit"),
        "exhausted": sum(1 for e in events if e.get("result") == "exhausted"),
        "peak_exposure": max(peak_exposure,
                             sum(r["cum_spend"] for r in open_runs)),
        "next_rid": rid,
    }


# ------------------------------------------------------------------ helpers
def _config():
    return {
        "tiers": TIERS,
        "tier_draws": TIER_DRAWS,
        "payout": PAYOUT,
        "double_start": DOUBLE_START,
        "overdue_window": OVERDUE_WINDOW,
        "overdue_threshold": OVERDUE_THRESHOLD,
        "p_hit_pct": round(20 * 19 / (80 * 79) * 100, 4),   # 6.0127
        "ev_per_dollar_pct": round((20 * 19 / (80 * 79) * PAYOUT - 1) * 100, 1),
    }


def _ladder_display(sched):
    out = []
    prev = 0
    for s in sched:
        w, j = s["wager"], s["draws"]
        out.append({
            "wager": w, "draws": j, "cum_spend": s["cum_spend"],
            "doubling": bool(s.get("doubling")),
            "profit_min": PAYOUT * w - s["cum_spend"],   # hit on last allowed draw
            "profit_max": PAYOUT * w - (prev + w),       # hit on first tier draw
        })
        prev = s["cum_spend"]
    return out


def _day_pairs(res):
    """(max_wager, closed+open pair summaries) for one replayed day."""
    max_wager = 0
    pairs_played = []
    for r in res["runs"]:
        max_wager = max(max_wager, tier_wager(r["end_tier"]))
        pairs_played.append({
            "pair": r["pair"], "result": r["result"], "draws": r["draws_total"],
            "spend": r["cum_spend"], "pnl": r["pnl"],
            "run_pnl_total": r.get("run_pnl_total"),
            "first_game": r["first_game"], "last_game": r["last_game"],
        })
    for op in res["open_runs"]:
        max_wager = max(max_wager, tier_wager(op["tier"]))
        pairs_played.append({
            "pair": op["pair"], "result": "open", "draws": op["draws_total"],
            "spend": op["cum_spend"], "pnl": -op["spend"],
            "run_pnl_total": -op["cum_spend"],
            "first_game": op["first_game"], "last_game": op["last_game"],
        })
    return max_wager, pairs_played


def _track_exposure(carry, res, state):
    """Update running exposure / realized-cum / drawdown stats in `state`.

    peak_exposure uses the replay's per-draw peak (true max money
    simultaneously sunk, incl. intra-day). Drawdown uses day pnl (realized
    closed runs + open-run spend sunk that day)."""
    state["max_exposure"] = max(state.get("max_exposure", 0),
                                res.get("peak_exposure", 0))
    state["cum_realized"] = state.get("cum_realized", 0) + res["pnl"]
    state["min_cum"] = min(state.get("min_cum", 0), state["cum_realized"])
    state["max_wager_any"] = max(
        state.get("max_wager_any", 0),
        max((tier_wager(r["end_tier"]) for r in res["runs"]), default=0),
        max((tier_wager(r["tier"]) for r in res["open_runs"]), default=0),
        max((e.get("wager", 0) for e in res["events"]), default=0))
    state["max_run_sunk"] = max(
        state.get("max_run_sunk", 0),
        max((r["cum_spend"] for r in res["runs"]), default=0),
        max((r["cum_spend"] for r in res["open_runs"]), default=0))


# ------------------------------------------------------------------ build
def build_sim(store_path=STORE, out_path=OUT, full=False, today=None,
              quiet=False):
    """Full-history ledger. Days older than the newest RECOMPUTE_DAYS freeze
    from a previous sim.json (same SIM_VERSION + config); the newest days
    always recompute. full=True rebuilds everything."""
    draws = store.load_draws(store_path)
    if not draws:
        raise SystemExit("no draws in store")

    by_date = {}
    for d in draws:
        nd = analysis_web._norm_date(d.get("draw_date"))
        if not nd:
            continue
        by_date.setdefault(nd, []).append(d)
    dates = sorted(by_date.keys())
    for nd in dates:
        by_date[nd].sort(key=lambda x: x["game_no"])

    if today is None:
        today = dates[-1]
    recompute = set(dates[-RECOMPUTE_DAYS:]) if not full else set(dates)

    prev_days = {}
    if not full and out_path.exists():
        try:
            old = json.loads(out_path.read_text(encoding="utf-8"))
            old_cfg = old.get("config", {})
            same_cfg = (
                old.get("sim_version") == SIM_VERSION
                and old_cfg.get("tiers") == TIERS
                and old_cfg.get("tier_draws") == TIER_DRAWS
                and old_cfg.get("payout") == PAYOUT
                and old_cfg.get("double_start") == DOUBLE_START
                and old_cfg.get("overdue_window") == OVERDUE_WINDOW
                and old_cfg.get("overdue_threshold") == OVERDUE_THRESHOLD
            )
            if same_cfg:
                for row in old.get("ledger", []):
                    if row["date"] < today and row["date"] not in recompute:
                        row.pop("events", None)
                        row.pop("open_runs", None)
                        prev_days[row["date"]] = row
            elif not quiet:
                print("  [sim] config/version changed -> full rebuild")
        except Exception as e:
            if not quiet:
                print(f"  [sim] previous sim.json unreadable ({e}); full rebuild")

    sched = wager_schedule()
    all_ascending = sorted(draws, key=lambda x: x["game_no"])

    ledger = []
    carry = []
    rid = 0
    n_replayed = 0
    st = {}
    for nd in dates:
        if nd in prev_days:
            carry = prev_days[nd].get("carry") or []
            ledger.append(prev_days[nd])
            continue
        day_draws = by_date[nd]
        first_g = day_draws[0]["game_no"]
        window_before = [d["numbers"] for d in all_ascending
                         if d["game_no"] < first_g][-OVERDUE_WINDOW:]
        res = replay_day(day_draws, window_before, carry_runs=carry,
                         next_rid=rid)
        carry = res["carry_out"]
        rid = res["next_rid"]
        n_replayed += 1

        max_wager, pairs_played = _day_pairs(res)
        _track_exposure(carry, res, st)
        exposure = sum(c["cum_spend"] for c in carry)

        row = {
            "date": nd,
            "draws_played": res["draws_played"],
            "wagers": res["wagers"],
            "runs": len(res["runs"]),
            "hits": res["hits"],
            "exhausted": res["exhausted"],
            "staked": res["staked"],
            "won": res["won"],
            "pnl": res["pnl"],
            "max_wager": max_wager,
            "peak_exposure": exposure,
            "pairs": pairs_played,
            "carry": carry,
        }
        if nd == today:
            row["events"] = res["events"]
            row["open_runs"] = res["open_runs"]
        ledger.append(row)
        if not quiet and n_replayed % 20 == 0:
            print(f"  [sim] replayed {n_replayed} days... ({nd})")

    tot = {
        "days": len(ledger),
        "draws_played": sum(r["draws_played"] for r in ledger),
        "wagers": sum(r.get("wagers", r["draws_played"]) for r in ledger),
        "runs": sum(r["runs"] for r in ledger),
        "hits": sum(r["hits"] for r in ledger),
        "exhausted": sum(r.get("exhausted", 0) for r in ledger),
        "staked": sum(r["staked"] for r in ledger),
        "won": sum(r["won"] for r in ledger),
        "pnl": sum(r["pnl"] for r in ledger),
        "max_wager": st.get("max_wager_any", 0),
        "peak_exposure": st.get("max_exposure", 0),
        "max_run_sunk": st.get("max_run_sunk", 0),
        "max_drawdown": st.get("min_cum", 0),
    }
    tot["roi_pct"] = round(tot["pnl"] / tot["staked"] * 100, 2) if tot["staked"] else 0.0
    if tot["runs"]:
        tot["hit_rate_pct"] = round(tot["hits"] / tot["runs"] * 100, 2)

    sim = {
        "sim_version": SIM_VERSION,
        "config": _config(),
        "schedule": sched,
        "ladder": _ladder_display(sched),
        "today": today,
        "totals": tot,
        "ledger": ledger,
    }

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(sim, separators=(",", ":")),
                        encoding="utf-8")
    if not quiet:
        cur = next((r for r in ledger if r["date"] == today), None)
        opn = (cur or {}).get("open_runs") or []
        print(f"  [sim] wrote {out_path.name}: {tot['days']} days, "
              f"pnl {tot['pnl']:+.0f}, max_wager ${tot['max_wager']}, "
              f"peak_exposure ${tot['peak_exposure']}, "
              f"drawdown {tot['max_drawdown']:+.0f}, today {today} "
              f"pnl {(cur or {}).get('pnl', 0):+.0f}, open {len(opn)}")
    return sim


# ------------------------------------------------------------- live session
def build_live(store_path=STORE, out_path=LIVE_OUT, quiet=False,
               start_game=None):
    """Build the LIVE session (data/live.json).

    Anchored at a fixed draw ("now" at creation) and running forward forever.
    Every build replays the whole session from the anchor — a pure function
    of the CSV + anchor, so no state can ever drift. The first pick is
    seeded with the same-day draws that happened before the anchor, matching
    the site's current top overdue pair at session start.
    """
    draws = store.load_draws(store_path)
    dated = [d for d in draws if analysis_web._norm_date(d.get("draw_date"))]
    if not dated:
        raise SystemExit("no dated draws in store")
    dated.sort(key=lambda x: x["game_no"])
    newest = dated[-1]["game_no"]

    if start_game is None and out_path.exists():
        try:
            prev = json.loads(out_path.read_text(encoding="utf-8"))
            start_game = int(prev.get("start_game") or 0) or None
        except Exception:
            start_game = None
    if start_game is None:
        start_game = newest + 1

    by_date = {}
    for d in dated:
        by_date.setdefault(analysis_web._norm_date(d["draw_date"]), []).append(d)
    dates = sorted(by_date)

    sched = wager_schedule()
    session_draws = [d for d in dated if d["game_no"] >= start_game]

    days = []
    runs_all = []
    events_all = []
    carry = []
    open_runs = []
    staked = won = draws_played = hits = exhausted = pnl = 0
    rid = 0
    st = {}
    last_time = None
    start_date = None

    if session_draws:
        anchor_date = analysis_web._norm_date(session_draws[0]["draw_date"])
        start_date = anchor_date
        for nd in [x for x in dates if x >= anchor_date]:
            day_draws = [d for d in by_date[nd] if d["game_no"] >= start_game]
            if not day_draws:
                continue
            first_g = day_draws[0]["game_no"]
            window_before = [d["numbers"] for d in dated
                             if d["game_no"] < first_g][-OVERDUE_WINDOW:]
            seed = ([d["numbers"] for d in by_date[nd]
                     if d["game_no"] < start_game]
                    if nd == anchor_date else [])
            res = replay_day(day_draws, window_before, carry_runs=carry,
                            seed_day=seed, next_rid=rid)
            carry = res["carry_out"]
            rid = res["next_rid"]
            staked += res["staked"]
            won += res["won"]
            draws_played += res["draws_played"]
            hits += res["hits"]
            exhausted += res["exhausted"]
            pnl += res["pnl"]   # realized today + today's open unrealized
            runs_all += res["runs"]
            events_all += res["events"]
            open_runs = res["open_runs"]
            last_time = day_draws[-1].get("draw_time")

            max_wager, pairs_played = _day_pairs(res)
            _track_exposure(carry, res, st)
            exposure = sum(c["cum_spend"] for c in carry)

            days.append({
                "date": nd,
                "draws_played": res["draws_played"],
                "wagers": res["wagers"],
                "runs": len(res["runs"]),
                "hits": res["hits"],
                "exhausted": res["exhausted"],
                "staked": res["staked"],
                "won": res["won"],
                "pnl": res["pnl"],
                "max_wager": max_wager,
                "peak_exposure": exposure,
                "pairs": pairs_played,
            })
    else:
        start_date = analysis_web._norm_date(dated[-1]["draw_date"])

    stats = {
        "draws_played": draws_played,
        "wagers": sum(1 for e in events_all if e.get("wager")),
        "runs": len(runs_all),
        "hits": hits,
        "exhausted": exhausted,
        "staked": staked,
        "won": won,
        "pnl": pnl,
        "roi_pct": round(pnl / staked * 100, 2) if staked else 0.0,
        "hit_rate_pct": (round(hits / len(runs_all) * 100, 2)
                         if runs_all else None),
        "days": len(days),
        "max_wager": st.get("max_wager_any", 0),
        "peak_exposure": st.get("max_exposure", 0),
        "max_run_sunk": st.get("max_run_sunk", 0),
        "max_drawdown": st.get("min_cum", 0),
        "open_exposure": sum(r["cum_spend"] for r in open_runs),
    }

    live = {
        "start_game": start_game,
        "start_date": start_date,
        "last_game": session_draws[-1]["game_no"] if session_draws else None,
        "last_draw_time": last_time,
        "config": _config(),
        "schedule": sched,
        "ladder": _ladder_display(sched),
        "stats": stats,
        "active_runs": [
            {
                "rid": r["rid"], "pair": r["pair"], "tier": r["tier"],
                "wager": tier_wager(r["tier"]),
                "draws_at_tier": r["draws_at_tier"],
                "tier_draws_left": tier_draws(r["tier"]) - r["draws_at_tier"],
                "spend": r["spend"], "cum_spend": r["cum_spend"],
                "draws_total": r["draws_total"],
                "doubling": r["tier"] >= n_base(),
                "profit_if_hit": PAYOUT * tier_wager(r["tier"]) - r["cum_spend"],
                "next_wager": tier_wager(r["tier"] + 1),
                "started_game": r["first_game"],
                "last_game": r["last_game"],
            } for r in open_runs
        ],
        "days": days,
        "runs": runs_all,
        "events": events_all[-LIVE_EVENT_CAP:],
        "open_runs": open_runs,
    }

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(live, separators=(",", ":")),
                        encoding="utf-8")
    if not quiet:
        act = live["active_runs"]
        print(f"  [live] wrote {out_path.name}: start #{start_game}, "
              f"{draws_played} draws, pnl {pnl:+.0f}, "
              f"active {[str(r['pair']) + '@$' + str(r['wager']) for r in act]}")
    return live


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--full", action="store_true",
                    help="recompute every day from scratch (slow first backfill)")
    ap.add_argument("--live", action="store_true",
                    help="build only the live session (data/live.json)")
    ap.add_argument("--store", type=Path, default=STORE)
    ap.add_argument("--out", type=Path, default=OUT)
    a = ap.parse_args()
    if a.live:
        build_live(a.store, LIVE_OUT)
    else:
        build_sim(a.store, a.out, full=a.full)