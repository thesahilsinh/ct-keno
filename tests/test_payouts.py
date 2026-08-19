from pathlib import Path
import payouts

FIX = Path(__file__).parent / "fixtures" / "keno_htp.html"


def test_parses_all_spots():
    p = payouts.parse_payouts(FIX.read_text())
    assert set(p) == set(range(1, 11))            # spots 1..10
    assert p[1][1] == 2.5
    assert p[4][4] == 100
    assert p[10][10] == 100000
    assert p[9][0] == 2                           # 9-spot catch-0 pays $2


def test_bonus_values():
    assert set(payouts.BONUS_MULTIPLIERS) == {1, 2, 3, 4, 5, 10}


def test_bonus_to_mult():
    assert payouts.bonus_to_mult("No Bonus") == 1
    assert payouts.bonus_to_mult("10X") == 10
    assert payouts.bonus_to_mult("bogus") == 1
