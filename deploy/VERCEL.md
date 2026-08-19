# Hosting the CT Keno dashboard on Vercel (free, no PC, no credit card)

This is the recommended setup for "I want a stable URL on my phone, but I can't
keep my PC running."

## How it works

```
GitHub Actions (free)                    Vercel (free)
  scrapes ctlottery.org  ──commits──▶  data/draws.json  ──served──▶  ct-keno.vercel.app
  every 5 minutes                       (persists in git)            (static site)
```

- **Vercel** hosts the static frontend at a stable URL — no IP to remember.
- **GitHub Actions** is the "background script that runs somewhere" — it scrapes
  on a schedule and commits the data back to the repo. Free, no credit card, no PC.

**The one tradeoff:** GitHub's fastest free schedule is **every 5 minutes**, not
30 seconds. Keno draws land every ~4 min, so you'll see each new draw within
~5 minutes. That's the price of "free + no PC + no credit card."

## Setup (one-time, ~10 minutes)

### 1. Push to GitHub
```bash
cd C:/Users/thesa/ct-keno-sim
git init
git add .
git commit -m "keno dashboard"
git branch -M main
git remote add origin https://github.com/<YOUR-USERNAME>/ct-keno.git
git push -u origin main
```
(You'll need a free GitHub account and to authenticate — `gh auth login` or a
personal access token.)

### 2. Deploy to Vercel
1. Go to https://vercel.com and sign up with your GitHub account (free).
2. Click **"Add New → Project"**, import the `ct-keno` repo.
3. Vercel auto-detects it as a static site. **No build command, no framework.**
   Set:
   - Framework preset: **Other**
   - Build command: *(leave empty)*
   - Output directory: *(leave empty — it serves the repo root)*
4. Click **Deploy**. You'll get a URL like `ct-keno.vercel.app`.

### 3. Verify the scraper runs
- Go to your repo → **Actions** tab. You should see the "Scrape Keno" workflow
  running every 5 minutes.
- After a run, check that `data/draws.json` was updated (the commit history
  will show "scrape: ..." commits).

### 4. Open on your phone
```
https://ct-keno.vercel.app
```
Bookmark it. Done.

## How the pieces fit

| File | Role |
|------|------|
| `build_data.py` | Scrapes today+yesterday, computes all analysis, writes `data/draws.json` |
| `.github/workflows/scrape.yml` | Runs `build_data.py` every 5 min, commits the result |
| `index.html` | Static frontend; fetches `data/draws.json` and renders everything |
| `data/draws.csv` | The persistent store (committed to git, survives between runs) |
| `data/draws.json` | The compact data the frontend reads |

## Caveats (honest)

- **~5-minute updates**, not real-time. Good enough for checking trends on your
  phone; not a second-by-second ticker.
- **GitHub Actions free tier** has a monthly minutes cap (2000 min/month for
  private repos, unlimited for public). A 5-min job × ~288/day ≈ 14,400
  min/month — **make the repo public** to stay free, or it'll hit the cap.
- **The CSV grows** — ~300 draws/day ≈ 30KB/day. Over a year that's ~10MB in
  git history. Fine for now; if it gets heavy, we can trim old rows.
- **No HTTPS concerns** — Vercel gives you HTTPS for free automatically.

## If you want true 30-second real-time instead

That requires an always-on server (see `deploy/README.md` for the Oracle Cloud
option). This Vercel setup is the best "free + no PC + no credit card" option.
