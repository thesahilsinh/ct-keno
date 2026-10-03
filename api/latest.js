// Serverless proxy: latest official CT Keno draws, same-origin.
// The official API (ctilottery.org) sends no access-control-allow-origin,
// so the browser cannot call it directly — this function fetches it
// server-side (no CORS restriction) and returns compact JSON:
//   { ok, fetched_at, newest_game, draws: [{game, time, nums}] }
const API =
  'https://www.ctilottery.org/api/v1/draw-games/draws/page' +
  '?order=DESC&game-names=Keno&status=CLOSED&size=6&page=0';

export default async function handler(req, res) {
  res.setHeader('Cache-Control', 'no-store');
  try {
    const r = await fetch(API, {
      headers: { 'User-Agent': 'Mozilla/5.0 (compatible; ct-keno-live/1.0)' },
      signal: AbortSignal.timeout(8000),
    });
    if (!r.ok) throw new Error('upstream ' + r.status);
    const data = await r.json();
    const draws = (data.draws || [])
      .map((d) => {
        const primary = (d.results && d.results[0] && d.results[0].primary) || [];
        const nums = primary
          .filter((x) => typeof x === 'string' && !x.startsWith('M-'))
          .map(Number)
          .sort((a, b) => a - b);
        return {
          game: Number(d.id),
          time: d.drawTime,
          nums,
        };
      })
      .filter((d) => d.nums.length === 20);
    res.status(200).json({
      ok: true,
      fetched_at: Date.now(),
      newest_game: draws.length ? draws[0].game : null,
      draws,
    });
  } catch (e) {
    res.status(200).json({ ok: false, error: String(e), fetched_at: Date.now() });
  }
}