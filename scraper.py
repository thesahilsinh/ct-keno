"""Scrape real CT Keno draws from ctlottery.org.

Endpoint (verified): /ajax/getWinningNumbers?g=23&s=MM/DD/YYYY&e=MM/DD/YYYY
One request = one calendar day (~221-236 draws). Returns an HTML <table>.

Row shape (server-rendered):
  <tr><td>GAME#</td>
      <td>N1 - ... - N10<br/>N11 - ... - N20</td>
      <td>BONUS</td>
      <td><a href="...Watch...">Watch</a></td></tr>

Fields: game_no (int, newest-first), numbers (20 ints in 1..80),
bonus in {No Bonus,2X,3X,4X,5X,10X}. No timestamp is exposed by the site.
"""
import re
import time
import urllib.request
from datetime import date, timedelta
from html import unescape

GAME_ID = 23
ENDPOINT = "https://www.ctlottery.org/ajax/getWinningNumbers?g={g}&s={s}&e={e}"
REFERER = "https://www.ctlottery.org/WinningNumbers/KENO"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")
ROW_RE = re.compile(
    r"<tr>\s*<td>(\d+)</td>\s*<td>(.*?)</td>\s*<td>([^<]*)</td>", re.S)
NUM_RE = re.compile(r"\b(\d{1,2})\b")
BONUS_VALUES = {"No Bonus", "2X", "3X", "4X", "5X", "10X"}


def parse_draws_html(html: str) -> list:
    """Parse the endpoint HTML into draw records. Pure function (no network)."""
    rows = []
    for m in ROW_RE.finditer(html):
        game_no = int(m.group(1))
        halves = re.split(r"<br\s*/?>", unescape(m.group(2)), flags=re.I)
        nums = [int(x) for h in halves for x in NUM_RE.findall(h)]
        if len(nums) != 20:
            continue
        bonus = m.group(3).strip() or "No Bonus"
        if bonus not in BONUS_VALUES:
            bonus = "No Bonus"
        rows.append({"game_no": game_no, "numbers": sorted(nums), "bonus": bonus})
    return rows


def fetch_day(d_mmddyyyy: str, timeout: int = 30, draw_date: str = None) -> list:
    """Fetch ONE day of draws over the network. Returns parsed records.

    `draw_date` (ISO YYYY-MM-DD) is stamped onto each row so the store can later
    show per-day trends. The endpoint returns exactly one calendar day's draws,
    so the requested date *is* the draw date.
    """
    url = ENDPOINT.format(g=GAME_ID, s=d_mmddyyyy, e=d_mmddyyyy)
    req = urllib.request.Request(url, headers={
        "User-Agent": UA,
        "Accept": "text/html, */*; q=0.01",
        "Referer": REFERER,
        "X-Requested-With": "XMLHttpRequest",
    })
    with urllib.request.urlopen(req, timeout=timeout) as r:
        body = r.read().decode("utf-8", "ignore")
    if body.lstrip()[:1] not in ("<",):
        # Silent-200 trap: Sitefinity served the home page, not data.
        raise RuntimeError("endpoint returned non-HTML (likely WAF/access block)")
    rows = parse_draws_html(body)
    if draw_date:
        for r in rows:
            r["draw_date"] = draw_date
    return rows


def daterange(start: date, end: date):
    """Yield each date from start..end inclusive."""
    d = start
    while d <= end:
        yield d
        d += timedelta(days=1)


def scrape_range(start: date, end: date, store_path, delay: float = 1.0):
    """Fetch every day in [start,end], append to the store (deduped). Returns count added."""
    from store import append_draws
    added = 0
    for d in daterange(start, end):
        ds = d.strftime("%m/%d/%Y")
        try:
            rows = fetch_day(ds, draw_date=d.isoformat())
        except Exception as e:
            print(f"  ! {ds} failed: {e}")
            continue
        before = len(load_len(store_path))
        append_draws(rows, store_path)
        after = len(load_len(store_path))
        added += after - before
        print(f"  + {ds}: {len(rows)} draws (store now {after})")
        time.sleep(delay)
    return added


def load_len(store_path) -> list:
    from store import load_draws
    return load_draws(store_path)
