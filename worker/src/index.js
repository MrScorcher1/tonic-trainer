/* Tonic Trainer ratings Worker.
 *
 * Collects player difficulty ratings and serves the aggregate back. Deployed
 * separately from the Pages site; the site works without it.
 *
 * THE CONSTRAINT THAT SHAPES THIS FILE: Cloudflare's KV free tier allows
 * 100,000 reads but only 1,000 WRITES per day. Writes are 100x scarcer than
 * reads, so:
 *   - the client batches ~10 votes per request (see docs/ratings.js), and
 *   - everything lives under ONE aggregate key, not one key per song. Per-song
 *     keys would mean 3,094 reads per page load; one key is one read per
 *     session and one read-modify-write per flush.
 *
 * BUT BATCHING DOES NOT MAKE A REQUEST COST ONE WRITE. A 10-vote POST costs 12:
 * the aggregate (1), the per-IP rate-limit counter (1), and the
 * per-song-per-IP-per-day cap (1 per accepted vote, so 10). That puts the real
 * ceiling at ~830 ratings a day, not the ~10,000 an earlier comment claimed.
 * Eleven of every twelve writes are the abuse guards, so if the ceiling ever
 * binds, make the guards cheaper — batching larger barely moves it.
 *
 * ACCEPTED, NOT SOLVED: concurrent flushes are LAST-WRITE-WINS and can drop
 * votes. At this scale that is rare and cheap, and a queue or Durable Object is
 * not worth the moving part. This is not atomic and must not be described as
 * such.
 *
 * THE RATE LIMITING IS ABUSE REDUCTION, NOT SECURITY. A public write endpoint
 * gets found eventually. Per-IP request limits and a per-song-per-IP-per-day
 * cap raise the cost of casual ballot-stuffing; they do not prevent a
 * determined actor with many addresses, and nothing here should be described as
 * secure.
 */

const AGGREGATE_KEY = "aggregate:v1";
const MAX_BATCH = 50;            // a legitimate client sends ~10
const IP_WINDOW_SECONDS = 60;
const IP_MAX_REQUESTS = 20;      // per window
const PER_SONG_PER_IP_PER_DAY = 3;
const VALID_RATINGS = new Set([1, 2, 3]);

function corsHeaders(origin) {
  return {
    "Access-Control-Allow-Origin": origin || "*",
    "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type",
    "Access-Control-Max-Age": "86400",
  };
}

function json(body, status, origin) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json", ...corsHeaders(origin) },
  });
}

function clientIp(request) {
  return request.headers.get("CF-Connecting-IP") || "unknown";
}

function today() {
  return new Date().toISOString().slice(0, 10);
}

/** Sliding-ish window counter. Cheap, approximate, and deliberately so. */
async function rateLimited(env, ip) {
  const key = `rl:${ip}:${Math.floor(Date.now() / (IP_WINDOW_SECONDS * 1000))}`;
  const current = Number((await env.RATINGS.get(key)) || 0);
  if (current >= IP_MAX_REQUESTS) return true;
  // expirationTtl keeps these counters from accumulating; they are not the
  // aggregate and are allowed to be lossy.
  await env.RATINGS.put(key, String(current + 1), { expirationTtl: IP_WINDOW_SECONDS * 2 });
  return false;
}

async function votesUsed(env, ip, songId) {
  const key = `cap:${today()}:${ip}:${songId}`;
  const used = Number((await env.RATINGS.get(key)) || 0);
  return { key, used };
}

export default {
  async fetch(request, env) {
    const origin = request.headers.get("Origin") || "*";

    if (request.method === "OPTIONS") {
      return new Response(null, { status: 204, headers: corsHeaders(origin) });
    }

    if (!env.RATINGS) {
      // Loud, not silent: a missing binding means the deploy is misconfigured.
      return json({ error: "KV binding RATINGS is not configured" }, 500, origin);
    }

    if (request.method === "GET") {
      const raw = await env.RATINGS.get(AGGREGATE_KEY);
      const songs = raw ? JSON.parse(raw) : {};
      return json({ songs, key: AGGREGATE_KEY }, 200, origin);
    }

    if (request.method !== "POST") {
      return json({ error: "method not allowed" }, 405, origin);
    }

    const ip = clientIp(request);
    if (await rateLimited(env, ip)) {
      return json({ error: "rate limited" }, 429, origin);
    }

    let body;
    try {
      body = await request.json();
    } catch (err) {
      return json({ error: "body is not valid JSON" }, 400, origin);
    }

    const ratings = Array.isArray(body && body.ratings) ? body.ratings : null;
    if (!ratings || !ratings.length) {
      return json({ error: "expected {ratings: [...]}" }, 400, origin);
    }
    if (ratings.length > MAX_BATCH) {
      return json({ error: `batch larger than ${MAX_BATCH}` }, 413, origin);
    }

    const raw = await env.RATINGS.get(AGGREGATE_KEY);
    const songs = raw ? JSON.parse(raw) : {};

    let accepted = 0;
    const rejected = [];
    for (const entry of ratings) {
      const id = entry && typeof entry.id === "string" ? entry.id : null;
      const value = Number(entry && entry.rating);
      if (!id || !/^fma-\d{6}$/.test(id) || !VALID_RATINGS.has(value)) {
        rejected.push({ id, reason: "malformed id or rating outside 1..3" });
        continue;
      }
      const { key, used } = await votesUsed(env, ip, id);
      if (used >= PER_SONG_PER_IP_PER_DAY) {
        rejected.push({ id, reason: "per-song daily cap for this address" });
        continue;
      }
      await env.RATINGS.put(key, String(used + 1), { expirationTtl: 60 * 60 * 24 });

      const current = songs[id] || { n: 0, sum: 0 };
      songs[id] = { n: current.n + 1, sum: current.sum + value };
      accepted += 1;
    }

    if (accepted) {
      // Read-modify-write. Last write wins; see the header note.
      await env.RATINGS.put(AGGREGATE_KEY, JSON.stringify(songs));
    }

    return json({ accepted, rejected, songs_tracked: Object.keys(songs).length }, 200, origin);
  },
};
