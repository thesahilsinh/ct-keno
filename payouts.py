"""Official CT Keno paytable, parsed from the How-To-Play page.

The paytable is published as static <h2>N-Spot Game</h2> tables on
https://www.ctlottery.org/KENO . We parse it at runtime (not hardcode) so a
layout tweak is a one-line fix. PAYOUTS[spot][match] = prize in dollars.
"""
import re
from html import unescape

BONUS_MULTIPLIERS = [1, 2, 3, 4, 5, 10]  # No Bonus=1, then 2X..10X
GAME_ID = 23
PAYTABLE_URL = "https://www.ctlottery.org/KENO"

# Populated at runtime by load_payouts(); simulate.py reads this.
PAYOUTS: dict = {}


def _money(s: str) -> float:
    return float(s.replace("$", "").replace(",", "").strip())


def parse_payouts(html: str) -> dict:
    """Return {spot: {match: prize}}. Spots 1..10."""
    data = {s: {} for s in range(1, 11)}
    for spot_s, block in re.findall(r"<h2>(\d+)-Spot Game</h2>(.*?)</table>", html, re.S):
        spot = int(spot_s)
        for match_s, prize_s, _odds in re.findall(
            r"<td[^>]*>(\d+)</td><td[^>]*>([^<]+)</td><td[^>]*>([^<]+)</td>", block
        ):
            data[spot][int(match_s)] = _money(prize_s)
    return data


def load_payouts(html: str) -> dict:
    """Parse and cache into module-level PAYOUTS; returns it.

    Mutates the existing dict in place (so `from payouts import PAYOUTS`
    elsewhere keeps pointing at the live object).
    """
    global PAYOUTS
    data = parse_payouts(html)
    PAYOUTS.clear()
    PAYOUTS.update(data)
    return PAYOUTS


def bonus_to_mult(bonus: str) -> int:
    return {"No Bonus": 1, "2X": 2, "3X": 3, "4X": 4, "5X": 5, "10X": 10}.get(bonus, 1)
