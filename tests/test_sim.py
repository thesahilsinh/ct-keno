"""Tests for sim.py — the 2-spot pair-chase ladder engine.

Covers: ladder math (strict profitability), site-parity pair picking,
hit / exhaust / carry flows, determinism, and ledger freezing.
Run:  python tests/test_sim.py
"""
import inspect
import json
import sys
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import sim
import store
import analysis_web

STORE = Path(__file__).resolve().parent.parent / "data" / "draws.csv"


# ------------------------------------------------------------ schedule
def test_schedule_math():
    sched = sim.wager_schedule()
    assert [(s["wager"], s["draws"], s["cum_spend"]) for s in sched] == [
        (1, 10, 10), (2, 5, 20), (3, 4, 32), (4, 2, 40),
        (5, 2, 50), (10, 5, 100), (20, 5, 200),
    ]
    # strict profitability: a hit at ANY allowed draw j at tier w must return
    # more than spent so far INCLUDING that draw's wager.
    prev = 0
    for s in sched:
        w, j = s["wager"], s["draws"]
        for jj in range(1, j + 1):
            assert prev + jj * w < 11 * w, (w, jj)
        prev = s["cum_spend"]
    # tightness: one MORE draw at each tier would break the rule
    prev = 0
    for s in sched:
        w, j = s["wager"], s["draws"]
        assert prev + (j + 1) * w >= 11 * w
        prev = s["cum_spend"]
    assert sum(s["draws"] for s in sched) == 33
    assert sched[-1]["cum_spend"] == 200


# ------------------------------------------------------- pair picking
def test_pick_pair_matches_site_overdue():
    """Port-parity: over the same window, PairTracker.rank == site top pair."""
    draws = store.load_draws(STORE)
    draws.sort(key=lambda d: d["game_no"], reverse=True)
    today = analysis_web._today_str(draws)
    day = [d for d in draws
           if analysis_web._norm_date(d.get("draw_date")) == today]
    if len(day) < 20:      # data pulled before the day got going
        print("skip parity (short day)")
        return
    site = analysis_web.compute_overdue(day, window=len(day))
    tr = sim.PairTracker()
    for d in reversed(day):            # oldest first
        tr.add_draw(sorted(set(d["numbers"])))
    mine = tr.rank()
    if site["pairs"]:
        assert mine == site["pairs"][0]["combo"], (mine, site["pairs"][0])


def test_pick_pair_respects_threshold_and_exclude():
    tr = sim.PairTracker()
    base = list(range(1, 21))
    for _ in range(40):
        tr.add_draw(base)
    # every pair inside `base` is 0 draws since last seen -> not overdue
    assert tr.rank() is None
    # exclude must suppress an otherwise-qualifying pair
    tr2 = sim.PairTracker()
    for _ in range(40):
        tr2.add_draw([1, 2] + list(range(3, 21)))
    for _ in range(20):               # 20 draws without [1,2] -> overdue
        tr2.add_draw(list(range(21, 41)))
    picked = tr2.rank()
    assert picked == [1, 2]
    assert tr2.rank(exclude={(1, 2)}) != [1, 2]


# ------------------------------------------------------------ helpers
def _mkdraw(g, nums):
    return {"game_no": g, "numbers": sorted(set(nums)), "bonus": "No Bonus",
            "draw_date": "2026-01-01", "draw_time": ""}


def _warm(pair, n_old=30, n_recent=20):
    """Window where `pair` appeared n_old times but NOT in the last n_recent
    draws -> it is the top overdue pick (highest count, lex-min tie-break).
    Filler numbers are all > max(pair) so no other pair can lex-beat it."""
    hi = max(pair)
    filler = list(range(hi + 1, hi + 19))
    old = [sorted(set(pair + filler)) for _ in range(n_old)]
    recent = [list(range(61, 81)) for _ in range(n_recent)]
    return old + recent


def _miss_nums(i, pair):
    """20 numbers guaranteed NOT to contain both pair members."""
    out = [x for x in range((i * 3) % 60 + 1, (i * 3) % 60 + 21)
           if x not in pair]
    return (out + [x for x in range(61, 81)])[:20]


