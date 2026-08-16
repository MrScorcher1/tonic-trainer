# Tonic Trainer ratings Worker

A ~150-line Cloudflare Worker that collects player difficulty ratings and serves
the aggregate back. **The app works without it** — if this is missing,
unreachable, over quota or slow, the site falls back to the algorithmic
difficulty and keeps playing. A ratings outage must never break play.

## What the build agent could and could not do

| step | result |
|---|---|
| Install wrangler | **works** — `npx wrangler@latest` installs 4.123.0 (npm is allowlisted) |
| Bundle and validate the Worker | **works** — `wrangler deploy --dry-run` reports 4.02 KiB, KV binding wired |
| Reach `api.cloudflare.com` | **blocked** — `403`, `X-Proxy-Error: blocked-by-allowlist` |
| Create the KV namespace | **cannot** — an authenticated API call to the blocked host |
| Deploy | **cannot** — same |

Two reasons the user deploying is the *correct* arrangement rather than a
workaround, independent of any allowlist:

1. `wrangler` authenticates by browser OAuth (impossible headlessly) or by
   `CLOUDFLARE_API_TOKEN`. That token is the user's secret; the agent must never
   hold or ask for one.
2. Creating the KV namespace is the same authenticated call.

## Deploying — commands verified against `wrangler --help` (4.123.0)

```bash
cd worker
npx wrangler login                       # opens a browser; one time
npx wrangler kv namespace create RATINGS # prints the id and a TOML block
```

Paste the printed id into `wrangler.toml`, replacing
`REPLACE_WITH_KV_NAMESPACE_ID`, then:

```bash
npx wrangler deploy
```

`deploy` prints the Worker URL — something like
`https://tonic-trainer.<your-subdomain>.workers.dev`.

## The two values that come back, and where they go

| value | where it goes |
|---|---|
| **KV namespace id** (from `kv namespace create`) | `worker/wrangler.toml`, the `id` under `[[kv_namespaces]]` |
| **Worker URL** (from `deploy`) | rebuild the site with it: `TT_RATINGS_ENDPOINT="https://…workers.dev" .venv/bin/python -m tonic_trainer.static_build`, which writes it into `docs/config.json` as `ratings_endpoint` |

With `ratings_endpoint` empty — the default — the client never calls out, and
the difficulty shown is purely the computed prior.

## Design constraints worth not re-deriving later

**KV free tier: 100,000 reads/day but only 1,000 WRITES/day.** Writes are 100×
scarcer, which is why the client batches ~10 votes per request and why
everything lives under a single aggregate key (`aggregate:v1`) rather than one
key per song. Per-song keys would be 3,094 reads per page load; one key is one
read per session and one read-modify-write per flush. **Per-vote writes are a
design failure here, not an inefficiency** — they would cap the app at 1,000
ratings a day and then fail hard.

**A 10-vote batch costs 12 writes, not 1 — so the ceiling is ~830 votes/day.**
Batching does not reduce the write count to one per request, because two other
writes are per-something-else:

| write | how many per 10-vote POST |
|---|---|
| per-IP rate-limit counter (`index.js`, the 20/min guard) | 1 |
| per-song-per-IP-per-day cap, one key per accepted vote | 10 |
| the aggregate read-modify-write | 1 |
| **total** | **12** |

1,000 writes ÷ 12 per batch × 10 votes ≈ **833 ratings a day**, and the two
abuse guards are 11 of every 12 writes. An earlier version of this file claimed
batching bought ~10,000 ratings a day; that counted only the aggregate write and
was wrong by 12×. If the real ceiling ever binds, the fix is to make the guards
cheaper (in-memory or shorter-lived counters), not to batch harder — batching
larger only helps the one write in twelve that is actually per-request.

**Concurrent flushes are last-write-wins and can drop votes.** Accepted at this
scale; a queue or Durable Object is not worth the moving part. This is *not*
atomic and should not be described as such.

**The rate limiting is abuse reduction, not security.** Per-IP request limits
(20/min) and a per-song-per-IP-per-day cap (3) raise the cost of casual
ballot-stuffing. They do not stop a determined actor with many addresses, and
nothing here is secure.

## API

```
GET  /   -> {"songs": {"fma-000173": {"n": 4, "sum": 9}, …}, "key": "aggregate:v1"}
POST /   <- {"ratings": [{"id": "fma-000173", "rating": 2, "prior": 1}, …]}
         -> {"accepted": 9, "rejected": [...], "songs_tracked": 812}
```

Ratings outside 1–3 and ids not matching `fma-\d{6}` are rejected individually
and reported, never silently dropped.

## Statelessness

Aggregate per-song counts are **not per-user state**. Nothing identifies or
tracks a player, no history is kept, and every visitor is still new. The rule
the build spec draws (§0 rule 5) is about *user* state; this stays outside it.
