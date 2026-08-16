/* Tonic Trainer — front panel logic.
 *
 * Three rules hold this file together, and breaking any of them ships
 * something that is silently broken on a phone:
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
 * State lives in this closure and nowhere else: no localStorage, no cookies,
 * no server-side session. Reload and the unit forgets you (SPEC §0.5).
 */

(() => {
  "use strict";

  const NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"];
  const WHITE_PCS = [0, 2, 4, 5, 7, 9, 11];
  // Black-key offsets as a fraction of keyboard width, keyed by pitch class.
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

  const el = (id) => document.getElementById(id);
  const dom = {
    unit: el("unit"),
    title: el("track-title"),
    artist: el("track-artist"),
    license: el("track-license"),
    attribution: el("attribution"),
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

  /** All mutable state. In memory only — this object is never persisted. */
  const state = {
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
    stats: { attempts: 0, correct: 0, buckets: {} },
  };

  /** Web Audio graph. Created lazily, inside a gesture. */
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

  /**
   * Create the single AudioContext. MUST be called synchronously from a user
   * gesture handler — never after an await, never on load.
   */
  function ensureContext() {
    if (audio.ctx) {
      if (audio.ctx.state === "suspended") audio.ctx.resume();
      return audio.ctx;
    }
    const Ctor = window.AudioContext || window.webkitAudioContext;
    // Recorded for the Gate 6 invariant: was there a live user event on the
    // stack when the context came into being?
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
    audio.clipGain.gain.value = clip * clip;   // perceptual-ish taper
    audio.droneGain.gain.value = drone * drone * 0.5;
  }

  function stopClip() {
    if (audio.clipSource) {
      audio.clipSource.onended = null;
      // Only sources we started can be stopped; a never-started source would
      // throw InvalidStateError, so track it rather than swallowing the error.
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
    source.loop = state.looping;      // gapless seam; no restart handler
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
      { f: freq / 2, type: "sine", g: 0.32 },   // octave below for body
      { f: freq * 2, type: "triangle", g: 0.08 }, // a little edge to find by ear
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
        // No try/catch: the ramp target and the current value are both clamped
        // positive (exponentialRamp rejects zero), and re-stopping a stopped
        // oscillator is legal, so none of these calls can throw.
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
   *   idle          — not selected, not sounding
   *   auditioning   — sounding right now (this is the pitch in your ear)
   *   committed     — armed for CHECK, not currently sounding
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

  function apiBase() {
    // The server may serve under a random path prefix in exposed modes; the
    // page is served from that same prefix, so derive it from our own URL.
    const path = window.location.pathname.replace(/\/[^/]*$/, "");
    return path === "/" ? "" : path;
  }

  async function loadPuzzle() {
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
    renderKeys();
    setStatus("LOADING …");

    const tier = dom.tier.value;
    const resp = await fetch(`${apiBase()}/api/puzzle?tier=${encodeURIComponent(tier)}`);
    if (!resp.ok) {
      setStatus(`LOAD FAILED (${resp.status})`);
      return;
    }
    const puzzle = await resp.json();
    state.puzzle = puzzle;

    dom.title.textContent = puzzle.title;
    dom.artist.textContent = puzzle.artist;
    dom.license.textContent = puzzle.license;
    dom.attribution.textContent =
      `Audio: “${puzzle.title}” by ${puzzle.artist} — licensed ${puzzle.license}. ` +
      `Source: Free Music Archive. Key annotations: fma_keys (FMAK).`;
    dom.tierReadout.textContent = (puzzle.difficulty || tier).toUpperCase();
    setStatus("PRESS PLAY TO START");

    // Bytes are fetched now; decoding waits for the AudioContext, which cannot
    // exist until the user gestures.
    const audioResp = await fetch(`${apiBase()}${puzzle.audio_url}`);
    if (!audioResp.ok) {
      setStatus(`AUDIO FAILED (${audioResp.status})`);
      return;
    }
    state.clipBytes = await audioResp.arrayBuffer();
    if (audio.ctx) await decodeClip();
  }

  async function decodeClip() {
    if (!state.clipBytes || !audio.ctx || state.clipBuffer) return;
    // decodeAudioData detaches the buffer, so decode a copy: a second decode
    // (after a context restart) would otherwise get an empty ArrayBuffer.
    const copy = state.clipBytes.slice(0);
    state.clipBuffer = await audio.ctx.decodeAudioData(copy);
  }

  async function submitGuess() {
    if (state.committedPc === null || !state.puzzle || state.answered) return;
    const body = {
      id: state.puzzle.id,
      tonic_pc: state.committedPc,
      mode: state.mode,
    };
    const resp = await fetch(`${apiBase()}/api/guess`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!resp.ok) {
      setStatus(`CHECK FAILED (${resp.status})`);
      return;
    }
    const verdict = await resp.json();
    state.answered = true;
    state.lastGuess = body;

    state.stats.attempts += 1;
    if (verdict.correct) state.stats.correct += 1;
    const bucket = verdict.relative_error;
    state.stats.buckets[bucket] = (state.stats.buckets[bucket] || 0) + 1;
    renderStats();

    dom.result.hidden = false;
    dom.resultBadge.textContent = verdict.correct ? "CORRECT" : bucket.toUpperCase();
    dom.resultBadge.dataset.ok = String(verdict.correct);
    dom.resultKey.textContent = verdict.key_display;
    dom.resultText.textContent = verdict.explanation;
    dom.check.disabled = true;
    setStatus(verdict.correct ? "LOCKED IT" : "NOT QUITE — TRY THE NEXT ONE");
  }

  async function flagAnswer() {
    if (!state.lastGuess) return;
    dom.flag.disabled = true;
    dom.flag.textContent = "FLAGGING …";
    const resp = await fetch(`${apiBase()}/api/dispute`, {
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
      // Tapping the sounding key silences it but keeps it armed — the
      // committed state stays visible on its own.
      stopDrone(true);
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
      stats: JSON.parse(JSON.stringify(state.stats)),
      sameContextForClipAndDrone:
        Boolean(audio.ctx) &&
        (!audio.clipSource || audio.clipSource.context === audio.ctx) &&
        (!audio.droneOscillators.length || audio.droneOscillators[0].osc.context === audio.ctx),
    }),
    endedHandlerSource: handleClipEnded.toString(),
    reload: loadPuzzle,
  };

  // Test-only: lets the suite reproduce a backgrounded tab, which headless
  // browsers will not do on their own.
  window.__ttSuspendForTest = () => (audio.ctx ? audio.ctx.suspend() : Promise.resolve());

  /* ----------------------------------------------------------------- start */

  buildPiano();
  renderKeys();
  renderStats();
  fetch(`${apiBase()}/api/health`)
    .then((r) => r.json())
    .then((h) => { dom.poolReadout.textContent = `${h.pool} CLIPS IN POOL`; })
    .catch(() => { dom.poolReadout.textContent = "POOL UNAVAILABLE"; });
  loadPuzzle();
})();
