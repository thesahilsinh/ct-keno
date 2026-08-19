#!/usr/bin/env python3
"""CT Keno — real-time dashboard (single file, self-contained).

Run:
    python server.py                 # http://localhost:8000
    python server.py --interval 30   # scrape every 30s (default 60s)

What it does:
  * Serves an inline HTML dashboard (no build step, no static file needed).
  * Background thread scrapes TODAY + YESTERDAY from ctlottery.org every
    --interval seconds, appending new draws to data/draws.csv (dedup-safe).
  * /api/state returns live stats; the browser polls it every 2s, so new
    draws flash in as they land.

The dashboard shows, live:
  * total draws + newest game # + last scrape time (ticking)
  * latest draws feed (newest first, NEW rows flash)
  * number frequency heat-board (1-80, updates as draws arrive)
  * hot / cold / overdue lists
  * draws-per-day bar chart (last 14 days)
"""
import argparse
import json
import threading
import time
from collections import Counter
from datetime import date, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import scraper
import store
import analysis_web

ROOT = Path(__file__).resolve().parent
STORE = ROOT / "data" / "draws.csv"
HOST = "0.0.0.0"
PORT = 8000

STATE_LOCK = threading.Lock()
LAST_SCRAPE = "never"
STATE = {"total_draws": 0, "draws": [], "freq": {}, "last_seen": {},
         "bonus": {}, "day_trend": [], "game_min": 0, "game_max": 0,
         "expected_per_num": 0}
ANALYSIS = None
PREDICTION = None
TODAY_TREND = None
TODAY_ANALYSIS = None


def compute_state():
    """Recompute live stats from the store. Returns the state dict."""
    draws = store.load_draws(STORE)
    if not draws:
        return None
    # newest first
    draws = sorted(draws, key=lambda d: d["game_no"], reverse=True)
    total = len(draws)
    expected = round(total * 20 / 80, 1)

    freq = Counter()
    last_seen = {}
    for idx, d in enumerate(draws):
        for n in d["numbers"]:
            freq[n] += 1
            last_seen[n] = idx

    bonus = Counter(d["bonus"] for d in draws)
    gnos = [d["game_no"] for d in draws]

    # per-day counts (normalize both ISO and MM/DD/YYYY formats)
    per_day = Counter()
    for d in draws:
        nd = analysis_web._norm_date(d.get("draw_date"))
        if nd:
            per_day[nd] += 1
    day_trend = [{"date": k, "count": per_day[k]} for k in sorted(per_day)]

    return {
        "total_draws": total,
        "game_min": min(gnos),
        "game_max": max(gnos),
        "expected_per_num": expected,
        "freq": {str(n): freq.get(n, 0) for n in range(1, 81)},
        "last_seen": {str(n): last_seen.get(n, total) for n in range(1, 81)},
        "bonus": dict(bonus),
        "day_trend": day_trend,
        # full draw set for the live feed (newest first)
        "draws": [{"game_no": d["game_no"], "bonus": d["bonus"],
                   "numbers": d["numbers"]} for d in draws],
    }


def refresh_state(force_rebuild=True, recompute_prediction=False):
    global STATE, ANALYSIS, PREDICTION, TODAY_TREND, TODAY_ANALYSIS
    data = compute_state()
    if data:
        with STATE_LOCK:
            STATE = data
        # compute the rich analysis from the full draw set
        try:
            ANALYSIS = analysis_web.compute_analysis(
                store.load_draws(STORE), recent_window=50)
        except Exception as e:
            print(f"  [analysis] error: {e}")
        # today's trend (cheap, recompute every refresh)
        try:
            TODAY_TREND = analysis_web.compute_today_trend(
                store.load_draws(STORE), baseline_days=90)
        except Exception as e:
            print(f"  [today-trend] error: {e}")
        # today-only analysis (same shape as /api/analysis, but today's draws)
        try:
            TODAY_ANALYSIS = analysis_web.compute_today_analysis(
                store.load_draws(STORE))
        except Exception as e:
            print(f"  [today-analysis] error: {e}")
        # pair prediction is expensive (~2.7s over full history); only recompute
        # when new draws actually landed, or on first load.
        if recompute_prediction or PREDICTION is None:
            try:
                PREDICTION = analysis_web.compute_pair_prediction(
                    store.load_draws(STORE), recent_window=50)
            except Exception as e:
                print(f"  [prediction] error: {e}")
    return data


def scrape_once():
    today = date.today()
    added = 0
    for d in (today, today - timedelta(days=1)):
        try:
            rows = scraper.fetch_day(d.strftime("%m/%d/%Y"), draw_date=d.isoformat())
            added += store.append_draws(rows, STORE)
        except Exception as e:
            print(f"  [scrape] {d.isoformat()} failed: {e}")
    return added


