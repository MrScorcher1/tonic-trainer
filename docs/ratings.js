/* Player difficulty ratings: session memory, batched writes, fail-soft.
 *
 * WHY BATCHING IS NOT AN OPTIMISATION. Cloudflare's KV free tier allows 100,000
 * reads but only 1,000 WRITES per day — writes are 100x scarcer. One write per
 * vote would cap the whole app at 1,000 ratings a day and then fail hard. At ~10
 * votes per write that becomes ~10,000 ratings a day. Per-vote writes are a
 * design failure here, not an inefficiency.
 *
 * WHY THE EXIT FLUSH USES visibilitychange/pagehide AND NEVER beforeunload.
 * iOS Safari frequently never fires the unload events — it suspends the tab
 * instead. An exit flush built on beforeunload would silently lose most mobile
 * sessions, and this app must work on iPhone. Same class of iOS-specific trap as
 * the single-AudioContext rule, and just as invisible if you get it wrong.
 * sendBeacon is used because a normal fetch() is CANCELLED when the page goes
 * away; the browser takes ownership of a beacon and delivers it afterwards.
 *
 * WHAT IS ACCEPTED RATHER THAN ENGINEERED AROUND:
 *  - A beacon's response cannot be read, so the client never confirms a write
 *    landed. Fail-soft covers it.
 *  - Concurrent flushes on the server are last-write-wins and can drop votes.
 *    At this scale that is rare and cheap; this is NOT atomic and must not be
 *    described as such.
 *  - A hard crash before any flush loses at most (batch - 1) ratings, never a
 *    whole session. That bound is why this does not need to be more elaborate.
 *
 * STATELESSNESS. Ratings live in JavaScript memory for the session and are lost
 * on reload — nothing is written to localStorage, sessionStorage or cookies.
 * Aggregate per-song counts on the server are not per-user state: nothing
 * identifies or tracks a player, and every visitor is still new.
 *
 * WHAT THESE RATINGS CAN AND CANNOT SHOW. The computed difficulty is displayed
 * from the moment a puzzle loads (the user retired the guard that hid it until
 * after rating). Every rater therefore sees the algorithm's answer first, which
 * splits the data in two:
 *   - DISAGREEMENT survives the anchor. A song players consistently call 3
 *     against a computed 1 is genuine signal, because the anchor pushed the
 *     other way.
 *   - AGREEMENT does not. A vote matching the prior may simply be repeating it.
 * So this system can reliably FLAG INDIVIDUAL BADLY-MISRATED SONGS, and that is
 * the only claim it supports. It does not validate the difficulty model, and any
 * aggregate "players agree with the algorithm N% of the time" figure is partly
 * an artifact of the display, not evidence. Do not compute or publish one.
 */

(() => {
  "use strict";

  const MAX_PENDING = 100;   // a broken endpoint must not grow memory forever
  const CLAMP_MIN = 1;
  const CLAMP_MAX = 3;

  const state = {
    endpoint: "",
    batchSize: 10,
    priorWeight: 5,
    aggregate: {},        // {song_id: {n, sum}} — fetched once per session
    aggregateLoaded: false,
    pending: [],          // not yet sent
    session: [],          // everything rated this session, for inspection/export
    flushes: 0,
    lastError: null,
  };

  /**
   * Bayesian shrinkage of the algorithmic prior toward the player votes.
   *
   *     displayed = round((w * prior + sum(votes)) / (w + n))    clamped 1..3
   *
   * w is the prior's weight in pseudo-votes. At w=5, one vote barely moves the
   * rating, five carry equal weight to the algorithm, twenty essentially
   * override it — a single dissenter cannot flip a song, sustained disagreement
   * can. w IS A REASONED GUESS, NOT A CALIBRATED PARAMETER; it cannot be
   * calibrated until votes exist.
   */
  function shrink(prior, n, sum, weight) {
    const w = typeof weight === "number" ? weight : state.priorWeight;
    if (!n) return prior;                       // n=0 must return exactly the prior
    const value = Math.round((w * prior + sum) / (w + n));
    return Math.min(CLAMP_MAX, Math.max(CLAMP_MIN, value));
  }

  /** prior, n and displayed stay separate so "algorithm said" vs "players said" stays auditable. */
  function rating(puzzleId, prior) {
    const agg = state.aggregate[puzzleId];
    const n = agg ? agg.n : 0;
    const sum = agg ? agg.sum : 0;
    return { prior, n, sum, displayed: shrink(prior, n, sum) };
  }

  async function loadAggregate() {
    if (!state.endpoint) return;   // no service configured: the prior stands alone
    try {
      const resp = await fetch(state.endpoint, { method: "GET" });
      if (!resp.ok) throw new Error(`ratings GET returned ${resp.status}`);
      const body = await resp.json();
      state.aggregate = body && typeof body === "object" ? (body.songs || body) : {};
      state.aggregateLoaded = true;
    } catch (err) {
      // Fail soft: a ratings outage must never break play. The app falls back to
      // the algorithmic difficulty, which is always present in the manifest.
      state.lastError = String(err);
    }
  }

  function record(puzzleId, value, meta) {
    const entry = {
      id: puzzleId,
      rating: Number(value),
      prior: meta && meta.prior,
      tonic_margin: meta && meta.tonic_margin,
      mode_margin: meta && meta.mode_margin,
      t: Date.now(),
    };
    state.session.push(entry);
    state.pending.push(entry);
    if (state.pending.length > MAX_PENDING) {
      state.pending.splice(0, state.pending.length - MAX_PENDING);
    }
    if (state.pending.length >= state.batchSize) flush(false);
    return entry;
  }

  /**
   * Send the pending batch. `viaBeacon` is used on the way out, where a normal
   * fetch would be cancelled with the page.
   */
  function flush(viaBeacon) {
    if (!state.pending.length || !state.endpoint) return false;
    const batch = state.pending.splice(0, state.pending.length);
    const payload = JSON.stringify({ ratings: batch });
    state.flushes += 1;

    if (viaBeacon && navigator.sendBeacon) {
      // Response is unreadable by design; fail-soft already covers not knowing.
      navigator.sendBeacon(state.endpoint, new Blob([payload], { type: "application/json" }));
      return true;
    }

    fetch(state.endpoint, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: payload,
      keepalive: true,
    }).catch((err) => {
      state.lastError = String(err);   // fail soft; the ratings are simply lost
    });
    return true;
  }

  function init(config) {
    state.endpoint = (config && config.ratings_endpoint) || "";
    state.batchSize = (config && config.ratings_batch_size) || 10;
    state.priorWeight = (config && typeof config.ratings_prior_weight === "number")
      ? config.ratings_prior_weight : 5;

    // Exit flush. visibilitychange + pagehide ONLY — never beforeunload/unload,
    // which iOS Safari frequently does not fire.
    document.addEventListener("visibilitychange", () => {
      if (document.visibilityState === "hidden") flush(true);
    });
    window.addEventListener("pagehide", () => flush(true));

    return loadAggregate();
  }

  window.TTRatings = {
    init,
    record,
    flush,
    rating,
    shrink,
    // Inspection for the gates and for a curious player; not persistence.
    debug: () => ({
      endpoint: state.endpoint,
      batchSize: state.batchSize,
      priorWeight: state.priorWeight,
      pending: state.pending.slice(),
      session: state.session.slice(),
      flushes: state.flushes,
      aggregateLoaded: state.aggregateLoaded,
      lastError: state.lastError,
    }),
  };
})();
