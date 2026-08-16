/* Tonic Trainer — front panel logic.
 *
 * This runs as a PURE STATIC SITE: no server, no API. The manifest is a static
 * asset, puzzle selection and scoring happen here, and audio streams from the
 * Hugging Face CDN. The FastAPI server still exists but is demoted to a fallback
 * (see README) for the one case we do not control: HF withdrawing CORS.
 *
 * Three rules hold this file together, and breaking any of them ships something
 * that is silently broken on a phone:
 *
 * 1. ONE audio path. The clip and the drone are both Web Audio nodes on the
 *    same AudioContext. No <audio> element, ever. On iOS the element path and
 *    the Web Audio path behave differently under the hardware silent switch, so
 *    a split path can leave one audible and the other mute — fatal for an app
 *    whose whole interaction is hearing both at once.
 * 2. The AudioContext is constructed INSIDE a user-gesture handler, and
 *    synchronously — no await before it. iOS refuses to start audio otherwise.
 * 3. Looping is AudioBufferSourceNode.loop = true, not an `ended` handler that
 *    starts a new source. A restart handler gives an audible gap at the seam.
 *
 * State lives in this closure and nowhere else: no localStorage, no cookies, no
 * server-side session. Reload and the unit forgets you.
 */

(() => {
  "use strict";

  const NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"];
  const WHITE_PCS = [0, 2, 4, 5, 7, 9, 11];
  const BLACK_LAYOUT = [
    { pc: 1, left: 10.2 },
    { pc: 3, left: 24.0 },
    { pc: 6, left: 52.0 },
    { pc: 8, left: 66.0 },
    { pc: 10, left: 80.0 },
  ];
  const A4 = 440;
  const DRONE_ATTACK = 0.04;
  const DRONE_RELEASE = 0.08;
  const TAGGED_TIERS = ["tier1", "tier2", "tier3"];

  const el = (id) => document.getElementById(id);
  const dom = {
    title: el("track-title"),
    artist: el("track-artist"),
    license: el("track-license"),
    attribution: el("attribution"),
    error: el("error"),
    status: el("status-line"),
    tierReadout: el("tier-readout"),
    poolReadout: el("pool-readout"),
    play: el("btn-play"),
    loop: el("btn-loop"),
    tier: el("tier-select"),
    mixClip: el("mix-clip"),
    mixDrone: el("mix-drone"),
    mixClipOut: el("mix-clip-out"),
    mixDroneOut: el("mix-drone-out"),
    piano: el("piano"),
    armed: el("armed-readout"),
    droneOff: el("btn-drone-off"),
    major: el("btn-major"),
    minor: el("btn-minor"),
    check: el("btn-check"),
    next: el("btn-next"),
    result: el("result"),
    resultBadge: el("result-badge"),
    resultKey: el("result-key"),
    resultText: el("result-text"),
    flag: el("btn-flag"),
    statsGrid: el("stats-grid"),
    lampPlay: el("lamp-play"),
    lampDrone: el("lamp-drone"),
  };

  /** All mutable state. In memory only — never persisted. */
  const state = {
    config: null,
    entries: [],
    puzzle: null,
    clipBuffer: null,
    clipBytes: null,
    playing: false,
    looping: true,
    committedPc: null,
    auditioningPc: null,
    mode: "major",
    answered: false,
    lastGuess: null,
    answerRequests: 0,
    stats: { attempts: 0, correct: 0, buckets: {} },
  };

  const audio = {
    ctx: null,
    clipGain: null,
    droneGain: null,
    clipSource: null,
    clipStarted: false,
    droneOscillators: [],
    createdDuringGesture: null,
  };

  const pcToFreq = (pc) => A4 * Math.pow(2, (pc + 3) / 12) / 2; // C4-based octave

  /* ---------------------------------------------------------------- audio */

  function ensureContext() {
    if (audio.ctx) {
      if (audio.ctx.state === "suspended") audio.ctx.resume();
      return audio.ctx;
    }
    const Ctor = window.AudioContext || window.webkitAudioContext;
    audio.createdDuringGesture = Boolean(window.event);
    audio.ctx = new Ctor();
    audio.clipGain = audio.ctx.createGain();
    audio.droneGain = audio.ctx.createGain();
    audio.clipGain.connect(audio.ctx.destination);
    audio.droneGain.connect(audio.ctx.destination);
    applyMix();
    if (audio.ctx.state === "suspended") audio.ctx.resume();
    return audio.ctx;
  }

  function applyMix() {
    if (!audio.ctx) return;
    const clip = Number(dom.mixClip.value) / 100;
    const drone = Number(dom.mixDrone.value) / 100;
    audio.clipGain.gain.value = clip * clip;
    audio.droneGain.gain.value = drone * drone * 0.5;
  }

  function stopClip() {
    if (audio.clipSource) {
      audio.clipSource.onended = null;
      if (audio.clipStarted) audio.clipSource.stop();
      audio.clipSource.disconnect();
      audio.clipSource = null;
      audio.clipStarted = false;
    }
    state.playing = false;
    dom.play.classList.remove("is-playing");
    dom.play.querySelector(".btn__text").textContent = "PLAY";
    dom.lampPlay.dataset.on = "false";
  }

  /** The clip's `ended` handler. It updates the panel; it never restarts audio. */
  function handleClipEnded() {
    state.playing = false;
    dom.play.classList.remove("is-playing");
    dom.play.querySelector(".btn__text").textContent = "PLAY";
    dom.lampPlay.dataset.on = "false";
    setStatus("CLIP ENDED — PRESS PLAY");
  }

  function startClip() {
    if (!audio.ctx || !state.clipBuffer) return;
    stopClip();
    const source = audio.ctx.createBufferSource();
    source.buffer = state.clipBuffer;
    source.loop = state.looping;
    source.connect(audio.clipGain);
    source.onended = handleClipEnded;
    source.start();
    audio.clipSource = source;
    audio.clipStarted = true;
    state.playing = true;
    dom.play.classList.add("is-playing");
    dom.play.querySelector(".btn__text").textContent = "PAUSE";
    dom.lampPlay.dataset.on = "true";
    setStatus(state.looping ? "LOOPING" : "PLAYING ONCE");
  }

  function startDrone(pc) {
    stopDrone(true);
    const ctx = audio.ctx;
    const now = ctx.currentTime;
    const voice = ctx.createGain();
    voice.gain.setValueAtTime(0.0001, now);
    voice.gain.exponentialRampToValueAtTime(1, now + DRONE_ATTACK);
    voice.connect(audio.droneGain);

    const freq = pcToFreq(pc);
    const specs = [
      { f: freq, type: "sine", g: 0.6 },
      { f: freq / 2, type: "sine", g: 0.32 },
      { f: freq * 2, type: "triangle", g: 0.08 },
    ];
    audio.droneOscillators = specs.map((spec) => {
      const osc = ctx.createOscillator();
      const g = ctx.createGain();
      osc.type = spec.type;
      osc.frequency.setValueAtTime(spec.f, now);
      g.gain.setValueAtTime(spec.g, now);
      osc.connect(g);
      g.connect(voice);
      osc.start();
      return { osc, gain: g, voice };
    });
    state.auditioningPc = pc;
    dom.lampDrone.dataset.on = "true";
  }

  function stopDrone(silent) {
    if (audio.droneOscillators.length && audio.ctx) {
      const now = audio.ctx.currentTime;
      audio.droneOscillators.forEach(({ osc, voice }) => {
        // No try/catch: the ramp target and current value are clamped positive
        // (exponentialRamp rejects zero) and re-stopping a stopped oscillator is
        // legal, so none of these can throw.
        voice.gain.cancelScheduledValues(now);
        voice.gain.setValueAtTime(Math.max(voice.gain.value, 0.0001), now);
        voice.gain.exponentialRampToValueAtTime(0.0001, now + DRONE_RELEASE);
        osc.stop(now + DRONE_RELEASE + 0.01);
      });
    }
    audio.droneOscillators = [];
    state.auditioningPc = null;
    dom.lampDrone.dataset.on = "false";
    if (!silent) renderKeys();
  }

  /* ------------------------------------------------------------- rendering */

  function setStatus(text) {
    dom.status.textContent = text;
  }

  /**
   * Surface a failure instead of looking like a broken puzzle.
   *
   * The realistic failures are all external and all invisible by default: HF
   * withdrawing Access-Control-Allow-Origin, an HF outage, or the /resolve/
   * rate limit (3000 requests per 5 minutes per IP). Each produces a fetch that
   * simply does not return audio, so without this the unit would sit there
   * looking broken with no cause named.
   */
  function showError(headline, detail) {
    dom.error.hidden = false;
    dom.error.textContent = `${headline} ${detail}`;
    setStatus("AUDIO UNAVAILABLE");
  }

  function clearError() {
    dom.error.hidden = true;
    dom.error.textContent = "";
  }

  function buildPiano() {
    dom.piano.textContent = "";
    WHITE_PCS.forEach((pc) => {
      const key = document.createElement("button");
      key.type = "button";
      key.className = "key key--white";
      key.dataset.pc = String(pc);
      key.dataset.state = "idle";
      key.dataset.armed = "false";
      key.setAttribute("aria-label", `${NOTE_NAMES[pc]} — drone and arm`);
      key.textContent = NOTE_NAMES[pc];
      dom.piano.appendChild(key);
    });
    BLACK_LAYOUT.forEach(({ pc, left }) => {
      const key = document.createElement("button");
      key.type = "button";
      key.className = "key key--black";
      key.dataset.pc = String(pc);
      key.dataset.state = "idle";
      key.dataset.armed = "false";
      key.style.left = `${left}%`;
      key.setAttribute("aria-label", `${NOTE_NAMES[pc]} — drone and arm`);
      key.textContent = NOTE_NAMES[pc];
      dom.piano.appendChild(key);
    });
  }

  /**
   * Three distinct key states, so exploring never looks like submitting:
   *   idle | auditioning (sounding now) | committed (armed for CHECK).
   * The armed marker is a separate attribute so both markers are observable.
   */
  function renderKeys() {
    dom.piano.querySelectorAll(".key").forEach((key) => {
      const pc = Number(key.dataset.pc);
      const armed = pc === state.committedPc;
      const sounding = pc === state.auditioningPc;
      key.dataset.state = sounding ? "auditioning" : armed ? "committed" : "idle";
      key.dataset.armed = armed ? "true" : "false";
    });
    dom.armed.textContent =
      state.committedPc === null ? "—" : `${NOTE_NAMES[state.committedPc]} ${state.mode.toUpperCase()}`;
    dom.check.disabled = state.committedPc === null || state.answered;
  }

  function renderStats() {
    const { attempts, correct, buckets } = state.stats;
    const pct = attempts ? Math.round((correct / attempts) * 100) : 0;
    const cells = [
      ["ATTEMPTS", attempts],
      ["ACCURACY", `${pct}%`],
      ["RELATIVE", buckets.relative || 0],
      ["PARALLEL", buckets.parallel || 0],
      ["SEMITONE", buckets.semitone || 0],
      ["FIFTH", buckets.fifth || 0],
      ["OTHER", buckets.other || 0],
    ];
    dom.statsGrid.textContent = "";
    cells.forEach(([k, v]) => {
      const box = document.createElement("div");
      box.className = "stat";
      const key = document.createElement("span");
      key.className = "stat__k";
      key.textContent = k;
      const val = document.createElement("span");
      val.className = "stat__v";
      val.textContent = String(v);
      box.append(key, val);
      dom.statsGrid.appendChild(box);
    });
  }

  /* ------------------------------------------------------------- puzzle IO */

  function poolFor(tier) {
    if (tier === "tagged") return state.entries.filter((e) => TAGGED_TIERS.includes(e.difficulty));
    if (!tier || tier === "any") return state.entries;
    return state.entries.filter((e) => e.difficulty === tier);
  }

  /** Unbiased pick from a cryptographic source — no seeded sequence, no memory. */
  function pickRandom(list) {
    if (!list.length) return null;
    const limit = Math.floor(0xffffffff / list.length) * list.length;
    const buf = new Uint32Array(1);
    let value;
    do {
      crypto.getRandomValues(buf);
      [value] = buf;
    } while (value >= limit);
    return list[value % list.length];
  }

  /**
   * Build the clip URL.
   *
   * ALWAYS the `resolve/` URL, never what it redirects to. Hugging Face answers
   * `resolve/` with a 302 to a CDN URL that is SIGNED — it carries an Expires
   * timestamp plus a Policy/Signature pair. Caching or persisting that resolved
   * URL anywhere works perfectly until the signature expires and then breaks
   * playback for everyone, so the redirect must be followed fresh every time.
   */
  function audioUrl(entry) {
    const base = (state.config && state.config.audio_base) || "";
    if (!base) return `audio/${entry.audio_path}`;   // demoted local server
    return `${base}/${entry.audio_path}`;
  }

  async function loadCorpus() {
    const [config, manifest] = await Promise.all([
      fetch("config.json").then((r) => r.json()),
      fetch("manifest.json").then((r) => {
        if (!r.ok) throw new Error(`manifest.json returned ${r.status}`);
        return r.json();
      }),
    ]);
    state.config = config;
    state.entries = manifest;
    dom.poolReadout.textContent = `${manifest.length} CLIPS IN POOL`;
    // Disputes need the operator-side log, which only the fallback server has.
    if (!config.api) dom.flag.hidden = true;
  }

  async function loadPuzzle() {
    const wasPlaying = state.playing;
    stopClip();
    stopDrone(true);
    state.puzzle = null;
    state.clipBuffer = null;
    state.clipBytes = null;
    state.committedPc = null;
    state.auditioningPc = null;
    state.answered = false;
    state.lastGuess = null;
    dom.result.hidden = true;
    dom.check.disabled = true;
    dom.flag.disabled = false;
    dom.flag.textContent = "FLAG THIS ANSWER";
    clearError();
    renderKeys();
    setStatus("LOADING …");

    const pool = poolFor(dom.tier.value);
    const puzzle = pickRandom(pool);
    if (!puzzle) {
      showError("NO PUZZLES IN THIS POOL.", "Pick another setting.");
      return;
    }
    state.puzzle = puzzle;

    dom.title.textContent = puzzle.title;
    dom.artist.textContent = puzzle.artist;
    dom.license.textContent = puzzle.license;
    dom.attribution.textContent =
      `Audio: “${puzzle.title}” by ${puzzle.artist} — licensed ${puzzle.license}. ` +
      `Source: Free Music Archive. Key annotations: fma_keys (FMAK).`;
    dom.tierReadout.textContent = (puzzle.difficulty || "").toUpperCase();
    setStatus("PRESS PLAY TO START");

    const url = audioUrl(puzzle);
    let response;
    try {
      response = await fetch(url);
    } catch (err) {
      showError(
        "COULD NOT REACH THE AUDIO HOST.",
        "The clips stream from the Hugging Face CDN. Likely causes: you are offline, " +
        "the CDN stopped sending an Access-Control-Allow-Origin header, or its rate " +
        "limit (3000 requests / 5 min per IP) was hit. The local fallback server in " +
        `this project can serve the clips instead. (${err.message})`,
      );
      return;
    }
    if (!response.ok) {
      showError(
        `THE AUDIO HOST RETURNED ${response.status}.`,
        response.status === 429
          ? "That is the Hugging Face rate limit (3000 requests / 5 min per IP). Wait a few minutes."
          : "The clip may have moved, or the dataset is unavailable. The local fallback server can serve the clips instead.",
      );
      return;
    }

    state.clipBytes = await response.arrayBuffer();
    if (audio.ctx) {
      await decodeClip();
      if (wasPlaying) startClip();
    }
  }

  async function decodeClip() {
    if (!state.clipBytes || !audio.ctx || state.clipBuffer) return;
    // decodeAudioData detaches the buffer, so decode a copy: a second decode
    // would otherwise get an empty ArrayBuffer.
    const copy = state.clipBytes.slice(0);
    state.clipBuffer = await audio.ctx.decodeAudioData(copy);
  }

  /**
   * Score the guess entirely in the browser.
   *
   * The answer file is fetched HERE and nowhere else — not on load, not on
   * puzzle selection. That is what makes "peeking requires intent" true rather
   * than merely claimed: until you commit, no request for the answer exists.
   */
  async function submitGuess() {
    if (state.committedPc === null || !state.puzzle || state.answered) return;
    const guess = { id: state.puzzle.id, tonic_pc: state.committedPc, mode: state.mode };

    let verifier;
    try {
      const resp = await fetch(`answers/${state.puzzle.id}.json`);
      if (!resp.ok) throw new Error(`answers/${state.puzzle.id}.json returned ${resp.status}`);
      verifier = (await resp.json()).h;
    } catch (err) {
      showError("COULD NOT LOAD THE ANSWER FOR THIS PUZZLE.", err.message);
      return;
    }

    const answer = await window.TTScoring.recoverAnswer(state.puzzle.id, verifier);
    const bucket = window.TTScoring.classify(
      guess.tonic_pc, guess.mode, answer.tonic_pc, answer.mode,
    );
    const keyDisplay = window.TTScoring.displayKey(answer.tonic_pc, answer.mode);
    const guessDisplay = window.TTScoring.displayKey(guess.tonic_pc, guess.mode);

    state.answered = true;
    state.lastGuess = guess;
    state.stats.attempts += 1;
    if (bucket === "exact") state.stats.correct += 1;
    state.stats.buckets[bucket] = (state.stats.buckets[bucket] || 0) + 1;
    renderStats();

    dom.result.hidden = false;
    dom.resultBadge.textContent = bucket === "exact" ? "CORRECT" : bucket.toUpperCase();
    dom.resultBadge.dataset.ok = String(bucket === "exact");
    dom.resultKey.textContent = keyDisplay;
    dom.resultText.textContent = window.TTScoring.explain(bucket, keyDisplay, guessDisplay);
    dom.check.disabled = true;
    setStatus(bucket === "exact" ? "LOCKED IT" : "NOT QUITE — TRY THE NEXT ONE");
  }

  async function flagAnswer() {
    if (!state.lastGuess || !state.config || !state.config.api) return;
    dom.flag.disabled = true;
    dom.flag.textContent = "FLAGGING …";
    const resp = await fetch("api/dispute", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(state.lastGuess),
    });
    dom.flag.textContent = resp.ok ? "FLAGGED — THANK YOU" : `FLAG FAILED (${resp.status})`;
  }

  /* ---------------------------------------------------------------- events */

  dom.play.addEventListener("click", async () => {
    ensureContext();                     // synchronous, inside the gesture
    if (state.playing) {
      stopClip();
      setStatus("PAUSED");
      return;
    }
    if (!state.clipBuffer) {
      setStatus("DECODING …");
      await decodeClip();
    }
    if (!state.clipBuffer) {
      setStatus("NO AUDIO LOADED");
      return;
    }
    startClip();
  });

  dom.loop.addEventListener("click", () => {
    state.looping = !state.looping;
    dom.loop.classList.toggle("is-on", state.looping);
    dom.loop.setAttribute("aria-pressed", String(state.looping));
    if (audio.clipSource) audio.clipSource.loop = state.looping;
    setStatus(state.looping ? "LOOP ON" : "LOOP OFF");
  });

  dom.piano.addEventListener("click", (event) => {
    const key = event.target.closest(".key");
    if (!key) return;
    ensureContext();                     // synchronous, inside the gesture
    const pc = Number(key.dataset.pc);
    if (pc === state.auditioningPc) {
      stopDrone(true);                   // silence it, keep it armed
    } else {
      state.committedPc = pc;
      startDrone(pc);
    }
    renderKeys();
  });

  dom.droneOff.addEventListener("click", () => {
    stopDrone(true);
    renderKeys();
  });

  [dom.major, dom.minor].forEach((btn) => {
    btn.addEventListener("click", () => {
      state.mode = btn === dom.major ? "major" : "minor";
      dom.major.classList.toggle("is-on", state.mode === "major");
      dom.minor.classList.toggle("is-on", state.mode === "minor");
      dom.major.setAttribute("aria-pressed", String(state.mode === "major"));
      dom.minor.setAttribute("aria-pressed", String(state.mode === "minor"));
      renderKeys();
    });
  });

  dom.check.addEventListener("click", submitGuess);
  dom.next.addEventListener("click", () => { loadPuzzle(); });
  dom.flag.addEventListener("click", flagAnswer);
  dom.tier.addEventListener("change", () => { loadPuzzle(); });

  [dom.mixClip, dom.mixDrone].forEach((slider) => {
    slider.addEventListener("input", () => {
      dom.mixClipOut.value = dom.mixClip.value;
      dom.mixDroneOut.value = dom.mixDrone.value;
      applyMix();
    });
  });

  // Returning to a backgrounded tab leaves the context suspended on iOS; resume
  // it rather than presenting a dead unit.
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden && audio.ctx && audio.ctx.state === "suspended") {
      audio.ctx.resume();
    }
  });

  /* ------------------------------------------------------------ test hooks */

  window.__tt = {
    getState: () => ({
      hasContext: Boolean(audio.ctx),
      contextState: audio.ctx ? audio.ctx.state : null,
      createdDuringGesture: audio.createdDuringGesture,
      playing: state.playing,
      looping: state.looping,
      clipLoopFlag: audio.clipSource ? audio.clipSource.loop : null,
      droneRunning: audio.droneOscillators.length > 0,
      droneVoices: audio.droneOscillators.length,
      droneFrequency: audio.droneOscillators.length
        ? audio.droneOscillators[0].osc.frequency.value
        : null,
      auditioningPc: state.auditioningPc,
      committedPc: state.committedPc,
      mode: state.mode,
      answered: state.answered,
      puzzleId: state.puzzle ? state.puzzle.id : null,
      poolSize: state.entries.length,
      stats: JSON.parse(JSON.stringify(state.stats)),
      sameContextForClipAndDrone:
        Boolean(audio.ctx) &&
        (!audio.clipSource || audio.clipSource.context === audio.ctx) &&
        (!audio.droneOscillators.length || audio.droneOscillators[0].osc.context === audio.ctx),
    }),
    endedHandlerSource: handleClipEnded.toString(),
    reload: loadPuzzle,
  };

  window.__ttSuspendForTest = () => (audio.ctx ? audio.ctx.suspend() : Promise.resolve());

  /* ----------------------------------------------------------------- start */

  buildPiano();
  renderKeys();
  renderStats();
  loadCorpus()
    .then(loadPuzzle)
    .catch((err) => showError("COULD NOT LOAD THE PUZZLE LIST.", err.message));
})();
