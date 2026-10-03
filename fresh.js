// Shared fresh-data loader: reads the freshest JSON from BOTH sources.
// raw.githubusercontent = freshest commit (updated every ~2.5 min by the
// Action) but caches up to 5 min in CDN/browser -> cache-busted.
// Same-origin deployed copy = always works, may lag a few minutes during
// Vercel's deploy queue. We take whichever has the newest `last_game`
// (or `newest_game` for draws.json).
(function () {
  'use strict';

  const RAW_BASE = 'https://raw.githubusercontent.com/thesahilsinh/ct-keno/main/data/';

  async function grab(url) {
    const bust = url + (url.includes('?') ? '&' : '?') + 't=' + Date.now();
    const r = await fetch(bust, { cache: 'no-store' });
    if (!r.ok) throw new Error('HTTP ' + r.status);
    return r.json();
  }

  async function bestOf(name, key) {
    // Pick whichever source has the newer data. Numeric stamp preferred
    // (last_game / newest_game); history.json falls back to games[0];
    // sim.json uses the 'today' DATE string — compare those lexically.
    const stamp = (j) => {
      if (!j) return null;
      if (typeof j[key] === 'number') return j[key];
      if (Array.isArray(j.games) && j.games.length) return j.games[0];
      if (typeof j[key] === 'string') return j[key];      // ISO date
      if (typeof j.today === 'string') return j.today;
      return null;
    };
    const newer = (a, b) => {
      const x = stamp(a), y = stamp(b);
      if (x === null) return false;
      if (y === null) return true;
      return x > y;    // works for numbers AND ISO date strings
    };
    const results = await Promise.allSettled([
      grab(RAW_BASE + name),
      grab('data/' + name),
    ]);
    let best = null;
    for (const res of results) {
      if (res.status !== 'fulfilled') continue;
      const j = res.value;
      if (!j) continue;
      if (best === null || newer(j, best)) {
        best = j;
      }
    }
    if (best === null) throw new Error('no source reachable for ' + name);
    return best;
  }

  window.freshData = {
    // draws.json / history.json keyed on newest_game; live.json keyed on last_game
    get: (name, key) => bestOf(name, key || 'last_game'),
    grab,
  };
})();