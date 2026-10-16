// Umami `data-before-send` hook: rewrites only what is sent to analytics, never
// the real URL. Room codes don't reach Umami, and every room/game lands in one
// row of the report:
//   /room/6454        → /room/:code
//   /game/6920        → /game/:code
//   /join?code=6454   → /join?code=xxxx   (still shows "came via invite link")
// Other query params (edition, utm_*, fbclid, …) are kept — Umami's traffic
// channels are built from them.

function normalizeAnalyticsUrl(raw, origin) {
  let u;
  try { u = new URL(raw, origin); } catch { return raw; }
  if (u.origin !== origin) return raw;  // external referrer: leave as is
  u.pathname = u.pathname.replace(/^\/(room|game)\/[^/]+\/?$/, '/$1/:code');
  if (u.searchParams.has('code')) u.searchParams.set('code', 'xxxx');
  u.searchParams.delete('session_id');
  u.hash = '';
  const relative = String(raw).startsWith('/');
  return relative ? u.pathname + u.search : u.toString();
}

function umamiBeforeSend(type, payload) {
  if (!payload) return payload;
  const origin = location.origin;
  if (payload.url) payload.url = normalizeAnalyticsUrl(payload.url, origin);
  if (payload.referrer) payload.referrer = normalizeAnalyticsUrl(payload.referrer, origin);
  return payload;
}
