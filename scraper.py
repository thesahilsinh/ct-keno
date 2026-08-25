"""Scrape real CT Keno draws from ctlottery.org (new JSON API).

The old site (ctlottery.org/ajax/getWinningNumbers) is gone. The new site is a
JS SPA backed by a JSON API:

    GET https://www.ctilottery.org/api/v1/draw-games/draws/page
        ?order=DESC&game-names=Keno&status=CLOSED
        &date-from=<epoch_ms>&date-to=<epoch_ms>&size=500&page=0

Response shape (JSON):
    {
      "draws": [
        {
          "id": "1185655",            # game number (string)
          "drawTime": 1787628240000,  # epoch millis (real timestamp!)
          "status": "CLOSED",
          "results": [{
            "primary": ["68","51",...,"46","M-03"],  # 20 numbers + multiplier marker
            "multiplier": 3
          }]
        }, ...
      ],
      "nextItems": 0, "previousItems": 0,
      "pageUrls": {...}, "nextPageUrl": "...", "previousPageUrl": "..."
    }

Multiplier -> bonus mapping: 1=No Bonus, 2=2X, 3=3X, 4=4X, 5=5X, 10=10X.
The `primary` array holds 20 numbers plus a trailing "M-XX" multiplier marker.
"""
import json
import re
import time
import urllib.request
from datetime import date, datetime, timedelta
from html import unescape
from zoneinfo import ZoneInfo

ENDPOINT = "https://www.ctilottery.org/api/v1/draw-games/draws/page"
REFERER = "https://www.ctilottery.org/en-us/winning-numbers/keno.html"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")
TZ = ZoneInfo("America/New_York")

# multiplier int -> bonus label (matches the historical store's `bonus` field)
MULT_TO_BONUS = {1: "No Bonus", 2: "2X", 3: "3X", 4: "4X", 5: "5X", 10: "10X"}


def _day_bounds_epoch_ms(d: date) -> tuple:
    """Return (start_of_day, end_of_day) epoch millis for `d` in Eastern time."""
    start = datetime(d.year, d.month, d.day, 0, 0, 0, tzinfo=TZ)
    end = start + timedelta(days=1)
    return int(start.timestamp() * 1000), int(end.timestamp() * 1000)


def parse_draws_json(payload: dict) -> list:
    """Parse the JSON API response into draw records. Pure function (no network)."""
    rows = []
    for dr in payload.get("draws", []):
        try:
            game_no = int(dr["id"])
        except (KeyError, ValueError, TypeError):
            continue
        results = dr.get("results") or []
        if not results:
            continue
        primary = results[0].get("primary") or []
        # 20 numbers + a trailing "M-XX" multiplier marker
        nums = []
        for x in primary:
            if isinstance(x, str) and x.startswith("M-"):
                continue
            try:
                nums.append(int(x))
            except (ValueError, TypeError):
                continue
        if len(nums) != 20:
            continue
        mult = results[0].get("multiplier", 1)
        bonus = MULT_TO_BONUS.get(mult, "No Bonus")
        row = {"game_no": game_no, "numbers": sorted(nums), "bonus": bonus}
        # real timestamp if present (epoch millis -> ISO, Eastern)
        dt = dr.get("drawTime")
        if dt:
            row["draw_time"] = datetime.fromtimestamp(dt / 1000, tz=TZ).isoformat()
        rows.append(row)
    return rows


# ---- legacy HTML parser (old ctlottery.org endpoint, kept for tests) ----
ROW_RE = re.compile(
    r"<tr>\s*<td>(\d+)</td>\s*<td>(.*?)</td>\s*<td>([^<]*)</td>", re.S)
NUM_RE = re.compile(r"\b(\d{1,2})\b")
BONUS_VALUES = {"No Bonus", "2X", "3X", "4X", "5X", "10X"}


def parse_draws_html(html: str) -> list:
    """Parse the OLD endpoint HTML into draw records (legacy, for tests)."""
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


def _fetch_page(params: dict, timeout: int = 30) -> dict:
    """GET one page of the draws API and return the parsed JSON dict."""
    from urllib.parse import urlencode
    url = ENDPOINT + "?" + urlencode(params)
    req = urllib.request.Request(url, headers={
        "User-Agent": UA,
        "Accept": "application/json",
        "Referer": REFERER,
        "X-User-Agent": "portal",
    })
    with urllib.request.urlopen(req, timeout=timeout) as r:
        body = r.read().decode("utf-8", "ignore")
    return json.loads(body)


def fetch_day(d_mmddyyyy: str, timeout: int = 30, draw_date: str = None) -> list:
    """Fetch ONE day of draws over the network. Returns parsed records.

    `draw_date` (ISO YYYY-MM-DD) is stamped onto each row so the store can later
    show per-day trends. The API returns exactly one calendar day's draws for the
    requested date range, so the requested date *is* the draw date.
    """
    d = datetime.strptime(d_mmddyyyy, "%m/%d/%Y").date()
    start_ms, end_ms = _day_bounds_epoch_ms(d)

    params = {
        "order": "DESC",
        "game-names": "Keno",
        "status": "CLOSED",
        "date-from": start_ms,
        "date-to": end_ms,
        "size": 500,
        "page": 0,
    }
    payload = _fetch_page(params, timeout=timeout)
    rows = parse_draws_json(payload)

    # paginate if the day has more than `size` draws (unlikely, but be safe)
    page = 1
    while payload.get("nextItems", 0) > 0 and page < 20:
        params["page"] = page
        payload = _fetch_page(params, timeout=timeout)
        rows.extend(parse_draws_json(payload))
        page += 1

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
    from store import append_draws, load_draws
    added = 0
    for d in daterange(start, end):
        ds = d.strftime("%m/%d/%Y")
        try:
            rows = fetch_day(ds, draw_date=d.isoformat())
        except Exception as e:
            print(f"  ! {ds} failed: {e}")
            continue
        before = len(load_draws(store_path))
        append_draws(rows, store_path)
        after = len(load_draws(store_path))
        added += after - before
        print(f"  + {ds}: {len(rows)} draws (store now {after})")
        time.sleep(delay)
    return added
