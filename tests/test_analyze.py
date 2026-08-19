from pathlib import Path

import payouts
import simulate
import analyze

FIX = Path(__file__).parent / "fixtures" / "keno_one_day.html"
payouts.load_payouts((Path(__file__).parent / "fixtures" / "keno_htp.html").read_text())


def _result():
    from scraper import parse_draws_html
    draws = parse_draws_html(FIX.read_text())
    s = simulate.Strategy(spots=4, mode="quickpick", wager=1, bonus=False)
    return simulate.replay_history(draws, s)


def test_summarize_basic():
    st = analyze.summarize(_result())
    assert st["roi_pct"] == round(100 * st["net"] / st["total_wagered"], 2)
    assert st["longest_losing_streak"] >= 0
    assert st["draws"] > 0


def test_distribution_stats():
    nets = simulate.monte_carlo(
        parse_fixture(), simulate.Strategy(spots=4, mode="quickpick", wager=1, bonus=False),
        session_draws=20, sessions=100, seed=3)
    ds = analyze.distribution_stats(nets)
    assert ds["sessions"] == 100
    assert ds["p10"] <= ds["p50"] <= ds["p90"]


def test_house_edge_negative():
    he = analyze.theoretical_house_edge(spot=4, wager=1, bonus=False)
    assert he < 0


def parse_fixture():
    from scraper import parse_draws_html
    return parse_draws_html(FIX.read_text())