# ------------------------------------------------------------ replay
def test_replay_hit_closes_run_profitable():
    sched = sim.wager_schedule()
    warm = _warm([1, 2])
    # draw 1 misses, draw 2 hits [1,2] -> spend $2, prize $11, pnl +9
    day = [_mkdraw(101, list(range(10, 30))),
           _mkdraw(102, [1, 2] + list(range(3, 21)))]
    res = sim.replay_day(day, warm, sched)
    assert res["hits"] == 1
    r = res["runs"][0]
    assert r["pair"] == [1, 2]
    assert r["spend"] == 2
    assert r["pnl"] == 11 - 2
    assert r["result"] == "hit"
    assert res["pnl"] == 9


def test_replay_ladder_escalates_and_exhausts():
    sched = sim.wager_schedule()
    warm = _warm([7, 9])
    # 33 draws where [7,9] never appears -> run exhausts at -$200
    day = [_mkdraw(100 + i, _miss_nums(i, (7, 9))) for i in range(33)]
    res = sim.replay_day(day, warm, sched)
    assert len(res["runs"]) == 1
    r = res["runs"][0]
    assert r["result"] == "exhausted"
    assert r["spend"] == 200
    assert r["pnl"] == -200
    assert r["draws"] == 33
    # exhaustion -> SAME pair gets a fresh $1 ladder, run re-opens next draw
    assert res["carry_out"] == [7, 9]
    if res["open_run"]:
        assert res["open_run"]["pair"] == [7, 9]
        assert res["open_run"]["tier"] == 0
        assert res["open_run"]["spend"] == 0
    # wager sequence must match the schedule exactly
    wagers = [e["wager"] for e in res["events"] if e.get("wager")]
    expect = []
    for s in sched:
        expect += [s["wager"]] * s["draws"]
    assert wagers == expect


def test_replay_exhausted_pair_keeps_chasing():
    """After exhaustion the SAME pair is chased again with a fresh ladder."""
    sched = sim.wager_schedule()
    warm = _warm([7, 9])

    def miss(i):
        start = (i * 3) % 60 + 1
        nums = [x for x in range(start, start + 20) if x not in (7, 9)]
        return (nums + [x for x in range(61, 81)])[:20]

    day = [_mkdraw(100 + i, miss(i)) for i in range(33)]   # exhaust ladder
    day += [_mkdraw(200 + i, miss(i)) for i in range(9)]   # 9 misses at $1
    day.append(_mkdraw(300, [7, 9] + list(range(10, 28))))  # 10th draw HIT
    res = sim.replay_day(day, warm, sched)
    assert len(res["runs"]) == 2
    assert res["runs"][0]["result"] == "exhausted"
    assert res["runs"][1]["result"] == "hit"
    assert res["runs"][1]["pair"] == [7, 9]
    assert res["runs"][1]["spend"] == 10       # fresh ladder: 10 draws at $1
    assert res["runs"][1]["pnl"] == 11 - 10
    assert res["pnl"] == -200 + 1


def test_replay_daily_reset_and_carry():
    """Ladder resets next day; un-hit pair carries across midnight."""
    sched = sim.wager_schedule()
    warm = _warm([5, 6])
    # day 1: 5 misses at $1 (tier not exhausted), day ends -> pair carries
    d1 = [_mkdraw(10 + i, list(range(30, 50))) for i in range(5)]
    r1 = sim.replay_day(d1, warm, sched)
    assert r1["open_run"] and r1["open_run"]["pair"] == [5, 6]
    assert r1["open_run"]["spend"] == 5
    assert r1["carry_out"] == [5, 6]
    # day 2: FIRST draw continues the SAME pair at $1 (fresh ladder)
    d2 = [_mkdraw(50, list(range(30, 50))),
          _mkdraw(51, [5, 6] + list(range(3, 21)))]
    r2 = sim.replay_day(d2, warm, sched, carry_pair=[5, 6])
    ev = [e for e in r2["events"] if e.get("wager")]
    assert ev[0]["pair"] == [5, 6]
    assert ev[0]["wager"] == 1
    assert r2["runs"][0]["pair"] == [5, 6]
    # spend: draw50 miss $1 + draw51 hit $1 -> 11 - 2 = +9
    assert r2["runs"][0]["pnl"] == 9


