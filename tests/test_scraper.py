from pathlib import Path
import scraper

FIX = Path(__file__).parent / "fixtures" / "keno_one_day.html"


def test_parse_count_and_fields():
    rows = scraper.parse_draws_html(FIX.read_text())
    assert len(rows) >= 200                       # ~221-236 draws/day
    r = rows[0]
    assert isinstance(r["game_no"], int) and r["game_no"] > 0
    assert len(r["numbers"]) == 20
    assert all(1 <= n <= 80 for n in r["numbers"])
    assert r["bonus"] in {"No Bonus", "2X", "3X", "4X", "5X", "10X"}
    assert r["numbers"] == sorted(r["numbers"])


def test_game_no_monotonic_desc():
    rows = scraper.parse_draws_html(FIX.read_text())
    gnos = [r["game_no"] for r in rows]
    assert gnos == sorted(gnos, reverse=True)


def test_numbers_split_across_both_halves():
    # first row in fixture is game 1182153 with two <br>-split halves
    rows = scraper.parse_draws_html(FIX.read_text())
    assert all(len(set(r["numbers"])) == 20 for r in rows[:5])  # no dup within a draw
