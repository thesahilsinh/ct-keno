from analysis_web import compute_overdue


def test_overdue_thresholds_match_odds():
    # pair 1 in 16.63 -> 16, triplet 1 in 72.07 -> 72, quad 1 in 326.44 -> 326
    draws = [{"game_no": i, "numbers": list(range(1, 21))} for i in range(10, 0, -1)]
    od = compute_overdue(draws, window=2000)
    assert od["thresholds"]["pair"] == 16
    assert od["thresholds"]["triplet"] == 72
    assert od["thresholds"]["quad"] == 326


def test_overdue_only_returns_combos_at_or_past_threshold():
    # 10 draws, all numbers 1..20. A pair like (1,2) appears every draw -> last=0.
    # A pair like (21,22) never appears -> not in counts at all (never seen).
    draws = [{"game_no": i, "numbers": list(range(1, 21))} for i in range(10, 0, -1)]
    od = compute_overdue(draws, window=2000)
    # every returned combo must have last >= its threshold
    for c in od["pairs"]:
        assert c["last"] >= od["thresholds"]["pair"]
    for c in od["triplets"]:
        assert c["last"] >= od["thresholds"]["triplet"]
    for c in od["quads"]:
        assert c["last"] >= od["thresholds"]["quad"]


def test_overdue_sorted_by_last_asc():
    # ascending: threshold (16) -> max
    draws = [{"game_no": i, "numbers": list(range(1, 21))} for i in range(10, 0, -1)]
    od = compute_overdue(draws, window=2000)
    lasts = [c["last"] for c in od["pairs"]]
    assert lasts == sorted(lasts)
