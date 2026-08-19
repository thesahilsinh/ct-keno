import tempfile
from pathlib import Path

from scraper import parse_draws_html
import store

FIX = Path(__file__).parent / "fixtures" / "keno_one_day.html"


def test_dedup_roundtrip():
    rows = parse_draws_html(FIX.read_text())
    p = Path(tempfile.mktemp(suffix=".csv"))
    store.append_draws(rows, p)
    n1 = len(store.load_draws(p))
    store.append_draws(rows, p)                    # re-run, should add nothing
    n2 = len(store.load_draws(p))
    assert n1 == n2 == len(rows)
    back = store.load_draws(p)
    assert back[0]["numbers"] == rows[0]["numbers"]
    p.unlink()