def scraper_loop(interval):
    global LAST_SCRAPE
    while True:
        try:
            added = scrape_once()
            LAST_SCRAPE = time.strftime("%Y-%m-%d %H:%M:%S")
            data = refresh_state(recompute_prediction=(added > 0))
            tot = data["total_draws"] if data else "?"
            print(f"  [scrape] {LAST_SCRAPE} +{added} new -- store total {tot}")
        except Exception as e:
            print(f"  [scrape] error: {e}")
        time.sleep(interval)


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, body, ctype):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.split("?")[0] == "/api/state":
            with STATE_LOCK:
                data = dict(STATE)
            data["server_time"] = time.strftime("%Y-%m-%d %H:%M:%S")
            data["last_scrape"] = LAST_SCRAPE
            self._send(200, json.dumps(data).encode(), "application/json")
            return
        if self.path.split("?")[0] == "/api/analysis":
            with STATE_LOCK:
                a = ANALYSIS
            if a is None:
                self._send(200, b"{}", "application/json")
            else:
                self._send(200, json.dumps(a).encode(), "application/json")
            return
        if self.path.split("?")[0] == "/api/prediction":
            with STATE_LOCK:
                p = PREDICTION
            if p is None:
                self._send(200, b"{}", "application/json")
            else:
                self._send(200, json.dumps(p).encode(), "application/json")
            return
        if self.path.split("?")[0] == "/api/today":
            with STATE_LOCK:
                t = TODAY_TREND
            if t is None:
                self._send(200, b"{}", "application/json")
            else:
                self._send(200, json.dumps(t).encode(), "application/json")
            return
        if self.path.split("?")[0] == "/api/today-analysis":
            with STATE_LOCK:
                ta = TODAY_ANALYSIS
            if ta is None:
                self._send(200, b"{}", "application/json")
            else:
                self._send(200, json.dumps(ta).encode(), "application/json")
            return
        if self.path.split("?")[0] == "/today":
            self._send(200, TODAY_HTML.encode(), "text/html; charset=utf-8")
            return
        self._send(200, HTML.encode(), "text/html; charset=utf-8")

    def log_message(self, *args):
        pass


HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>CT Keno — Live</title>
<style>
:root{--bg:#0f1419;--panel:#1a212b;--panel2:#222c39;--ink:#e8eef5;--muted:#8aa0b5;
--line:#2c3848;--hot:#ff5a4d;--cold:#3a8dde;--accent:#ffce54}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 -apple-system,Segoe UI,Roboto,Arial,sans-serif}
header{padding:18px 24px;border-bottom:1px solid var(--line);display:flex;align-items:center;gap:14px;flex-wrap:wrap}
h1{margin:0;font-size:20px}
.status{display:flex;align-items:center;gap:8px;font-size:13px;color:var(--muted);margin-left:auto}
.dot{width:10px;height:10px;border-radius:50%;background:#555}
.dot.on{background:#46c878;animation:pulse 1.8s infinite}
.dot.err{background:#ff5a4d}
@keyframes pulse{0%{box-shadow:0 0 0 0 rgba(70,200,120,.5)}70%{box-shadow:0 0 0 8px rgba(70,200,120,0)}100%{box-shadow:0 0 0 0 rgba(70,200,120,0)}}
.wrap{max-width:1100px;margin:0 auto;padding:20px 24px 60px}
.cards{display:flex;flex-wrap:wrap;gap:12px;margin:16px 0 22px}
.card{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:13px 18px;min-width:150px}
.card .k{color:var(--muted);font-size:11px;text-transform:uppercase;letter-spacing:.6px}
.card .v{font-size:24px;font-weight:700;margin-top:3px}
section{background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:18px 20px;margin-bottom:20px}
section h2{margin:0 0 14px;font-size:16px}
.board{display:grid;grid-template-columns:repeat(10,1fr);gap:5px}
.cell{aspect-ratio:1/1;border-radius:8px;display:flex;flex-direction:column;align-items:center;justify-content:center;font-weight:700;border:1px solid rgba(255,255,255,.06)}
.cell .num{font-size:14px}.cell .cnt{font-size:10px;opacity:.85}
.legend{display:flex;align-items:center;gap:10px;margin-top:12px;color:var(--muted);font-size:12px}
.legend .bar{height:12px;width:200px;border-radius:6px;background:linear-gradient(90deg,#1c3a5e,#2f6f4f,#caa53a,#e8732f,#ff4d3d)}
.lists{display:flex;gap:16px;flex-wrap:wrap}
.list{flex:1;min-width:220px}
.list h3{margin:0 0 8px;font-size:13px;color:var(--muted)}
.chips{display:flex;flex-wrap:wrap;gap:5px}
.chip{background:var(--panel2);border:1px solid var(--line);border-radius:16px;padding:4px 10px;font-size:12px}
.chip b{color:var(--accent)}
.toolbar{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:12px}
.toolbar button{background:var(--panel2);color:var(--ink);border:1px solid var(--line);border-radius:8px;padding:6px 12px;font-size:12px;cursor:pointer}
.toolbar button.on{background:var(--accent);color:#1a1300;border-color:var(--accent);font-weight:700}
.cell{cursor:pointer}
.feed{display:flex;flex-direction:column;gap:5px;max-height:420px;overflow:auto}
.frow{display:flex;gap:10px;align-items:center;background:var(--panel2);border:1px solid var(--line);border-radius:8px;padding:6px 10px}
.frow .gno{color:var(--muted);font-size:12px;min-width:70px}
.frow .ns{display:flex;flex-wrap:wrap;gap:3px}
.frow .n{width:20px;height:20px;border-radius:50%;display:flex;align-items:center;justify-content:center;font-size:10px;background:#2a3645;border:1px solid var(--line)}
.frow .b{margin-left:auto;padding:2px 7px;border-radius:5px;font-size:10px;font-weight:700;background:#2a3645}
.b.x10{background:#ff4d3d;color:#fff}.b.x5{background:#ff8a3d}.b.x4{background:#e8b53a}
.b.x3{background:#9bd24a}.b.x2{background:#4aa3d2}.b.nb{background:#3a4654}
.flash{animation:fl .9s ease-out}
@keyframes fl{0%{background:#364a2f}100%{background:var(--panel2)}}
canvas{width:100%;height:220px;display:block}
.note{color:var(--muted);font-size:12px;margin-top:8px}
</style>
</head>
<body>
<header>
  <h1>CT Keno — Live Draws</h1>
  <div class="status"><span class="dot" id="dot"></span><span id="status">connecting…</span></div>
  <a class="nav" href="/today" style="color:var(--accent);text-decoration:none;font-size:13px">today's analysis →</a>
</header>
<div class="wrap">
  <div class="cards" id="cards"></div>

  <section>
    <h2>Latest Draws <span style="color:var(--muted);font-weight:400;font-size:13px">(newest first, live)</span></h2>
    <div class="feed" id="feed"></div>
  </section>

  <section>
    <h2>Number Frequency Board</h2>
    <div class="board" id="board"></div>
    <div class="legend"><span>cold</span><div class="bar"></div><span>hot</span></div>
    <div class="note">Each number drawn ~<b id="exp"></b> times (uniform expectation). Cell = times drawn.</div>
  </section>

  <section>
    <h2>Hot / Cold / Overdue</h2>
    <div class="lists">
      <div class="list"><h3>Hot (most drawn)</h3><div class="chips" id="hot"></div></div>
      <div class="list"><h3>Cold (least drawn)</h3><div class="chips" id="cold"></div></div>
      <div class="list"><h3>Longest since seen</h3><div class="chips" id="overdue"></div></div>
    </div>
  </section>

  <section>
    <h2>Draws Per Day (last 14 days)</h2>
    <canvas id="dayChart"></canvas>
    <div class="note" id="dayNote"></div>
  </section>

  <section>
    <h2>Number Analysis — Decision Support</h2>
    <div class="toolbar">
      <button id="aFreq" class="on">Frequency</button>
      <button id="aDue">Overdue</button>
      <button id="aRecent">Recent (50)</button>
      <button id="aGap">Max Gap</button>
    </div>
    <div class="board" id="aboard"></div>
    <div class="legend"><span>low</span><div class="bar"></div><span>high</span>
      <span id="aleg" style="margin-left:10px"></span></div>
    <div class="note">Click a metric to re-color the board. Hover a number for its full stats.</div>
  </section>

  <section>
    <h2>Top Combinations</h2>
    <div class="lists">
      <div class="list"><h3>Top Pairs</h3><div class="chips" id="apairs"></div></div>
      <div class="list"><h3>Top Triplets</h3><div class="chips" id="atriplets"></div></div>
      <div class="list"><h3>Top Quads</h3><div class="chips" id="aquads"></div></div>
    </div>
  </section>

  <section>
    <h2>Distribution</h2>
    <div class="lists">
      <div class="list"><h3>By Range (10s)</h3><div class="chips" id="aranges"></div></div>
      <div class="list"><h3>Odd / Even</h3><div class="chips" id="aoddeven"></div></div>
      <div class="list"><h3>High / Low</h3><div class="chips" id="ahighlow"></div></div>
    </div>
    <div class="note" id="asum"></div>
  </section>

  <section>
    <h2>Pair Prediction — Top Scored</h2>
    <div class="note" id="pnote"></div>
    <div class="lists">
      <div class="list" style="flex:2;min-width:320px">
        <h3>Ranked pairs (composite score)</h3>
        <div class="chips" id="ppairs" style="flex-direction:column;align-items:stretch"></div>
      </div>
      <div class="list">
        <h3>#1 Pick</h3>
        <div id="ptop" style="font-size:28px;font-weight:700;color:var(--accent)"></div>
        <div class="note" id="ptopdetail"></div>
      </div>
    </div>
    <div class="note" id="pdisclaimer"></div>
  </section>

  <section>
    <h2>Today's Trend — vs 3-Month Baseline</h2>
    <div class="note" id="tnote"></div>
    <div class="lists">
      <div class="list">
        <h3>Today's Pick</h3>
        <div id="tpick" style="font-size:26px;font-weight:700;color:var(--accent)"></div>
        <div class="note" id="tpickpair"></div>
      </div>
      <div class="list" style="flex:2;min-width:320px">
        <h3>Hot numbers (trend z)</h3>
        <div class="chips" id="tnums" style="flex-direction:column;align-items:stretch"></div>
      </div>
      <div class="list" style="flex:2;min-width:320px">
        <h3>Hot pairs (trend z)</h3>
        <div class="chips" id="tpairs" style="flex-direction:column;align-items:stretch"></div>
      </div>
    </div>
    <div class="note" id="tdisclaimer"></div>
  </section>
</div>

<script>
let S = null;
let seenGames = new Set();
let firstLoad = true;

function heat(v, minF, maxF){
  const t = (v-minF)/Math.max(1,(maxF-minF));
  const stops=[[28,58,94],[47,111,79],[202,165,58],[232,115,47],[255,77,61]];
  const x=t*(stops.length-1), i=Math.floor(x), f=x-i;
  const a=stops[i], b=stops[Math.min(stops.length-1,i+1)];
  const c=k=>Math.round(a[k]+(b[k]-a[k])*f);
  return `rgb(${c(0)},${c(1)},${c(2)})`;
}

function render(){
  if(!S) return;
  const freq = Object.values(S.freq);
  const maxF = Math.max(...freq), minF = Math.min(...freq);

  // cards
  document.getElementById('cards').innerHTML = [
    ['Total draws', S.total_draws],
    ['Newest game #', S.game_max],
    ['Expected / number', S.expected_per_num],
    ['Last scrape', S.last_scrape || '—'],
  ].map(([k,v])=>`<div class="card"><div class="k">${k}</div><div class="v">${v}</div></div>`).join('');
  document.getElementById('exp').textContent = S.expected_per_num;

  // board
  const board = document.getElementById('board');
  board.innerHTML = Array.from({length:80},(_,i)=>i+1).map(n=>{
    const v = S.freq[n];
    return `<div class="cell" style="background:${heat(v,minF,maxF)}"><span class="num">${n}</span><span class="cnt">${v}</span></div>`;
  }).join('');

  // hot/cold/overdue
  const arr = Array.from({length:80},(_,i)=>i+1);
  const byFreq = [...arr].sort((a,b)=>S.freq[b]-S.freq[a]);
  const byOver = [...arr].sort((a,b)=>S.last_seen[b]-S.last_seen[a]);
  const chip = (n,v)=>`<span class="chip">#${n} <b>${v}</b></span>`;
  document.getElementById('hot').innerHTML = byFreq.slice(0,10).map(n=>chip(n,S.freq[n])).join('');
  document.getElementById('cold').innerHTML = byFreq.slice(-10).map(n=>chip(n,S.freq[n])).join('');
  document.getElementById('overdue').innerHTML = byOver.slice(0,10).map(n=>chip(n,S.last_seen[n]+' ago')).join('');

  // feed
  const feed = document.getElementById('feed');
  const rows = (S.draws||[]).slice(0,40);
  feed.innerHTML = rows.map(d=>{
    const isNew = !seenGames.has(d.game_no);
    seenGames.add(d.game_no);
    const ns = d.numbers.map(n=>`<span class="n">${n}</span>`).join('');
    const bcls = d.bonus==='No Bonus'?'nb':('x'+d.bonus.replace('X',''));
    return `<div class="frow ${isNew&&!firstLoad?'flash':''}"><span class="gno">#${d.game_no}</span><span class="ns">${ns}</span><span class="b ${bcls}">${d.bonus}</span></div>`;
  }).join('');

  // day chart
  drawDayChart();
  firstLoad = false;
}

function drawDayChart(){
  const c = document.getElementById('dayChart');
  const ctx = c.getContext('2d');
  const dpr = window.devicePixelRatio||1;
  const w = c.clientWidth, h = 220;
  c.width = w*dpr; c.height = h*dpr;
  ctx.setTransform(dpr,0,0,dpr,0,0);
  ctx.clearRect(0,0,w,h);
  const dt = (S.day_trend||[]).slice(-14);
  const note = document.getElementById('dayNote');
  if(!dt.length){
    ctx.fillStyle='#8aa0b5';ctx.font='13px sans-serif';ctx.textAlign='center';
    ctx.fillText('No dated draws yet', w/2, h/2);
    note.textContent='';
    return;
  }
  const max = Math.max(...dt.map(d=>d.count));
  const padL=44,padB=28,padT=12,padR=10;
  const x0=padL,x1=w-padR,y0=h-padB,y1=padT;
  const bw=(x1-x0)/dt.length;
  ctx.strokeStyle='#2c3848';ctx.fillStyle='#8aa0b5';ctx.font='11px sans-serif';ctx.textAlign='right';
  [0,max/2,max].forEach(v=>{const y=y0+(y1-y0)*(v/max);ctx.beginPath();ctx.moveTo(x0,y);ctx.lineTo(x1,y);ctx.stroke();ctx.fillText(Math.round(v),x0-8,y+3);});
  ctx.textAlign='center';
  dt.forEach((d,i)=>{const bh=(y0-y1)*(d.count/max);const x=x0+i*bw;
    ctx.fillStyle='#4aa3d2';ctx.fillRect(x+2,y0-bh,bw-4,bh);
    ctx.fillStyle='#8aa0b5';ctx.fillText(d.date.slice(5),x+bw/2,h-10);});
  note.textContent = `${dt.length} days · ${dt.reduce((a,d)=>a+d.count,0)} draws`;
}

function tick(){
  fetch('api/state',{cache:'no-store'}).then(r=>r.json()).then(s=>{
    S = s;
    document.getElementById('dot').className = 'dot on';
    document.getElementById('status').textContent = `live · ${s.total_draws} draws · last scrape ${s.last_scrape||'—'}`;
    render();
  }).catch(e=>{
    document.getElementById('dot').className = 'dot err';
    document.getElementById('status').textContent = 'offline — is server.py running?';
  });
  fetch('api/analysis',{cache:'no-store'}).then(r=>r.json()).then(a=>{
    if(a && a.numbers) renderAnalysis(a);
  }).catch(()=>{});
  fetch('api/prediction',{cache:'no-store'}).then(r=>r.json()).then(p=>{
    if(p && p.top) renderPrediction(p);
  }).catch(()=>{});
  fetch('api/today',{cache:'no-store'}).then(r=>r.json()).then(t=>{
    if(t && t.top_numbers) renderToday(t);
  }).catch(()=>{});
}

/* ---------- today's trend ---------- */
function renderToday(t){
  document.getElementById('tnote').textContent =
    `Today (${t.today}): ${t.n_today} draws · baseline: ${t.n_base} draws over ${t.baseline_days} days`;

  document.getElementById('tpick').textContent = t.pick_numbers.join(' · ');
  document.getElementById('tpickpair').textContent =
    `Top pair: ${t.pick_pair.join('-')} (z ${t.top_pairs[0].z})`;

  document.getElementById('tnums').innerHTML = t.top_numbers.map((n,i)=>{
    return `<span class="chip" style="display:flex;justify-content:space-between;gap:10px">
      <span><b>#${i+1}</b> ${n.num}</span>
      <span style="color:var(--muted)">today ${n.today}× (${n.today_pct}%) · base ${n.base_pct}% · z ${n.z}</span>
    </span>`;
  }).join('');

  document.getElementById('tpairs').innerHTML = t.top_pairs.map((p,i)=>{
    return `<span class="chip" style="display:flex;justify-content:space-between;gap:10px">
      <span><b>#${i+1}</b> ${p.pair.join('-')}</span>
      <span style="color:var(--muted)">today ${p.today}× (${p.today_pct}%) · base ${p.base_pct}% · z ${p.z}</span>
    </span>`;
  }).join('');

  document.getElementById('tdisclaimer').textContent =
    '⚠ Today\'s sample is small — a high z-score on a few dozen draws is normal random noise. Every number stays ~25% and every pair ~6% long-term; today\'s trend describes the past, not the next draw.';
}

/* ---------- pair prediction ---------- */
function renderPrediction(p){
  document.getElementById('pnote').textContent =
    `Over ${p.total} draws · theoretical P(any pair) = ${p.p_pair_pct}% · expected ${p.expected} appearances/pair`;

  const top = p.top[0];
  document.getElementById('ptop').textContent = top.pair.join(' - ');
  document.getElementById('ptopdetail').textContent =
    `score ${top.score} · ${top.count}× (expected ${top.expected}) · z ${top.z_freq} · last ${top.last} ago`;

  document.getElementById('ppairs').innerHTML = p.top.map((t,i)=>{
    return `<span class="chip" style="display:flex;justify-content:space-between;gap:10px">
      <span><b>#${i+1}</b> ${t.pair.join('-')}</span>
      <span style="color:var(--muted)">${t.count}× · z ${t.z_freq} · last ${t.last} ago</span>
    </span>`;
  }).join('');

  document.getElementById('pdisclaimer').textContent =
    '⚠ Every pair has the same 6.01% chance each draw — these scores rank past noise, not future odds. Keno is independent per draw; no formula beats the house edge.';
}

/* ---------- analysis ---------- */
let A = null;
let aMetric = 'freq';
const aMetrics = {aFreq:'freq', aDue:'due', aRecent:'recent', aGap:'max_gap'};

function renderAnalysis(a){
  A = a;
  const nums = a.numbers;
  // metric values for coloring
  const vals = Object.keys(nums).map(n=>nums[n][aMetric]);
  const maxV = Math.max(...vals), minV = Math.min(...vals);
  const board = document.getElementById('aboard');
  board.innerHTML = Object.keys(nums).map(n=>{
    const st = nums[n];
    const v = st[aMetric];
    const t = (v-minV)/Math.max(1,(maxV-minV));
    const col = heat(t*100, 0, 100);
    const label = aMetric==='freq' ? st.freq : aMetric==='due' ? st.due : aMetric==='recent' ? st.recent : st.max_gap;
    return `<div class="cell" data-n="${n}" style="background:${col}" title="freq ${st.freq} · last ${st.last_seen} · max gap ${st.max_gap} · due ${st.due} · recent ${st.recent}"><span class="num">${n}</span><span class="cnt">${label}</span></div>`;
  }).join('');
  document.getElementById('aleg').textContent = `metric: ${aMetric}`;

  // combinations
  const comboChip = c => `<span class="chip">${c.combo.join('-')} <b>${c.count}×</b> <span style="color:var(--muted)">gap ${c.max_gap} · last ${c.last} ago</span></span>`;
  document.getElementById('apairs').innerHTML = a.pairs.map(comboChip).join('');
  document.getElementById('atriplets').innerHTML = a.triplets.map(comboChip).join('');
  document.getElementById('aquads').innerHTML = a.quads.map(comboChip).join('');

  // distributions
  const rng = a.ranges;
  const rngTotal = Object.values(rng).reduce((x,y)=>x+y,0);
  document.getElementById('aranges').innerHTML = Object.keys(rng).map(k=>{
    const pct = (rng[k]/rngTotal*100).toFixed(1);
    return `<span class="chip">${k} <b>${rng[k]}</b> <span style="color:var(--muted)">${pct}%</span></span>`;
  }).join('');
  const oe = a.odd_even, oeTotal = oe.odd+oe.even;
  document.getElementById('aoddeven').innerHTML =
    `<span class="chip">Odd <b>${oe.odd}</b> <span style="color:var(--muted)">${(oe.odd/oeTotal*100).toFixed(1)}%</span></span>` +
    `<span class="chip">Even <b>${oe.even}</b> <span style="color:var(--muted)">${(oe.even/oeTotal*100).toFixed(1)}%</span></span>`;
  const hl = a.high_low, hlTotal = hl.high+hl.low;
  document.getElementById('ahighlow').innerHTML =
    `<span class="chip">High (41-80) <b>${hl.high}</b> <span style="color:var(--muted)">${(hl.high/hlTotal*100).toFixed(1)}%</span></span>` +
    `<span class="chip">Low (1-40) <b>${hl.low}</b> <span style="color:var(--muted)">${(hl.low/hlTotal*100).toFixed(1)}%</span></span>`;
  document.getElementById('asum').textContent =
    `Draw sum: min ${a.sum_min} · avg ${a.sum_avg} · max ${a.sum_max} (over ${a.total} draws)`;
}

// wire metric buttons
['aFreq','aDue','aRecent','aGap'].forEach(id=>{
  document.getElementById(id).onclick = ()=>{
    aMetric = aMetrics[id];
    ['aFreq','aDue','aRecent','aGap'].forEach(x=>document.getElementById(x).classList.toggle('on',x===id));
    if(A) renderAnalysis(A);
  };
});

tick();
setInterval(tick, 2000);
</script>
</body>
</html>
"""


TODAY_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>CT Keno — Today's Analysis</title>
<style>
:root{--bg:#0f1419;--panel:#1a212b;--panel2:#222c39;--ink:#e8eef5;--muted:#8aa0b5;
--line:#2c3848;--hot:#ff5a4d;--cold:#3a8dde;--accent:#ffce54}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 -apple-system,Segoe UI,Roboto,Arial,sans-serif}
header{padding:18px 24px;border-bottom:1px solid var(--line);display:flex;align-items:center;gap:14px;flex-wrap:wrap}
h1{margin:0;font-size:20px}
a.nav{color:var(--accent);text-decoration:none;font-size:13px;margin-left:auto}
.status{display:flex;align-items:center;gap:8px;font-size:13px;color:var(--muted)}
.dot{width:10px;height:10px;border-radius:50%;background:#555}
.dot.on{background:#46c878;animation:pulse 1.8s infinite}
.dot.err{background:#ff5a4d}
@keyframes pulse{0%{box-shadow:0 0 0 0 rgba(70,200,120,.5)}70%{box-shadow:0 0 0 8px rgba(70,200,120,0)}100%{box-shadow:0 0 0 0 rgba(70,200,120,0)}}
.wrap{max-width:1100px;margin:0 auto;padding:20px 24px 60px}
.cards{display:flex;flex-wrap:wrap;gap:12px;margin:16px 0 22px}
.card{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:13px 18px;min-width:150px}
.card .k{color:var(--muted);font-size:11px;text-transform:uppercase;letter-spacing:.6px}
.card .v{font-size:24px;font-weight:700;margin-top:3px}
section{background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:18px 20px;margin-bottom:20px}
section h2{margin:0 0 14px;font-size:16px}
.board{display:grid;grid-template-columns:repeat(10,1fr);gap:5px}
.cell{aspect-ratio:1/1;border-radius:8px;display:flex;flex-direction:column;align-items:center;justify-content:center;font-weight:700;border:1px solid rgba(255,255,255,.06);cursor:pointer}
.cell .num{font-size:14px}.cell .cnt{font-size:10px;opacity:.85}
.legend{display:flex;align-items:center;gap:10px;margin-top:12px;color:var(--muted);font-size:12px}
.legend .bar{height:12px;width:200px;border-radius:6px;background:linear-gradient(90deg,#1c3a5e,#2f6f4f,#caa53a,#e8732f,#ff4d3d)}
.lists{display:flex;gap:16px;flex-wrap:wrap}
.list{flex:1;min-width:220px}
.list h3{margin:0 0 8px;font-size:13px;color:var(--muted)}
.chips{display:flex;flex-wrap:wrap;gap:5px}
.chip{background:var(--panel2);border:1px solid var(--line);border-radius:16px;padding:4px 10px;font-size:12px}
.chip b{color:var(--accent)}
.toolbar{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:12px}
.toolbar button{background:var(--panel2);color:var(--ink);border:1px solid var(--line);border-radius:8px;padding:6px 12px;font-size:12px;cursor:pointer}
.toolbar button.on{background:var(--accent);color:#1a1300;border-color:var(--accent);font-weight:700}
.note{color:var(--muted);font-size:12px;margin-top:8px}
</style>
</head>
<body>
<header>
  <h1>CT Keno — Today's Analysis</h1>
  <div class="status"><span class="dot" id="dot"></span><span id="status">connecting…</span></div>
  <a class="nav" href="/">← full dashboard</a>
</header>
<div class="wrap">
  <div class="cards" id="cards"></div>

  <section>
    <h2>Number Analysis — Decision Support (today)</h2>
    <div class="toolbar">
      <button id="aFreq" class="on">Frequency</button>
      <button id="aDue">Overdue</button>
      <button id="aRecent">Recent</button>
      <button id="aGap">Max Gap</button>
    </div>
    <div class="board" id="aboard"></div>
    <div class="legend"><span>low</span><div class="bar"></div><span>high</span>
      <span id="aleg" style="margin-left:10px"></span></div>
    <div class="note">Click a metric to re-color the board. Hover a number for its full stats.</div>
  </section>

  <section>
    <h2>Top Combinations (today)</h2>
    <div class="lists">
      <div class="list"><h3>Top Pairs</h3><div class="chips" id="apairs"></div></div>
      <div class="list"><h3>Top Triplets</h3><div class="chips" id="atriplets"></div></div>
      <div class="list"><h3>Top Quads</h3><div class="chips" id="aquads"></div></div>
    </div>
  </section>

  <section>
    <h2>Distribution (today)</h2>
    <div class="lists">
      <div class="list"><h3>By Range (10s)</h3><div class="chips" id="aranges"></div></div>
      <div class="list"><h3>Odd / Even</h3><div class="chips" id="aoddeven"></div></div>
      <div class="list"><h3>High / Low</h3><div class="chips" id="ahighlow"></div></div>
    </div>
    <div class="note" id="asum"></div>
  </section>

  <section>
    <h2>Today's Trend — vs 3-Month Baseline</h2>
    <div class="note" id="tnote"></div>
    <div class="lists">
      <div class="list">
        <h3>Today's Pick</h3>
        <div id="tpick" style="font-size:26px;font-weight:700;color:var(--accent)"></div>
        <div class="note" id="tpickpair"></div>
      </div>
      <div class="list" style="flex:2;min-width:320px">
        <h3>Hot numbers (trend z)</h3>
        <div class="chips" id="tnums" style="flex-direction:column;align-items:stretch"></div>
      </div>
      <div class="list" style="flex:2;min-width:320px">
        <h3>Hot pairs (trend z)</h3>
        <div class="chips" id="tpairs" style="flex-direction:column;align-items:stretch"></div>
      </div>
    </div>
    <div class="note" id="tdisclaimer"></div>
  </section>
</div>

<script>
let A = null;
let aMetric = 'freq';
const aMetrics = {aFreq:'freq', aDue:'due', aRecent:'recent', aGap:'max_gap'};

function heat(v, minF, maxF){
  const t = (v-minF)/Math.max(1,(maxF-minF));
  const stops=[[28,58,94],[47,111,79],[202,165,58],[232,115,47],[255,77,61]];
  const x=t*(stops.length-1), i=Math.floor(x), f=x-i;
  const a=stops[i], b=stops[Math.min(stops.length-1,i+1)];
  const c=k=>Math.round(a[k]+(b[k]-a[k])*f);
  return `rgb(${c(0)},${c(1)},${c(2)})`;
}

function renderAnalysis(a){
  A = a;
  const nums = a.numbers;
  const vals = Object.keys(nums).map(n=>nums[n][aMetric]);
  const maxV = Math.max(...vals), minV = Math.min(...vals);
  const board = document.getElementById('aboard');
  board.innerHTML = Object.keys(nums).map(n=>{
    const st = nums[n];
    const v = st[aMetric];
    const t = (v-minV)/Math.max(1,(maxV-minV));
    const col = heat(t*100, 0, 100);
    const label = aMetric==='freq' ? st.freq : aMetric==='due' ? st.due : aMetric==='recent' ? st.recent : st.max_gap;
    return `<div class="cell" data-n="${n}" style="background:${col}" title="freq ${st.freq} · last ${st.last_seen} · max gap ${st.max_gap} · due ${st.due} · recent ${st.recent}"><span class="num">${n}</span><span class="cnt">${label}</span></div>`;
  }).join('');
  document.getElementById('aleg').textContent = `metric: ${aMetric}`;

  const comboChip = c => `<span class="chip">${c.combo.join('-')} <b>${c.count}×</b> <span style="color:var(--muted)">gap ${c.max_gap} · last ${c.last} ago</span></span>`;
  document.getElementById('apairs').innerHTML = a.pairs.map(comboChip).join('');
  document.getElementById('atriplets').innerHTML = a.triplets.map(comboChip).join('');
  document.getElementById('aquads').innerHTML = a.quads.map(comboChip).join('');

  const rng = a.ranges;
  const rngTotal = Object.values(rng).reduce((x,y)=>x+y,0);
  document.getElementById('aranges').innerHTML = Object.keys(rng).map(k=>{
    const pct = (rng[k]/rngTotal*100).toFixed(1);
    return `<span class="chip">${k} <b>${rng[k]}</b> <span style="color:var(--muted)">${pct}%</span></span>`;
  }).join('');
  const oe = a.odd_even, oeTotal = oe.odd+oe.even;
  document.getElementById('aoddeven').innerHTML =
    `<span class="chip">Odd <b>${oe.odd}</b> <span style="color:var(--muted)">${(oe.odd/oeTotal*100).toFixed(1)}%</span></span>` +
    `<span class="chip">Even <b>${oe.even}</b> <span style="color:var(--muted)">${(oe.even/oeTotal*100).toFixed(1)}%</span></span>`;
  const hl = a.high_low, hlTotal = hl.high+hl.low;
  document.getElementById('ahighlow').innerHTML =
    `<span class="chip">High (41-80) <b>${hl.high}</b> <span style="color:var(--muted)">${(hl.high/hlTotal*100).toFixed(1)}%</span></span>` +
    `<span class="chip">Low (1-40) <b>${hl.low}</b> <span style="color:var(--muted)">${(hl.low/hlTotal*100).toFixed(1)}%</span></span>`;
  document.getElementById('asum').textContent =
    `Draw sum: min ${a.sum_min} · avg ${a.sum_avg} · max ${a.sum_max} (over ${a.total} draws)`;

  // cards
  document.getElementById('cards').innerHTML = [
    ['Today\'s draws', a.total],
    ['Newest game #', a.newest],
  ].map(([k,v])=>`<div class="card"><div class="k">${k}</div><div class="v">${v}</div></div>`).join('');
}

function renderToday(t){
  document.getElementById('tnote').textContent =
    `Today (${t.today}): ${t.n_today} draws · baseline: ${t.n_base} draws over ${t.baseline_days} days`;
  document.getElementById('tpick').textContent = t.pick_numbers.join(' · ');
  document.getElementById('tpickpair').textContent =
    `Top pair: ${t.pick_pair.join('-')} (z ${t.top_pairs[0].z})`;
  document.getElementById('tnums').innerHTML = t.top_numbers.map((n,i)=>{
    return `<span class="chip" style="display:flex;justify-content:space-between;gap:10px">
      <span><b>#${i+1}</b> ${n.num}</span>
      <span style="color:var(--muted)">today ${n.today}× (${n.today_pct}%) · base ${n.base_pct}% · z ${n.z}</span>
    </span>`;
  }).join('');
  document.getElementById('tpairs').innerHTML = t.top_pairs.map((p,i)=>{
    return `<span class="chip" style="display:flex;justify-content:space-between;gap:10px">
      <span><b>#${i+1}</b> ${p.pair.join('-')}</span>
      <span style="color:var(--muted)">today ${p.today}× (${p.today_pct}%) · base ${p.base_pct}% · z ${p.z}</span>
    </span>`;
  }).join('');
  document.getElementById('tdisclaimer').textContent =
    '⚠ Today\'s sample is small — a high z-score on a few dozen draws is normal random noise. Every number stays ~25% and every pair ~6% long-term; today\'s trend describes the past, not the next draw.';
}

['aFreq','aDue','aRecent','aGap'].forEach(id=>{
  document.getElementById(id).onclick = ()=>{
    aMetric = aMetrics[id];
    ['aFreq','aDue','aRecent','aGap'].forEach(x=>document.getElementById(x).classList.toggle('on',x===id));
    if(A) renderAnalysis(A);
  };
});

function tick(){
  fetch('api/today-analysis',{cache:'no-store'}).then(r=>r.json()).then(a=>{
    if(a && a.numbers){
      document.getElementById('dot').className = 'dot on';
      document.getElementById('status').textContent = `live · ${a.total} draws today`;
      renderAnalysis(a);
    }
  }).catch(()=>{
    document.getElementById('dot').className = 'dot err';
    document.getElementById('status').textContent = 'offline';
  });
  fetch('api/today',{cache:'no-store'}).then(r=>r.json()).then(t=>{
    if(t && t.top_numbers) renderToday(t);
  }).catch(()=>{});
}

tick();
setInterval(tick, 2000);
</script>
</body>
</html>
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default=HOST)
    ap.add_argument("--port", type=int, default=PORT)
    ap.add_argument("--interval", type=int, default=60,
                    help="auto-scrape interval in seconds (default 60)")
    ap.add_argument("--no-scrape", action="store_true",
                    help="serve only; do not run the background scraper")
    args = ap.parse_args()

    print("Computing initial state from store...")
    refresh_state()

    if not args.no_scrape:
        print(f"Auto-scraper: every {args.interval}s (today + yesterday).")
        threading.Thread(target=scraper_loop, args=(args.interval,), daemon=True).start()
    else:
        print("Auto-scraper disabled (--no-scrape).")

    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"Dashboard:  http://localhost:{args.port}   (Ctrl+C to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
