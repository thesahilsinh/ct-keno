from analysis_web import compute_proportion


def test_proportion_baseline_and_sorting():
    # 4 draws, each with the SAME 20 numbers (1..20).
    draws = [{"game_no": i, "numbers": list(range(1, 21))} for i in range(4, 0, -1)]
    p = compute_proportion(draws)
    assert p["baseline_pct"] == 25.0
    assert p["total_draws"] == 4
    # numbers 1..20 appear every draw -> 100%; 21..80 never -> 0%
    by_num = {x["num"]: x for x in p["numbers"]}
    assert by_num[1]["proportion_pct"] == 100.0
    assert by_num[80]["proportion_pct"] == 0.0
    # sorted most-below-25% first: 21..80 (0%) come before 1..20 (100%)
    assert p["numbers"][0]["num"] in range(21, 81)
    assert p["numbers"][-1]["num"] in range(1, 21)


def test_proportion_window_limits_scope():
    # 100 draws where number 1 appears in the first 50 only.
    draws = []
    for i in range(100, 0, -1):
        nums = [1] + list(range(2, 21)) if i <= 50 else list(range(2, 21))
        draws.append({"game_no": i, "numbers": nums})
    full = compute_proportion(draws)
    win = compute_proportion(draws, window=50)
    by_full = {x["num"]: x for x in full["numbers"]}
    by_win = {x["num"]: x for x in win["numbers"]}
    # full: #1 appears 50/100 = 50%; window(50): #1 appears 0/50 = 0%
    assert by_full[1]["proportion_pct"] == 50.0
    assert by_win[1]["proportion_pct"] == 0.0
    assert win["total_draws"] == 50
