"""Tests for sim.py v2 — pair chase + doubling continuation + parallel pairs.

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
def test_base_ladder_math():
    bt = sim.base_tiers()
    assert [(s["wager"], s["draws"], s["cum_spend"]) for s in bt] == [
        (1, 10, 10), (2, 5, 20), (3, 4, 32), (4, 2, 40),
        (5, 2, 50), (10, 5, 100), (20, 5, 200),
    ]
    prev = 0
    for s in bt:
        w, j = s["wager"], s["draws"]
        for jj in range(1, j + 1):
            assert prev + jj * w < 11 * w, (w, jj)
        prev = s["cum_spend"]
    assert sum(s["draws"] for s in bt) == 33
    assert bt[-1]["cum_spend"] == 200


def test_doubling_schedule():
    """Doubling tiers: 40, 80, 160, 320, ... x5 draws each; spend before
    each doubling tier is exactly 5*w -> every hit strictly profitable
    (user's $40 -> $440 = +$40 minimum math)."""
    nb = sim.n_base()
    assert sim.tier_wager(nb) == 40
    assert sim.tier_wager(nb + 1) == 80
    assert sim.tier_wager(nb + 2) == 160
    assert sim.tier_wager(nb + 3) == 320
    assert sim.tier_draws(nb) == 5
    # cumulative spend before doubling tier k: 200, 400, 800, 1600, ...
    spend = 0
    for i in range(nb + 4):
        spend += sim.tier_wager(i) * sim.tier_draws(i)
        w_next = sim.tier_wager(i + 1)
        if i + 1 >= nb:                 # at every doubling boundary
            assert spend == 5 * w_next, (i, spend, w_next)
    # full display schedule: 14 tiers, cum through last doubling display tier
    sched = sim.wager_schedule()
    assert len(sched) == 7 + sim.DOUBLE_DISPLAY
    assert sched[-1]["wager"] == 40 * 2 ** (sim.DOUBLE_DISPLAY - 1)
    assert sched[-1]["cum_spend"] == 200 + 5 * sum(
        40 * 2 ** k for k in range(sim.DOUBLE_DISPLAY))
    # every tier's hit is strictly profitable (worst case = last tier draw)
    for s in sched:
        assert 11 * s["wager"] - s["cum_spend"] >= 1, s
    # user's worked examples
    lad = sim._ladder_display(sched)
    by_w = {t["wager"]: t for t in lad}
    assert by_w[40]["profit_min"] == 40      # 440 - 400
    assert by_w[20]["profit_min"] == 20      # 220 - 200
    assert by_w[2]["profit_min"] == 2         # 22 - 20


# ------------------------------------------------------- pair picking
def test_pick_pair_matches_site_overdue():
    draws = store.load_draws(STORE)
    draws.sort(key=lambda d: d["game_no"], reverse=True)
    today = analysis_web._today_str(draws)
    day = [d for d in draws
           if analysis_web._norm_date(d.get("draw_date")) == today]
    if len(day) < 20:
        print("skip parity (short day)")
        return
    site = analysis_web.compute_overdue(day, window=len(day))
    tr = sim.PairTracker()
    for d in reversed(day):
        tr.add_draw(sorted(set(d["numbers"])))
    mine = tr.rank()
    if site["pairs"]:
        assert mine == site["pairs"][0]["combo"], (mine, site["pairs"][0])


def test_pick_pair_respects_threshold_and_exclude():
    tr = sim.PairTracker()
    for _ in range(40):
        tr.add_draw(list(range(1, 21)))
    assert tr.rank() is None
    tr2 = sim.PairTracker()
    for _ in range(40):
        tr2.add_draw([1, 2] + list(range(3, 21)))
    for _ in range(20):
        tr2.add_draw(list(range(21, 41)))
    assert tr2.rank() == [1, 2]
    assert tr2.rank(exclude={(1, 2)}) != [1, 2]


# ------------------------------------------------------------ helpers
def _mkdraw(g, nums):
    return {"game_no": g, "numbers": sorted(set(nums)), "bonus": "No Bonus",
            "draw_date": "2026-01-01", "draw_time": ""}


def _warm(pair, n_old=30, n_recent=20):
    """Window where `pair` is frequent but absent for the last n_recent draws
    -> top overdue pick."""
    hi = max(pair)
    filler = list(range(hi + 1, hi + 19))
    old = [sorted(set(pair + filler)) for _ in range(n_old)]
    recent = [list(range(61, 81)) for _ in range(n_recent)]
    return old + recent


def _miss_nums(i, pair):
    out = [x for x in range((i * 3) % 60 + 1, (i * 3) % 60 + 21)
           if x not in pair]
    return (out + [x for x in range(61, 81)])[:20]


# ------------------------------------------------------------ replay v2
def test_replay_hit_banks_and_spawns():
    warm = _warm([1, 2])
    day = [_mkdraw(101, list(range(10, 30))),
           _mkdraw(102, [1, 2] + list(range(3, 21)))]
    res = sim.replay_day(day, warm)
    assert res["hits"] == 1
    r = res["runs"][0]
    assert r["pair"] == [1, 2]
    assert r["spend"] == 2
    assert r["pnl"] == 9
    assert res["pnl"] == 9


def test_replay_exhaustion_doubles_and_spawns_parallel():
    """Base ladder exhaust -> SAME pair continues at $40 (doubling) AND a
    parallel run opens on the next top overdue pair at $1."""
    warm = _warm([7, 9])
    day = [_mkdraw(100 + i, _miss_nums(i, (7, 9))) for i in range(33)]
    day += [_mkdraw(200, _miss_nums(0, (7, 9))),
            _mkdraw(201, _miss_nums(1, (7, 9)))]
    res = sim.replay_day(day, warm)
    ex = [e for e in res["events"] if e.get("result") == "exhausted"]
    assert len(ex) == 1
    assert ex[0]["cum_spend"] == 200
    # after exhaustion: TWO runs active (doubling [7,9] + spawned parallel)
    opens = res["open_runs"]
    assert len(opens) == 2, opens
    pairs = {tuple(r["pair"]) for r in opens}
    assert (7, 9) in pairs
    doubling = [r for r in opens if tuple(r["pair"]) == (7, 9)][0]
    assert doubling["tier"] == sim.n_base()
    assert sim.tier_wager(doubling["tier"]) == 40
    other = [r for r in opens if tuple(r["pair"]) != (7, 9)][0]
    assert other["tier"] == 0 and other["spend"] <= 2   # fresh $1 ladder
    # wager sequence on [7,9]: base ladder then $40 doubling
    ws = [e["wager"] for e in res["events"]
          if e.get("pair") == [7, 9] and e.get("wager")]
    expect = [1] * 10 + [2] * 5 + [3] * 4 + [4] * 2 + [5] * 2 + \
             [10] * 5 + [20] * 5 + [40, 40]
    assert ws == expect


def test_replay_parallel_run_hits_and_doubling_continues():
    """Exhaust -> parallel spawns (opens NEXT draw). Parallel hits -> banks
    profit + schedules the next top pair. The doubling run keeps going."""
    warm = _warm([7, 9])
    day = [_mkdraw(100 + i, _miss_nums(i, (7, 9))) for i in range(33)]
    # probe after 34 draws: parallel has opened on draw 34
    res_probe = sim.replay_day(day + [_mkdraw(199, _miss_nums(9, (7, 9)))],
                               warm)
    spawned = [r for r in res_probe["open_runs"]
               if tuple(r["pair"]) != (7, 9)][0]["pair"]
    # main: draw 200 opens+wagers the parallel ($1 miss), draw 201 hits it
    day += [_mkdraw(200, _miss_nums(0, (7, 9)))]
    nums = [x for x in _miss_nums(1, (7, 9)) if x not in spawned][:18]
    day.append(_mkdraw(201, nums + spawned))
    res = sim.replay_day(day, warm)
    hit_runs = [r for r in res["runs"] if r["result"] == "hit"]
    assert len(hit_runs) == 1
    assert hit_runs[0]["pair"] == spawned
    assert hit_runs[0]["spend"] == 2 and hit_runs[0]["pnl"] == 9
    # [7,9] still open in doubling at $40
    opens = res["open_runs"]
    assert any(tuple(r["pair"]) == (7, 9) and r["tier"] == sim.n_base()
               for r in opens), opens
    # the next top pair is scheduled but opens at the NEXT draw (none left)
    assert len(opens) == 1, opens


def test_replay_doubling_hit_banks_big_profit():
    """Deep run: exhaust base (33 draws), then hit on the 5th $40 draw:
    $200 base + $200 doubling = $400 spent, prize $440 -> +$40 lifetime
    (the user's exact math)."""
    warm = _warm([7, 9])
    day = [_mkdraw(100 + i, _miss_nums(i, (7, 9))) for i in range(33)]
    for i in range(4):
        day.append(_mkdraw(200 + i, _miss_nums(i, (7, 9))))
    day.append(_mkdraw(300, [7, 9] + list(range(10, 28))))
    res = sim.replay_day(day, warm)
    hits = [r for r in res["runs"] if r["result"] == "hit"
            and tuple(r["pair"]) == (7, 9)]
    assert len(hits) == 1
    r = hits[0]
    assert r["end_tier"] == sim.n_base()
    assert r["spend"] == 200 + 5 * 40        # base + five $40 draws (day)
    assert r["cum_spend"] == 400
    assert r["pnl"] == 440 - 400             # +40 on the day
    assert r["run_pnl_total"] == 440 - 400   # +40 lifetime (user's math)
    # doubling hit closes the run; only the spawned parallel remains
    assert all(tuple(o["pair"]) != (7, 9) for o in res["open_runs"])


def test_replay_daily_reset_and_carry():
    """Base ladder resets next day at $1; lifetime cum_spend carries."""
    warm = _warm([5, 6])
    d1 = [_mkdraw(10 + i, list(range(30, 50))) for i in range(5)]
    r1 = sim.replay_day(d1, warm)
    assert len(r1["open_runs"]) == 1
    assert r1["open_runs"][0]["pair"] == [5, 6]
    assert r1["open_runs"][0]["spend"] == 5
    assert r1["open_runs"][0]["cum_spend"] == 5
    d2 = [_mkdraw(50, list(range(30, 50))),
          _mkdraw(51, [5, 6] + list(range(3, 21)))]
    r2 = sim.replay_day(d2, warm, carry_runs=r1["carry_out"])
    ev = [e for e in r2["events"] if e.get("wager")]
    assert ev[0]["pair"] == [5, 6] and ev[0]["wager"] == 1
    assert r2["runs"][0]["pair"] == [5, 6]
    assert r2["runs"][0]["cum_spend"] == 7      # 5 yesterday + 2 today
    assert r2["runs"][0]["pnl"] == 9            # today: 11 - 2


def test_replay_doubling_carry_preserved():
    """A doubling run carried across midnight keeps its tier + cum_spend."""
    warm = _warm([7, 9])
    day = [_mkdraw(100 + i, _miss_nums(i, (7, 9))) for i in range(35)]
    r1 = sim.replay_day(day, warm)
    dbl = [r for r in r1["open_runs"] if tuple(r["pair"]) == (7, 9)]
    assert dbl and dbl[0]["tier"] == sim.n_base()
    assert dbl[0]["cum_spend"] == 200 + 2 * 40
    d2 = [_mkdraw(300, _miss_nums(3, (7, 9)))]
    r2 = sim.replay_day(d2, warm, carry_runs=r1["carry_out"])
    dbl2 = [r for r in r2["open_runs"] if tuple(r["pair"]) == (7, 9)]
    assert dbl2 and dbl2[0]["tier"] == sim.n_base()
    assert dbl2[0]["cum_spend"] == 200 + 3 * 40
    ev = [e for e in r2["events"]
          if e.get("pair") == [7, 9] and e.get("wager")]
    assert ev and ev[0]["wager"] == 40


def test_replay_hit_pair_not_repicked_same_day():
    warm = _warm([1, 2])
    day = [_mkdraw(1, [1, 2] + list(range(3, 21)))]
    for i in range(5):
        day.append(_mkdraw(10 + i, [1, 2] + list(range(3, 21))))
    res = sim.replay_day(day, warm)
    for e in res["events"]:
        if e.get("wager") and e["game"] > 1:
            assert e["pair"] != [1, 2], e


def test_replay_parallel_draws_played_count():
    """With 2 runs active, one draw = 2 wagers but draws_played counts 1."""
    warm = _warm([7, 9])
    day = [_mkdraw(100 + i, _miss_nums(i, (7, 9))) for i in range(33)]
    day += [_mkdraw(200, _miss_nums(0, (7, 9)))]
    res = sim.replay_day(day, warm)
    assert res["wagers"] == 35          # 33 + 2 (two runs on draw 200)
    assert res["draws_played"] == 34    # 34 unique draws wagered
    # peak exposure after the 34th draw: [7,9] cum $200 base + first $40
    # doubling draw + parallel $1 = $241
    assert res["peak_exposure"] == 241


def test_replay_skips_tiny_window():
    day = [_mkdraw(1, list(range(1, 21))), _mkdraw(2, list(range(2, 22)))]
    res = sim.replay_day(day, [])     # no history at all
    assert res["staked"] == 0
    assert all(e["result"] == "skip" for e in res["events"])


# ------------------------------------------------------------- build
def test_build_deterministic_and_shape():
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        out1, out2 = Path(td) / "s1.json", Path(td) / "s2.json"
        draws = store.load_draws(STORE)
        draws.sort(key=lambda d: d["game_no"], reverse=True)
        dates = sorted({analysis_web._norm_date(d["draw_date"])
                        for d in draws if analysis_web._norm_date(d["draw_date"])})
        first = dates[0]
        sim.build_sim(STORE, out1, full=True, today=first, quiet=True)
        sim.build_sim(STORE, out2, full=True, today=first, quiet=True)
        b1 = json.loads(out1.read_text(encoding="utf-8"))
        b2 = json.loads(out2.read_text(encoding="utf-8"))
        assert b1 == b2
        assert b1["sim_version"] == 2
        for k in ("days", "draws_played", "runs", "hits", "staked", "won",
                  "pnl", "roi_pct", "max_wager", "peak_exposure",
                  "max_run_sunk", "max_drawdown"):
            assert k in b1["totals"], k
        assert b1["ledger"] == sorted(b1["ledger"], key=lambda r: r["date"])


def test_build_freeze_and_recompute():
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        out = Path(td) / "s.json"
        draws = store.load_draws(STORE)
        draws.sort(key=lambda d: d["game_no"], reverse=True)
        dates = sorted({analysis_web._norm_date(d["draw_date"])
                        for d in draws if analysis_web._norm_date(d["draw_date"])})
        first, last = dates[0], dates[-1]
        sim.build_sim(STORE, out, full=True, today=first, quiet=True)
        sim.build_sim(STORE, out, full=False, today=last, quiet=True)
        b = json.loads(out.read_text(encoding="utf-8"))
        assert b["today"] == last
        assert len(b["ledger"]) == len(dates)
        mid = b["ledger"][len(b["ledger"]) // 2]["date"]
        for row in b["ledger"]:
            if row["date"] == mid:
                row["pnl"] = 999999
        out.write_text(json.dumps(b, separators=(",", ":")), encoding="utf-8")
        sim.build_sim(STORE, out, full=False, today=last, quiet=True)
        b2 = json.loads(out.read_text(encoding="utf-8"))
        mid2 = next(r for r in b2["ledger"] if r["date"] == mid)
        assert mid2["pnl"] == 999999
        today2 = next(r for r in b2["ledger"] if r["date"] == last)
        assert "events" in today2


def test_build_config_change_forces_full_rebuild():
    """A previous ledger written by a different SIM_VERSION must be ignored
    (all days recomputed), not frozen."""
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        out = Path(td) / "s.json"
        draws = store.load_draws(STORE)
        draws.sort(key=lambda d: d["game_no"], reverse=True)
        dates = sorted({analysis_web._norm_date(d["draw_date"])
                        for d in draws if analysis_web._norm_date(d["draw_date"])})
        first = dates[0]
        sim.build_sim(STORE, out, full=True, today=first, quiet=True)
        b = json.loads(out.read_text(encoding="utf-8"))
        # tamper + mark as a different version
        b["sim_version"] = 99
        b["ledger"][0]["pnl"] = 999999
        out.write_text(json.dumps(b, separators=(",", ":")), encoding="utf-8")
        sim.build_sim(STORE, out, full=False, today=dates[-1], quiet=True)
        b2 = json.loads(out.read_text(encoding="utf-8"))
        day0 = next(r for r in b2["ledger"] if r["date"] == first)
        assert day0["pnl"] != 999999          # recomputed: tamper gone


# ------------------------------------------------------------- live session
def test_live_anchor_and_seed():
    import tempfile
    draws = store.load_draws(STORE)
    draws.sort(key=lambda d: d["game_no"], reverse=True)
    with tempfile.TemporaryDirectory() as td:
        out = Path(td) / "live.json"
        live1 = sim.build_live(STORE, out, quiet=True)
        assert live1["start_game"] == draws[0]["game_no"] + 1
        assert live1["stats"]["draws_played"] == 0
        assert live1["last_game"] is None
        live2 = sim.build_live(STORE, out, quiet=True)
        assert live2["start_game"] == live1["start_game"]
        anchor = draws[1]["game_no"] + 1
        live3 = sim.build_live(STORE, out, quiet=True, start_game=anchor)
        assert live3["stats"]["draws_played"] == 1
        assert live3["last_game"] == draws[0]["game_no"]
        ev = live3["events"]
        assert ev and ev[0]["wager"] == 1


def test_live_session_math_and_carry():
    """Multi-day session: P&L conservation, doubling carry across midnight,
    active_runs panel shape."""
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        out = Path(td) / "live.json"
        draws = store.load_draws(STORE)
        draws.sort(key=lambda d: d["game_no"], reverse=True)
        dates = sorted({analysis_web._norm_date(d["draw_date"])
                        for d in draws if analysis_web._norm_date(d["draw_date"])})
        anchor_date = dates[-3]
        anchor = min(d["game_no"] for d in draws
                     if analysis_web._norm_date(d["draw_date"]) == anchor_date)
        live = sim.build_live(STORE, out, quiet=True, start_game=anchor)
        s = live["stats"]
        assert s["days"] == 3
        assert sum(d["pnl"] for d in live["days"]) == s["pnl"]
        # every closed hit run is profitable ON THE DAY (strict-profit rule
        # holds within a day). Lifetime can be flat/negative ONLY for base
        # runs carried across a daily reset; doubling hits are always
        # lifetime-profitable by construction.
        for r in live["runs"]:
            if r["result"] == "hit":
                assert r["pnl"] > 0, r
                if r["end_tier"] >= sim.n_base():
                    assert r["run_pnl_total"] >= sim.DOUBLE_START, r
                else:
                    assert r["run_pnl_total"] <= r["pnl"], r  # carry can only cost
        # events consistent with unique wagered draws
        assert s["draws_played"] == len({e["game"] for e in live["events"]
                                         if e.get("wager")})
        # anchor persisted
        live2 = sim.build_live(STORE, out, quiet=True)
        assert live2["start_game"] == anchor
        # active_runs shape + honesty: every active wager strictly profitable
        for r in live["active_runs"]:
            assert r["wager"] in (1, 2, 3, 4, 5, 10, 20) or \
                r["wager"] in [40 * 2 ** k for k in range(10)]
            assert r["profit_if_hit"] >= 1


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