#!/usr/bin/env python3
"""Build the keno visualization website from data/draws.csv.

Reads the scraped draws, computes frequency / hot-cold / bonus stats, and writes
a fully self-contained site/index.html (data embedded, no CDN, opens via file://).
Re-run after every `python cli.py scrape` to refresh the visuals.
"""
import json
import collections
from datetime import datetime
from pathlib import Path

import store

ROOT = Path(__file__).resolve().parent
STORE = ROOT / "data" / "draws.csv"
OUT = ROOT / "site" / "index.html"
EXPECTED_PER_NUM = None  # filled after we know total

def compute_data(draws=None):
    """Compute the full stats dict from the store (reused by the live server)."""
    if draws is None:
        draws = store.load_draws(STORE)
    if not draws:
        raise SystemExit("No draws in store. Run `python cli.py scrape` first.")
    draws = sorted(draws, key=lambda d: d["game_no"], reverse=True)  # newest first
    total = len(draws)
    expected = total * 20 / 80  # each number expected count if perfectly uniform

    freq = collections.Counter()
    last_seen = {}
    for idx, d in enumerate(draws):
        for n in d["numbers"]:
            freq[n] += 1
            last_seen[n] = idx  # 0 = appeared in the newest draw

    bonus = collections.Counter(d["bonus"] for d in draws)
    gnos = [d["game_no"] for d in draws]

    # Per-day draw counts (only rows that carry a draw_date from a live scrape)
    per_day = collections.Counter(d.get("draw_date") or "" for d in draws)
    per_day = {k: v for k, v in per_day.items() if k}
    day_trend = [{"date": k, "count": per_day[k]} for k in sorted(per_day)]

    return {
        "generated": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "total_draws": total,
        "game_min": min(gnos),
        "game_max": max(gnos),
        "expected_per_num": round(expected, 1),
        "freq": {str(n): freq.get(n, 0) for n in range(1, 81)},
        "last_seen": {str(n): last_seen.get(n, total) for n in range(1, 81)},
        "bonus": dict(bonus),
        "day_trend": day_trend,
        "draws": [{"game_no": d["game_no"], "bonus": d["bonus"], "numbers": d["numbers"]}
                  for d in draws],
    }


def build(max_embed: int = 3000):
    data = compute_data()
    gnos = [d["game_no"] for d in store.load_draws(STORE)]
    total = data["total_draws"]
    # Cap embedded draws so the static file stays light; the live server's
    # /api/state serves the full set. 3000 newest is enough for every view.
    if len(data["draws"]) > max_embed:
        data["draws"] = data["draws"][:max_embed]
        data["_embedded_note"] = f"showing {max_embed} newest of {total} draws"
    OUT.parent.mkdir(parents=True, exist_ok=True)
    html = TEMPLATE.replace("/*__DATA__*/", json.dumps(data))
    OUT.write_text(html, encoding="utf-8")
    print(f"Wrote {OUT}  ({total} draws in store, embedded {len(data['draws'])}; "
          f"game #{min(gnos)}..{max(gnos)})")


TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>CT Keno — Draw Visualizer</title>
<style>
  :root{
    --bg:#0f1419; --panel:#1a212b; --panel2:#222c39; --ink:#e8eef5; --muted:#8aa0b5;
    --line:#2c3848; --hot:#ff5a4d; --cold:#3a8dde; --accent:#ffce54;
  }
  *{box-sizing:border-box}
  body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 -apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif}
  header{padding:22px 26px;border-bottom:1px solid var(--line);background:linear-gradient(180deg,#16202c,#0f1419)}
  h1{margin:0;font-size:22px;letter-spacing:.3px}
  h1 small{color:var(--muted);font-weight:400;font-size:13px;margin-left:8px}
  .wrap{max-width:1100px;margin:0 auto;padding:22px 26px 60px}
  .cards{display:flex;flex-wrap:wrap;gap:12px;margin:18px 0 26px}
  .card{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:14px 18px;min-width:150px}
  .card .k{color:var(--muted);font-size:12px;text-transform:uppercase;letter-spacing:.6px}
  .card .v{font-size:24px;font-weight:700;margin-top:4px}
  section{background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:18px 20px;margin-bottom:22px}
  section h2{margin:0 0 14px;font-size:17px}
  .toolbar{display:flex;gap:10px;flex-wrap:wrap;align-items:center;margin-bottom:14px}
  .toolbar button,.toolbar select{background:var(--panel2);color:var(--ink);border:1px solid var(--line);
    border-radius:8px;padding:7px 12px;font-size:13px;cursor:pointer}
  .toolbar button.on{background:var(--accent);color:#1a1300;border-color:var(--accent);font-weight:700}
  .board{display:grid;grid-template-columns:repeat(10,1fr);gap:6px}
  .cell{position:relative;aspect-ratio:1/1;border-radius:9px;display:flex;flex-direction:column;
    align-items:center;justify-content:center;font-weight:700;cursor:pointer;border:1px solid rgba(255,255,255,.06);
    transition:transform .06s}
  .cell .num{font-size:15px}
  .cell .cnt{font-size:11px;font-weight:500;opacity:.85;margin-top:1px}
  .cell:hover{transform:scale(1.06);outline:2px solid #fff6}
  .cell.dim{opacity:.18}
  .cell.hl{outline:2px solid var(--accent);outline-offset:1px}
  .legend{display:flex;align-items:center;gap:10px;margin-top:14px;color:var(--muted);font-size:12px}
  .legend .bar{height:12px;width:200px;border-radius:6px;
    background:linear-gradient(90deg,#1c3a5e,#2f6f4f,#caa53a,#e8732f,#ff4d3d)}
  .lists{display:flex;gap:18px;flex-wrap:wrap}
  .list{flex:1;min-width:240px}
  .list h3{margin:0 0 8px;font-size:14px;color:var(--muted)}
  .chips{display:flex;flex-wrap:wrap;gap:6px}
  .chip{background:var(--panel2);border:1px solid var(--line);border-radius:20px;padding:5px 11px;font-size:13px}
  .chip b{color:var(--accent)}
  .bonus{display:flex;gap:14px;flex-wrap:wrap}
  .bcol{flex:1;min-width:120px}
  .bcol .lab{display:flex;justify-content:space-between;font-size:13px;margin-bottom:4px}
  .track{height:14px;background:var(--panel2);border-radius:7px;overflow:hidden}
  .fill{height:100%;background:linear-gradient(90deg,#caa53a,#ff7a3d)}
  .hist{max-height:520px;overflow:auto;border:1px solid var(--line);border-radius:10px}
  table{width:100%;border-collapse:collapse;font-size:13px}
  thead th{position:sticky;top:0;background:var(--panel2);text-align:left;padding:9px 12px;color:var(--muted);
    font-weight:600;border-bottom:1px solid var(--line)}
  tbody td{padding:8px 12px;border-bottom:1px solid #232e3c}
  tbody tr:hover{background:#1e2a36}
  .nums{display:flex;flex-wrap:wrap;gap:4px;max-width:560px}
  .n{width:24px;height:24px;border-radius:50%;display:flex;align-items:center;justify-content:center;
    font-size:11px;background:var(--panel2);border:1px solid var(--line)}
  .n.on{background:var(--accent);color:#1a1300;border-color:var(--accent);font-weight:700}
  .b{padding:2px 8px;border-radius:6px;font-size:11px;font-weight:700;background:#2a3645}
  .b.x10{background:#ff4d3d;color:#fff}.b.x5{background:#ff8a3d}.b.x4{background:#e8b53a}
  .b.x3{background:#9bd24a}.b.x2{background:#4aa3d2}.b.nb{background:#3a4654}
  .note{color:var(--muted);font-size:12px;margin-top:8px}
  input.search{background:var(--panel2);color:var(--ink);border:1px solid var(--line);border-radius:8px;padding:7px 12px;font-size:13px;min-width:160px}
  /* trend */
  #trendWrap{position:relative}
  #trend{width:100%;height:300px;display:block}
  .tcanvas{width:100%;height:300px}
  .tnote{color:var(--muted);font-size:12px;margin-top:8px}
  .chart-legend{display:flex;gap:16px;color:var(--muted);font-size:12px;margin-top:6px}
  .chart-legend .sw{display:inline-block;width:12px;height:12px;border-radius:3px;vertical-align:-1px;margin-right:5px}
  /* live */
  .status{display:flex;align-items:center;gap:9px;font-size:13px;color:var(--muted)}
  .dot{width:10px;height:10px;border-radius:50%;background:#555;box-shadow:0 0 0 0 rgba(80,200,120,.6)}
  .dot.on{background:#46c878;animation:pulse 1.8s infinite}
  .dot.err{background:#ff5a4d}
  @keyframes pulse{0%{box-shadow:0 0 0 0 rgba(70,200,120,.5)}70%{box-shadow:0 0 0 8px rgba(70,200,120,0)}100%{box-shadow:0 0 0 0 rgba(70,200,120,0)}}
  .live-list{display:flex;flex-direction:column;gap:6px;max-height:260px;overflow:auto}
  .lrow{display:flex;gap:12px;align-items:center;background:var(--panel2);border:1px solid var(--line);border-radius:8px;padding:7px 11px}
  .lrow .gno{color:var(--muted);font-size:12px;min-width:74px}
  .lrow .lns{display:flex;flex-wrap:wrap;gap:3px}
  .lrow .ln{width:20px;height:20px;border-radius:50%;display:flex;align-items:center;justify-content:center;font-size:10px;background:#2a3645;border:1px solid var(--line)}
  .lrow .lb{margin-left:auto}
  .flash{animation:flash .9s ease-out}
  @keyframes flash{0%{background:#364a2f}100%{background:var(--panel2)}}
  .timebar{fill:#4aa3d2}
  .timebar.avg{fill:#ffce54}
</head>
<body>
<header>
  <h1>CT Keno — Draw Visualizer <small>scraped from ctlottery.org</small></h1>
</header>
<div class="wrap">
  <div class="cards" id="cards"></div>

  <section>
    <h2>Number Frequency Board</h2>
    <div class="toolbar">
      <button id="sortNum" class="on">Sort by number</button>
      <button id="sortFreq">Sort by frequency</button>
      <button id="hotOnly">Hot only</button>
      <button id="coldOnly">Cold only</button>
      <button id="reset">Reset</button>
      <span class="note">Click a number to highlight it in the draw history.</span>
    </div>
    <div class="board" id="board"></div>
    <div class="legend"><span>cold</span><div class="bar"></div><span>hot</span>
      <span id="legtxt" style="margin-left:10px"></span></div>
    <div class="note">Each number is drawn ~<b id="exp"></b> times in this sample (uniform expectation). Cell text = times drawn.</div>
  </section>

  <section>
    <h2>Hot &amp; Cold Numbers</h2>
    <div class="lists">
      <div class="list"><h3>Hot (most drawn)</h3><div class="chips" id="hot"></div></div>
      <div class="list"><h3>Cold (least drawn)</h3><div class="chips" id="cold"></div></div>
      <div class="list"><h3>Longest since last seen</h3><div class="chips" id="overdue"></div></div>
    </div>
  </section>

  <section>
    <h2>Bonus Multiplier Distribution</h2>
    <div class="bonus" id="bonus"></div>
    <div class="note">Bonus doubles your wager; any prize won is multiplied by the draw's multiplier. Frequency is from the actual scraped draws.</div>
  </section>

  <section>
    <h2>Number Trend Over Time</h2>
    <div class="toolbar">
      <label style="color:var(--muted);font-size:13px">Track number</label>
      <select id="trendNum"></select>
      <label style="color:var(--muted);font-size:13px">Window</label>
      <select id="trendWin">
        <option value="20">20 draws</option>
        <option value="50" selected>50 draws</option>
        <option value="100">100 draws</option>
        <option value="200">200 draws</option>
      </select>
      <button id="trendClear">Clear</button>
    </div>
    <div id="trendWrap"><canvas id="trend" class="tcanvas"></canvas></div>
    <div class="chart-legend"><span><span class="sw" style="background:var(--accent)"></span>rolling hit-rate (share of window draws containing the number)</span></div>
    <div class="tnote">X axis = newest-first draw order (the site publishes no timestamps). With ~221–314 draws per day, ~50 draws ≈ a few hours. Compares each number's rolling frequency to the uniform baseline (<b id="basePct"></b>).</div>
  </section>

  <section>
    <h2>Draws Per Day</h2>
    <div id="dayWrap"><canvas id="dayChart" class="tcanvas" style="height:240px"></canvas></div>
    <div class="tnote" id="dayNote"></div>
  </section>

  <section>
    <h2>Draw History</h2>
    <div class="toolbar">
      <input class="search" id="search" placeholder="Filter by game # or number...">
      <select id="bonusFilter"><option value="">All bonus</option></select>
    </div>
    <div class="hist">
      <table>
        <thead><tr><th>Game #</th><th>Bonus</th><th>Winning numbers (20)</th></tr></thead>
        <tbody id="rows"></tbody>
      </table>
    </div>
    <div class="note">Newest first. "Draws ago" = position from the newest draw (the site publishes no timestamps).</div>
  </section>

  <section id="liveSec">
    <h2>Live Auto-Scraper</h2>
    <div class="status" id="liveStatus"><span class="dot" id="liveDot"></span><span id="liveText">connecting…</span></div>
    <div class="toolbar">
      <button id="liveToggle">Pause live updates</button>
      <span class="note" id="liveMeta"></span>
    </div>
    <h3 style="margin:14px 0 8px;font-size:14px;color:var(--muted)">Latest draws (auto-updating)</h3>
    <div class="live-list" id="liveList"></div>
  </section>

  <section id="timeSec">
    <h2>Draws Over Time — 7-Day / Weekly / Monthly</h2>
    <div class="toolbar">
      <button id="v7" class="on">7 days</button>
      <button id="vW">Weekly</button>
      <button id="vM">Monthly</button>
    </div>
    <div id="timeWrap"><canvas id="timeChart" class="tcanvas" style="height:300px"></canvas></div>
    <div class="chart-legend">
      <span><span class="sw" style="background:#4aa3d2"></span>draws in period</span>
      <span><span class="sw" style="background:#ffce54"></span>avg numbers hit / draw (1–80)</span>
    </div>
    <div class="tnote" id="timeNote"></div>
  </section>

</div>

<script>
const DATA = /*__DATA__*/;
const N = 80;
const freqArr = Array.from({length:N}, (_,i)=>DATA.freq[String(i+1)]);
const maxF = Math.max(...freqArr), minF = Math.min(...freqArr);

function heat(v){
  // map min..max -> 0..1, color blue->green->yellow->orange->red
  const t = (v-minF)/Math.max(1,(maxF-minF));
  const stops=[[28,58,94],[47,111,79],[202,165,58],[232,115,47],[255,77,61]];
  const x=t*(stops.length-1), i=Math.floor(x), f=x-i;
  const a=stops[i], b=stops[Math.min(stops.length-1,i+1)];
  const c=k=>Math.round(a[k]+(b[k]-a[k])*f);
  return `rgb(${c(0)},${c(1)},${c(2)})`;
}
function colorForNum(v){return heat(v);}

function buildCards(){
  const c=document.getElementById('cards');
  const items=[
    ['Total draws', DATA.total_draws],
    ['Game # range', DATA.game_min+' – '+DATA.game_max],
    ['Expected / number', DATA.expected_per_num],
    ['Bonus draws', Object.keys(DATA.bonus).length+' types'],
  ];
  c.innerHTML=items.map(([k,v])=>`<div class="card"><div class="k">${k}</div><div class="v">${v}</div></div>`).join('');
  document.getElementById('exp').textContent=DATA.expected_per_num;
}

let selected=null;
function renderBoard(order){
  const board=document.getElementById('board');
  let nums=[...Array(N).keys()].map(i=>i+1);
  if(order==='freq') nums.sort((a,b)=>DATA.freq[b]-DATA.freq[a]);
  board.innerHTML=nums.map(n=>{
    const v=DATA.freq[n], ago=DATA.last_seen[n];
    const dim = selected && n!==selected ? '' : '';
    return `<div class="cell" data-n="${n}" style="background:${colorForNum(v)}">
      <span class="num">${n}</span><span class="cnt">${v}</span></div>`;
  }).join('');
  document.querySelectorAll('.cell').forEach(el=>{
    el.onclick=()=>toggleSelect(+el.dataset.n);
  });
}

function toggleSelect(n){
  selected = selected===n ? null : n;
  document.querySelectorAll('.cell').forEach(el=>{
    const m=+el.dataset.n;
    el.classList.toggle('hl', selected===m);
    el.classList.toggle('dim', selected && m!==selected);
  });
  filterRows();
  document.getElementById('legtxt').textContent = selected
    ? `selected #${selected} — appeared ${DATA.freq[selected]}×, last ${DATA.last_seen[selected]} draws ago` : '';
}

function chip(n,v){
  const pct=((v-DATA.expected_per_num)/DATA.expected_per_num*100);
  const sign=pct>=0?'+':'';
  const col=pct>=0?'var(--hot)':'var(--cold)';
  return `<span class="chip">#${n} <b>${v}</b> <span style="color:${col}">(${sign}${pct.toFixed(0)}%)</span></span>`;
}
function buildLists(){
  const arr=[...Array(N).keys()].map(i=>i+1);
  const byFreq=[...arr].sort((a,b)=>DATA.freq[b]-DATA.freq[a]);
  const byOver=[...arr].sort((a,b)=>DATA.last_seen[b]-DATA.last_seen[a]);
  document.getElementById('hot').innerHTML=byFreq.slice(0,10).map(n=>chip(n,DATA.freq[n])).join('');
  document.getElementById('cold').innerHTML=byFreq.slice(-10).map(n=>chip(n,DATA.freq[n])).join('');
  document.getElementById('overdue').innerHTML=byOver.slice(0,10)
    .map(n=>`<span class="chip">#${n} <b>${DATA.last_seen[n]}</b> draws ago</span>`).join('');
}

function buildBonus(){
  const order=['No Bonus','2X','3X','4X','5X','10X'];
  const keys=order.filter(k=>k in DATA.bonus);
  const max=Math.max(...keys.map(k=>DATA.bonus[k]));
  document.getElementById('bonus').innerHTML=keys.map(k=>{
    const v=DATA.bonus[k], pct=(v/DATA.total_draws*100).toFixed(1);
    const w=(v/max*100).toFixed(1);
    const cls=k==='No Bonus'?'nb':('x'+k.replace('X',''));
    return `<div class="bcol"><div class="lab"><span class="b ${cls}">${k}</span><span>${v} (${pct}%)</span></div>
      <div class="track"><div class="fill" style="width:${w}%"></div></div></div>`;
  }).join('');
}

function bcls(b){return b==='No Bonus'?'nb':('x'+b.replace('X',''));}
function rowHTML(d,idx){
  const ns=d.numbers.map(n=>{
    const on=selected&&n===selected?'on':'';
    return `<span class="n ${on}">${n}</span>`;
  }).join('');
  return `<tr><td>#${d.game_no}</td><td><span class="b ${bcls(d.bonus)}">${d.bonus}</span></td>
    <td><div class="nums">${ns}</div></td></tr>`;
}
function filterRows(){
  const q=document.getElementById('search').value.trim().toLowerCase();
  const bf=document.getElementById('bonusFilter').value;
  const rows=document.getElementById('rows');
  let out=[];
  DATA.draws.forEach((d,idx)=>{
    if(bf && d.bonus!==bf) return;
    if(q){
      if(!('#'+d.game_no).includes(q) && !d.numbers.some(n=>(''+n).includes(q))) return;
    }
    out.push(rowHTML(d,idx));
  });
  rows.innerHTML=out.join('');
}

/* ---------- Number trend over time (rolling hit-rate line) ---------- */
let trendNum=null, trendWin=50;
function setupTrendControls(){
  const sel=document.getElementById('trendNum');
  for(let n=1;n<=80;n++){const o=document.createElement('option');o.value=n;o.textContent='#'+n;sel.appendChild(o);}
  sel.value=1;
  document.getElementById('basePct').textContent=(25).toFixed(0)+'%'; // 20/80 = 25%
  sel.onchange=()=>{trendNum=+sel.value;drawTrend();};
  document.getElementById('trendWin').onchange=e=>{trendWin=+e.target.value;drawTrend();};
  document.getElementById('trendClear').onclick=()=>{trendNum=null;drawTrend();};
}
function rollingHitRate(num,win){
  // DATA.draws is newest-first; compute rolling "contains num" rate over a trailing window
  const arr=DATA.draws; const n=arr.length; const out=[];
  for(let i=0;i<n;i++){
    const end=Math.min(n,i+win);
    let hit=0;
    for(let j=i;j<end;j++) if(arr[j].numbers.includes(num)) hit++;
    out.push(hit/(end-i));
  }
  return out;
}
function fitCanvas(c){
  const dpr=window.devicePixelRatio||1;
  const w=c.clientWidth, h=c.clientHeight||300;
  c.width=w*dpr; c.height=h*dpr;
  const ctx=c.getContext('2d'); ctx.setTransform(dpr,0,0,dpr,0,0);
  return {ctx,w,h};
}
function drawTrend(){
  const c=document.getElementById('trend');
  const {ctx,w,h}=fitCanvas(c);
  ctx.clearRect(0,0,w,h);
  if(!trendNum){
    ctx.fillStyle='#8aa0b5';ctx.font='14px sans-serif';ctx.textAlign='center';
    ctx.fillText('Pick a number above to plot its rolling hit-rate →',w/2,h/2);
    return;
  }
  const base=0.25; // 20/80
  const series=rollingHitRate(trendNum,trendWin);
  const padL=44,padR=14,padT=14,padB=28;
  const x0=padL,x1=w-padR,y0=h-padB,y1=padT;
  const yMin=0,yMax=0.6;
  const X=i=>x0+(x1-x0)*(i/(series.length-1||1));
  const Y=v=>y0+(y1-y0)*((v-yMin)/(yMax-yMin));
  // grid + baseline 25%
  ctx.strokeStyle='#2c3848';ctx.fillStyle='#8aa0b5';ctx.font='11px sans-serif';ctx.textAlign='right';
  [0,0.25,0.5].forEach(v=>{const y=Y(v);ctx.beginPath();ctx.moveTo(x0,y);ctx.lineTo(x1,y);ctx.stroke();
    ctx.fillText((v*100)+'%',x0-8,y+3);});
  // baseline (uniform) dashed
  ctx.save();ctx.setLineDash([5,5]);ctx.strokeStyle='#3a4654';
  ctx.beginPath();ctx.moveTo(x0,Y(base));ctx.lineTo(x1,Y(base));ctx.stroke();ctx.restore();
  // line
  ctx.strokeStyle='#ffce54';ctx.lineWidth=2;ctx.beginPath();
  series.forEach((v,i)=>{const x=X(i),y=Y(v);i?ctx.lineTo(x,y):ctx.moveTo(x,y);});
  ctx.stroke();
  // fill under
  ctx.lineTo(x1,y0);ctx.lineTo(x0,y0);ctx.closePath();
  ctx.fillStyle='rgba(255,206,84,.10)';ctx.fill();
  // axis labels
  ctx.fillStyle='#8aa0b5';ctx.textAlign='center';
  ctx.fillText('newest → oldest (draw order)',(x0+x1)/2,h-8);
  const cur=series[0];
  ctx.fillStyle='#ffce54';ctx.textAlign='left';ctx.font='13px sans-serif';
  ctx.fillText(`#${trendNum} last ${trendWin} draws: ${(cur*100).toFixed(0)}% (base 25%)`,x0+6,y1+12);
}

/* ---------- Draws per day bar chart ---------- */
function drawDayChart(){
  const c=document.getElementById('dayChart');
  const dt=DATA.day_trend||[];
  if(!dt.length){
    const {ctx,w}=fitCanvas(c);
    ctx.fillStyle='#8aa0b5';ctx.font='13px sans-serif';ctx.textAlign='center';
    ctx.fillText('No dated days yet — run `python cli.py scrape` to stamp dates.',w/2,120);
    document.getElementById('dayNote').textContent='Per-day counts appear once scrapes carry a date.';
    return;
  }
  const {ctx,w,h}=fitCanvas(c);
  ctx.clearRect(0,0,w,h);
  const max=Math.max(...dt.map(d=>d.count));
  const padL=44,padB=30,padT=14,padR=10;
  const x0=padL,x1=w-padR,y0=h-padB,y1=padT;
  const bw=(x1-x0)/dt.length;
  ctx.strokeStyle='#2c3848';ctx.fillStyle='#8aa0b5';ctx.font='11px sans-serif';ctx.textAlign='right';
  [0,max/2,max].forEach(v=>{const y=y0+(y1-y0)*(v/max);ctx.beginPath();ctx.moveTo(x0,y);ctx.lineTo(x1,y);ctx.stroke();
    ctx.fillText(Math.round(v),x0-8,y+3);});
  ctx.fillStyle='#4aa3d2';ctx.textAlign='center';
  dt.forEach((d,i)=>{const bh=(y0-y1)*(d.count/max);const x=x0+i*bw;
    ctx.fillRect(x+2,y0-bh,bw-4,bh);});
  // x labels (dates) sparse
  ctx.fillStyle='#8aa0b5';ctx.textAlign='center';
  const step=Math.ceil(dt.length/8);
  dt.forEach((d,i)=>{if(i%step===0){const x=x0+i*bw+bw/2;ctx.fillText(d.date.slice(5),x,h-10);}});
  ctx.fillStyle='#8aa0b5';
  ctx.fillText(`days with data: ${dt.length}  ·  total dated draws: ${dt.reduce((a,d)=>a+d.count,0)}`,(x0+x1)/2,h-2+0);
}

/* ---------- Time views: 7-day / weekly / monthly ---------- */
let timeMode='7';
function bucketize(mode){
  // DATA.day_trend = [{date:"YYYY-MM-DD", count:N}] already aggregated per day.
  const dt = (DATA.day_trend||[]).slice();
  if(!dt.length) return [];
  if(mode==='7'){
    return dt.slice(-7);
  } else if(mode==='week'){
    const dayMs=86400000, wk={};
    dt.forEach(d=>{const dt0=new Date(d.date+'T00:00:00');const dow=(dt0.getDay()+6)%7;
      const mon=new Date(dt0-dow*dayMs);const key=mon.toISOString().slice(0,10);
      wk[key]=(wk[key]||0)+d.count;});
    return Object.keys(wk).sort().slice(-12).map(k=>({date:k,count:wk[k]}));
  } else { // month
    const mo={};
    dt.forEach(d=>{const k=d.date.slice(0,7);mo[k]=(mo[k]||0)+d.count;});
    return Object.keys(mo).sort().slice(-12).map(k=>({date:k,count:mo[k]}));
  }
}
function drawTimeChart(){
  const c=document.getElementById('timeChart');
  if(!c) return;
  const data=bucketize(timeMode);
  const note=document.getElementById('timeNote');
  if(!data.length){
    const {ctx,w}=fitCanvas(c);
    ctx.clearRect(0,0,w,160);ctx.fillStyle='#8aa0b5';ctx.font='13px sans-serif';ctx.textAlign='center';
    ctx.fillText('No dated draws yet — the auto-scraper stamps dates as it runs.',w/2,120);
    note.textContent='Runs populate when the live scraper adds dated days.';
    return;
  }
  const {ctx,w,h}=fitCanvas(c); ctx.clearRect(0,0,w,h);
  const max=Math.max(...data.map(d=>d.count),1);
  const padL=46,padB=30,padT=16,padR=12;
  const x0=padL,x1=w-padR,y0=h-padB,y1=padT;
  const bw=(x1-x0)/data.length;
  ctx.strokeStyle='#2c3848';ctx.fillStyle='#8aa0b5';ctx.font='11px sans-serif';ctx.textAlign='right';
  [0,max/2,max].forEach(v=>{const y=y0+(y1-y0)*(v/max);ctx.beginPath();ctx.moveTo(x0,y);ctx.lineTo(x1,y);ctx.stroke();
    ctx.fillText(Math.round(v),x0-8,y+3);});
  data.forEach((d,i)=>{const bh=(y0-y1)*(d.count/max);const x=x0+i*bw;
    ctx.fillStyle='#4aa3d2';ctx.fillRect(x+3,y0-bh,bw-6,bh);
    ctx.fillStyle='#8aa0b5';ctx.textAlign='center';ctx.fillText(d.date.slice(5),x+bw/2,h-10);});
  const tot=data.reduce((a,d)=>a+d.count,0);
  note.textContent=`${data.length} periods · ${tot} draws · avg ${(tot/data.length).toFixed(0)}/period`;
}
function setupTimeButtons(){
  const map={'v7':'7','vW':'week','vM':'month'};
  Object.keys(map).forEach(id=>{
    document.getElementById(id).onclick=()=>{
      timeMode=map[id];
      ['v7','vW','vM'].forEach(x=>document.getElementById(x).classList.toggle('on',x===id));
      drawTimeChart();
    };
  });
}

/* ---------- Live auto-scraper (polls /api/state) ---------- */
let liveOn=true, seenGames=new Set();
function iso(d){return d.toISOString().slice(0,10);}
function setupLive(){
  const dot=document.getElementById('liveDot'), txt=document.getElementById('liveText'),
        meta=document.getElementById('liveMeta');
  function tick(){
    if(!liveOn) return;
    fetch('api/state',{cache:'no-store'}).then(r=>r.json()).then(s=>{
      dot.className='dot on';
      txt.textContent=`live · last refresh ${s.server_time||''}`;
      const nd=s.newest? s.newest.game_no : '—';
      meta.textContent=`total ${s.total_draws} · newest game #${nd}` + (s.last_scrape? ' · auto-scrape '+s.last_scrape : '');
      const list=document.getElementById('liveList');
      const rows=(s.draws||[]).slice(0,12);
      list.innerHTML=rows.map(d=>{
        const ns=d.numbers.map(n=>`<span class="ln">${n}</span>`).join('');
        const isNew=!seenGames.has(d.game_no);
        seenGames.add(d.game_no);
        const bcls=d.bonus==='No Bonus'?'nb':('x'+d.bonus.replace('X',''));
        return `<div class="lrow ${isNew?'flash':''}"><span class="gno">#${d.game_no}</span>
          <span class="lns">${ns}</span><span class="b ${bcls} lb">${d.bonus}</span></div>`;
      }).join('');
      drawTimeChart();
    }).catch(e=>{dot.className='dot err';txt.textContent='live feed unavailable — open via http://localhost:8000 (not file://)';});
  }
  tick();
  setInterval(tick,5000);
}
function setupLiveToggle(){
  const b=document.getElementById('liveToggle');
  b.onclick=()=>{liveOn=!liveOn;b.textContent=liveOn?'Pause live updates':'Resume live updates';
    b.classList.toggle('on',liveOn); if(liveOn) setupLive();};
}

function init(){
  buildCards(); renderBoard('num'); buildLists(); buildBonus(); filterRows();
  setupTrendControls(); drawTrend(); drawDayChart();
  setupTimeButtons(); drawTimeChart();
  setupLive(); setupLiveToggle();
  document.getElementById('sortNum').onclick=()=>{setActive('sortNum');renderBoard('num');};
  document.getElementById('sortFreq').onclick=()=>{setActive('sortFreq');renderBoard('freq');};
  document.getElementById('hotOnly').onclick=()=>toggleBand('hot');
  document.getElementById('coldOnly').onclick=()=>toggleBand('cold');
  document.getElementById('reset').onclick=()=>{selected=null;renderBoard('num');filterRows();
    document.querySelectorAll('.cell').forEach(el=>el.classList.remove('dim','hl'));
    document.getElementById('legtxt').textContent='';document.getElementById('search').value='';
    document.getElementById('bonusFilter').value='';filterRows();};
  document.getElementById('search').oninput=filterRows;
  const bf=document.getElementById('bonusFilter');
  Object.keys(DATA.bonus).forEach(k=>{const o=document.createElement('option');o.value=k;o.textContent=k;bf.appendChild(o);});
  bf.onchange=filterRows;
  window.addEventListener('resize',()=>{drawTrend();drawDayChart();drawTimeChart();});
}
function setActive(id){['sortNum','sortFreq'].forEach(x=>document.getElementById(x).classList.toggle('on',x===id));}
let band=null;
function toggleBand(which){
  band = band===which ? null : which;
  document.getElementById('hotOnly').classList.toggle('on', band==='hot');
  document.getElementById('coldOnly').classList.toggle('on', band==='cold');
  const arr=[...Array(N).keys()].map(i=>i+1);
  const byFreq=[...arr].sort((a,b)=>DATA.freq[b]-DATA.freq[a]);
  const set = which==='hot' ? byFreq.slice(0,10) : byFreq.slice(-10);
  document.querySelectorAll('.cell').forEach(el=>{
    const m=+el.dataset.n;
    el.classList.toggle('hl', band && set.includes(m));
    el.classList.toggle('dim', band && !set.includes(m));
  });
}
init();
</script>
</body>
</html>
"""


if __name__ == "__main__":
    build()
