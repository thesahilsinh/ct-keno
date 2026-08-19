from pathlib import Path

import payouts
import simulate

FIX = Path(__file__).parent / "fixtures" / "keno_one_day.html"
# load paytable once for the module
payouts.load_payouts((Path(__file__).parent / "fixtures" / "keno_htp.html").read_text())


def test_replay_fixed_no_bonus():
    from scraper import parse_draws_html
    draws = parse_draws_html(FIX.read_text())
    s = simulate.Strategy(spots=4, mode="fixed", picks=[1, 2, 3, 4], wager=1, bonus=False)
    res = simulate.replay_history(draws[:10], s)
    assert res.total_wagered == 10
    assert res.net == round(res.total_won - res.total_wagered, 2)
    assert len(res.per_draw) == 10


def test_bonus_doubles_cost_and_multiplies_win():
    draws = [{"game_no": 1,
              "numbers": [1, 2, 3, 4, 5, 6, 7, 8, 9, 10,
                          11, 12, 13, 14, 15, 16, 17, 18, 19, 20],
              "bonus": "10X"}]
    s = simulate.Strategy(spots=4, mode="fixed", picks=[1, 2, 3, 4], wager=1, bonus=True)
    res = simulate.replay_history(draws, s)
    assert res.total_wagered == 2                 # double cost with bonus
    assert res.total_won == 100 * 10             # 4-match = $100, x10 bonus


def test_monte_carlo_returns_distribution():
    from scraper import parse_draws_html
    draws = parse_draws_html(FIX.read_text())
    s = simulate.Strategy(spots=4, mode="quickpick", wager=1, bonus=False)
    dist = simulate.monte_carlo(draws, s, session_draws=20, sessions=200, seed=1)
    assert len(dist) == 200
    assert all(isinstance(x, float) for x in dist)


def test_theoretical_ev_negative():
    ev = simulate.theoretical_ev(spot=4, wager=1, bonus=False)
    assert ev < 0                                # keno is always negative-EV