def test_replay_hit_pair_not_repicked_same_day():
    """A pair that hit today must not be re-picked later the same day."""
    sched = sim.wager_schedule()
    warm = _warm([1, 2])
    # draw 1 hits [1,2]; later draws keep containing [1,2] — the engine
    # must pick something ELSE rather than re-chase [1,2].
    day = [_mkdraw(1, [1, 2] + list(range(3, 21)))]
    for i in range(5):
        day.append(_mkdraw(10 + i, [1, 2] + list(range(3, 21))))
    res = sim.replay_day(day, warm, sched)
    for e in res["events"]:
        if e.get("wager") and e["game"] > 1:
            assert e["pair"] != [1, 2], e


def test_replay_skips_tiny_window():
    sched = sim.wager_schedule()
    day = [_mkdraw(1, list(range(1, 21))), _mkdraw(2, list(range(2, 22)))]
    res = sim.replay_day(day, [], sched)     # no history at all
    assert res["staked"] == 0
    assert all(e["result"] == "skip" for e in res["events"])


# ------------------------------------------------------------- build
def test_build_deterministic_and_shape():
    """build_sim twice on the same store -> identical results + shape."""
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        out1, out2 = Path(td) / "s1.json", Path(td) / "s2.json"
        draws = store.load_draws(STORE)
        draws.sort(key=lambda d: d["game_no"], reverse=True)
        dates = sorted({analysis_web._norm_date(d["draw_date"])
                        for d in draws if analysis_web._norm_date(d["draw_date"])})
        # use the OLDEST day only -> fast: single-day ledger
        first = dates[0]
        sim.build_sim(STORE, out1, full=True, today=first, quiet=True)
        sim.build_sim(STORE, out2, full=True, today=first, quiet=True)
        b1 = json.loads(out1.read_text(encoding="utf-8"))
        b2 = json.loads(out2.read_text(encoding="utf-8"))
        assert b1 == b2
        assert b1["sim_version"] == 1
        assert b1["today"] == first
        for k in ("days", "draws_played", "runs", "hits", "staked", "won",
                  "pnl", "roi_pct"):
            assert k in b1["totals"], k
        # today's row carries events + open_run; ledger sorted by date
        today_row = next(r for r in b1["ledger"] if r["date"] == first)
        assert "events" in today_row and "open_run" in today_row
        assert b1["ledger"] == sorted(b1["ledger"], key=lambda r: r["date"])


def test_build_freeze_and_recompute():
    """Past days freeze; the newest days recompute even when a ledger exists."""
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        out = Path(td) / "s.json"
        draws = store.load_draws(STORE)
        draws.sort(key=lambda d: d["game_no"], reverse=True)
        dates = sorted({analysis_web._norm_date(d["draw_date"])
                        for d in draws if analysis_web._norm_date(d["draw_date"])})
        first, last = dates[0], dates[-1]
        # ledger with only the oldest day
        sim.build_sim(STORE, out, full=True, today=first, quiet=True)
        # extend: everything after `first` recomputes; first stays frozen
        sim.build_sim(STORE, out, full=False, today=last, quiet=True)
        b = json.loads(out.read_text(encoding="utf-8"))
        assert b["today"] == last
        assert len(b["ledger"]) == len(dates)
        # tamper a mid past row -> rebuild must NOT touch it (frozen),
        # but today's row must refresh (recomputed -> tamper gone)
        mid = b["ledger"][len(b["ledger"]) // 2]["date"]
        for row in b["ledger"]:
            if row["date"] == mid:
                row["pnl"] = 999999
        out.write_text(json.dumps(b, separators=(",", ":")), encoding="utf-8")
        sim.build_sim(STORE, out, full=False, today=last, quiet=True)
        b2 = json.loads(out.read_text(encoding="utf-8"))
        mid2 = next(r for r in b2["ledger"] if r["date"] == mid)
        assert mid2["pnl"] == 999999           # frozen: untouched
        today2 = next(r for r in b2["ledger"] if r["date"] == last)
        assert "events" in today2              # recomputed: events attached


# ---------------------------------------------------------------- runner
if __name__ == "__main__":
    fails = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                if len(inspect.signature(fn).parameters) == 0:
                    fn()
                else:
                    fn(None)
                print(f"PASS {name}")
            except Exception as e:
                fails += 1
                print(f"FAIL {name}: {e}")
                traceback.print_exc()
    print("ALL PASS" if fails == 0 else f"{fails} FAILURES")
    sys.exit(1 if fails else 0)