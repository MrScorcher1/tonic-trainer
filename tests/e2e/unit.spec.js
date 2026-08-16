/* Gate 6 — the front panel, on headless WebKit at desktop and iPhone viewports.
 *
 * These are the checks that cannot be made by reading the code: that the
 * AudioContext is born inside a gesture, that the clip and the drone share it,
 * that exploring the keyboard is visually distinct from arming a guess, and
 * that nothing about a session survives a reload.
 */

const { test, expect } = require("@playwright/test");
const { serveLocalClips } = require("./helpers");

const KEY_NAME_IN_TEXT = /[A-G][#b]? (Major|minor)/;

/** Records every AudioContext construction and whether a user event was live. */
async function instrument(page) {
  await page.addInitScript(() => {
    window.__ctxConstructions = [];
    window.__audioElements = 0;
    const wrap = (Ctor) =>
      new Proxy(Ctor, {
        construct(target, args) {
          window.__ctxConstructions.push({
            hasWindowEvent: Boolean(window.event),
            eventType: window.event ? window.event.type : null,
            stack: new Error().stack,
          });
          return Reflect.construct(target, args);
        },
      });
    if (window.AudioContext) window.AudioContext = wrap(window.AudioContext);
    if (window.webkitAudioContext) window.webkitAudioContext = wrap(window.webkitAudioContext);
    if (window.Audio) {
      window.Audio = new Proxy(window.Audio, {
        construct(target, args) {
          window.__audioElements += 1;
          return Reflect.construct(target, args);
        },
      });
    }
  });
}

async function loadUnit(page) {
  await instrument(page);
  await serveLocalClips(page);
  await page.goto("/");
  await expect(page.locator("#track-title")).not.toHaveText("—", { timeout: 20_000 });
  await page.waitForFunction(() => window.__tt && window.__tt.getState().puzzleId !== null);
}

test.beforeEach(async ({ page }) => {
  await loadUnit(page);
});

test("no AudioContext exists until the user gestures, then it is born in the handler", async ({ page }) => {
  expect(await page.evaluate(() => window.__ctxConstructions.length)).toBe(0);
  expect((await page.evaluate(() => window.__tt.getState())).hasContext).toBe(false);

  await page.click("#btn-play");
  await page.waitForFunction(() => window.__tt.getState().hasContext);

  const constructions = await page.evaluate(() => window.__ctxConstructions);
  expect(constructions.length).toBe(1);
  // The iOS autoplay requirement, verified statically rather than by ear.
  expect(constructions[0].hasWindowEvent).toBe(true);
  expect((await page.evaluate(() => window.__tt.getState())).createdDuringGesture).toBe(true);
});

test("play starts a looping clip and a piano tap drones over it", async ({ page }) => {
  await page.click("#btn-play");
  await page.waitForFunction(() => window.__tt.getState().playing);

  await page.click('.key[data-pc="7"]');
  await page.waitForFunction(() => window.__tt.getState().droneRunning);

  const s = await page.evaluate(() => window.__tt.getState());
  expect(s.playing).toBe(true);
  expect(s.clipLoopFlag).toBe(true);          // loop = true, not a restart handler
  expect(s.droneRunning).toBe(true);
  expect(s.droneVoices).toBeGreaterThan(0);
  expect(s.sameContextForClipAndDrone).toBe(true);
  expect(s.contextState).toBe("running");
  expect(Math.round(s.droneFrequency)).toBe(392); // G4
});

test("the ended handler never restarts playback", async ({ page }) => {
  const source = await page.evaluate(() => window.__tt.endedHandlerSource);
  expect(source).not.toMatch(/\.start\s*\(/);
  expect(source).not.toMatch(/startClip/);
});

test("no HTMLAudioElement is ever constructed", async ({ page }) => {
  await page.click("#btn-play");
  await page.click('.key[data-pc="0"]');
  expect(await page.evaluate(() => window.__audioElements)).toBe(0);
  expect(await page.locator("audio").count()).toBe(0);
});

test("the piano shows three distinct states and both markers move together", async ({ page }) => {
  const keyA = page.locator('.key[data-pc="0"]');
  const keyB = page.locator('.key[data-pc="5"]');

  await expect(keyA).toHaveAttribute("data-state", "idle");

  await keyA.click();
  await expect(keyA).toHaveAttribute("data-state", "auditioning");
  await expect(keyA).toHaveAttribute("data-armed", "true");

  await keyB.click();
  await expect(keyB).toHaveAttribute("data-state", "auditioning");
  await expect(keyB).toHaveAttribute("data-armed", "true");
  await expect(keyA).toHaveAttribute("data-state", "idle");     // auditioning marker moved
  await expect(keyA).toHaveAttribute("data-armed", "false");    // committed marker moved

  // Silencing the drone leaves the armed key visibly committed but not sounding
  // — the third state, so a stray tap before CHECK is obvious.
  await page.click("#btn-drone-off");
  await expect(keyB).toHaveAttribute("data-state", "committed");
  await expect(keyB).toHaveAttribute("data-armed", "true");

  const states = await page.$$eval(".key", (keys) => keys.map((k) => k.dataset.state));
  expect(new Set(states).has("committed")).toBe(true);
  expect(new Set(states).has("idle")).toBe(true);
});

test("the answer is not in the page before submission", async ({ page }) => {
  await page.click("#btn-play");
  await page.click('.key[data-pc="2"]');
  const text = await page.evaluate(() => document.body.innerText);
  expect(text).not.toMatch(KEY_NAME_IN_TEXT);
  const html = await page.content();
  expect(html).not.toMatch(/tonic_pc/);
  expect(html).not.toMatch(/key_display/);
});

/** The current puzzle's answer, recovered the same way the app recovers it.
 *  The suite needs it to construct guesses that are guaranteed WRONG — without
 *  it a "wrong guess" test passes or fails on which clip was picked. */
async function answerFor(page) {
  return page.evaluate(async () => {
    const id = window.__tt.getState().puzzleId;
    const resp = await fetch(`answers/${id}.json`);
    const { h } = await resp.json();
    return window.TTScoring.recoverAnswer(id, h);
  });
}

/** Pitch classes that cannot be the answer, so every guess made from them misses. */
const wrongPcs = (answer, n) =>
  [1, 2, 5, 7, 11].map((step) => (answer.tonic_pc + step) % 12).slice(0, n);

test("the attribution is full, visible without interaction, and below the session box", async ({ page }) => {
  const artist = await page.locator("#track-artist").innerText();
  const license = await page.locator("#track-license").innerText();
  const title = await page.locator("#track-title").innerText();

  // No guess, no click, no toggle — it is readable as the page loads.
  await expect(page.locator("#attribution")).toBeVisible();
  const attribution = await page.locator("#attribution").innerText();
  expect(attribution).toContain(artist);
  expect(attribution).toContain(license);
  expect(attribution).toContain(title);
  expect(attribution).toContain("Free Music Archive");

  // Not behind a details/summary or any collapsed container.
  const hidden = await page.evaluate(() => {
    const el = document.getElementById("attribution");
    return {
      inDetails: Boolean(el.closest("details")),
      display: getComputedStyle(el).display,
      visibility: getComputedStyle(el).visibility,
      clipped: el.scrollHeight > el.clientHeight + 1,
    };
  });
  expect(hidden.inDetails).toBe(false);
  expect(hidden.display).not.toBe("none");
  expect(hidden.visibility).toBe("visible");
  expect(hidden.clipped).toBe(false);

  // Below the session box, in document order and on screen.
  const order = await page.evaluate(() => {
    const stats = document.querySelector(".stats");
    const attr = document.getElementById("attribution");
    return {
      after: Boolean(
        stats.compareDocumentPosition(attr) & Node.DOCUMENT_POSITION_FOLLOWING,
      ),
      statsBottom: stats.getBoundingClientRect().bottom,
      attrTop: attr.getBoundingClientRect().top,
    };
  });
  expect(order.after).toBe(true);
  expect(order.attrTop).toBeGreaterThanOrEqual(order.statsBottom - 1);
});

test("guessing is unlimited and a wrong guess leaks nothing", async ({ page }) => {
  const answer = await answerFor(page);
  const answerText = await page.evaluate(
    (a) => window.TTScoring.displayKey(a.tonic_pc, a.mode), answer,
  );

  // Several wrong guesses in a row. Each one must be accepted, say only
  // "Incorrect", and leave CHECK live for the next attempt.
  const pcs = wrongPcs(answer, 4);
  for (let i = 0; i < pcs.length; i += 1) {
    await page.click(`.key[data-pc="${pcs[i]}"]`);
    await expect(page.locator("#btn-check")).toBeEnabled();
    await page.click("#btn-check");
    await page.waitForFunction(
      (n) => window.__tt.getState().guessesThisPuzzle === n, i + 1,
    );

    await expect(page.locator("#result-badge")).toHaveText("INCORRECT");
    await expect(page.locator("#result-key")).toHaveText("—");

    // The assertion this whole test exists for, made after EVERY wrong guess.
    const body = await page.evaluate(() => document.body.innerText);
    expect(body).not.toContain(answerText);
    expect(body).not.toMatch(KEY_NAME_IN_TEXT);
    const html = await page.content();
    expect(html).not.toContain(answerText);

    // No warmer/colder: the taxonomy must not surface as text or as a hint on
    // the keyboard.
    const resultText = await page.locator("#result-text").innerText();
    for (const bucket of ["relative", "parallel", "semitone", "fifth"]) {
      expect(resultText.toLowerCase()).not.toContain(bucket);
    }
    const keyHints = await page.$$eval(".key", (keys) =>
      keys.map((k) => k.dataset.state).filter((s) => !["idle", "committed", "auditioning"].includes(s)));
    expect(keyHints).toEqual([]);

    // Still unresolved, so the puzzle is still playable.
    const st = await page.evaluate(() => window.__tt.getState());
    expect(st.resolved).toBe(false);
    expect(st.solved).toBe(false);
  }

  // Classified silently: the buckets moved even though nothing was displayed.
  const stats = await page.evaluate(() => window.__tt.getState().stats);
  expect(stats.guesses).toBe(pcs.length);
  expect(stats.songs).toBe(1);
  expect(stats.solved).toBe(0);
  expect(Object.values(stats.buckets).reduce((a, b) => a + b, 0)).toBe(pcs.length);
  expect(stats.buckets.exact || 0).toBe(0);
});

test("a correct guess reveals the key and counts as solved", async ({ page }) => {
  const answer = await answerFor(page);
  if (answer.mode === "minor") await page.click("#btn-minor");
  await page.click(`.key[data-pc="${answer.tonic_pc}"]`);
  await page.click("#btn-check");
  await page.waitForFunction(() => window.__tt.getState().solved === true);

  await expect(page.locator("#result-badge")).toHaveText("CORRECT");
  await expect(page.locator("#result-key")).not.toHaveText("—");
  expect(await page.evaluate(() => document.body.innerText)).toMatch(KEY_NAME_IN_TEXT);

  const stats = await page.evaluate(() => window.__tt.getState().stats);
  expect(stats.solved).toBe(1);
  expect(stats.songs).toBe(1);
  await expect(page.locator("#btn-check")).toBeDisabled();
});

test("REVEAL is available without guessing first", async ({ page }) => {
  // No key touched, no guess made. Giving up before guessing is allowed, and
  // the control must not be dead until the player interacts with the keyboard.
  await expect(page.locator("#btn-reveal")).toBeEnabled();
  await page.click("#btn-reveal");
  await page.waitForFunction(() => window.__tt.getState().revealed === true);
  await expect(page.locator("#result-key")).not.toHaveText("—");

  const stats = await page.evaluate(() => window.__tt.getState().stats);
  expect(stats.songs).toBe(1);
  expect(stats.guesses).toBe(0);
  expect(stats.solved).toBe(0);
});

test("REVEAL is explicit, never automatic, and scores as not solved", async ({ page }) => {
  const answer = await answerFor(page);
  const answerText = await page.evaluate(
    (a) => window.TTScoring.displayKey(a.tonic_pc, a.mode), answer,
  );

  // One wrong guess, then a long wait: nothing may reveal itself on a timer.
  await page.click(`.key[data-pc="${wrongPcs(answer, 1)[0]}"]`);
  await page.click("#btn-check");
  await page.waitForFunction(() => window.__tt.getState().guessesThisPuzzle === 1);
  await page.waitForTimeout(1500);
  expect(await page.evaluate(() => document.body.innerText)).not.toContain(answerText);
  expect(await page.evaluate(() => window.__tt.getState().revealed)).toBe(false);

  // REVEAL is its own control, visually distinct from CHECK and from NEXT.
  const distinct = await page.evaluate(() => {
    const style = (id) => {
      const s = getComputedStyle(document.getElementById(id));
      return `${s.backgroundColor}|${s.borderColor}|${s.color}`;
    };
    return { reveal: style("btn-reveal"), check: style("btn-check"), next: style("btn-next") };
  });
  expect(distinct.reveal).not.toBe(distinct.check);
  expect(distinct.reveal).not.toBe(distinct.next);

  await page.click("#btn-reveal");
  await page.waitForFunction(() => window.__tt.getState().revealed === true);

  await expect(page.locator("#result-badge")).toHaveText("REVEALED");
  await expect(page.locator("#result-key")).toHaveText(answerText);
  // The relative_error reading is delivered here and nowhere earlier.
  await expect(page.locator("#result-text")).not.toHaveText("");
  const attribution = await page.locator("#attribution").innerText();
  expect(attribution).toContain(await page.locator("#track-artist").innerText());

  const stats = await page.evaluate(() => window.__tt.getState().stats);
  expect(stats.revealed).toBe(1);
  expect(stats.solved).toBe(0);
  expect(stats.songs).toBe(1);
  await expect(page.locator("#btn-check")).toBeDisabled();
});

test("attempts per song are tracked across puzzles", async ({ page }) => {
  const answer = await answerFor(page);
  for (const pc of wrongPcs(answer, 3)) {
    await page.click(`.key[data-pc="${pc}"]`);
    await page.click("#btn-check");
  }
  await page.waitForFunction(() => window.__tt.getState().guessesThisPuzzle === 3);
  await page.click("#btn-reveal");
  await page.waitForFunction(() => window.__tt.getState().revealed === true);

  await page.click("#btn-next");
  await page.waitForFunction(() => window.__tt.getState().guessesThisPuzzle === 0);
  const next = await answerFor(page);
  await page.click(`.key[data-pc="${wrongPcs(next, 1)[0]}"]`);
  await page.click("#btn-check");
  await page.waitForFunction(() => window.__tt.getState().stats.guesses === 4);

  const stats = await page.evaluate(() => window.__tt.getState().stats);
  expect(stats.songs).toBe(2);
  expect(stats.guesses).toBe(4);
  await expect(page.locator("#stats-grid")).toContainText("GUESSES / SONG");
  await expect(page.locator("#stats-grid")).toContainText("2.0");
});

test("session stats are ephemeral: no storage, and a reload resets them", async ({ page }) => {
  const answer = await answerFor(page);
  await page.click(`.key[data-pc="${wrongPcs(answer, 1)[0]}"]`);
  await page.click("#btn-check");
  await page.waitForFunction(() => window.__tt.getState().stats.guesses === 1);

  const storage = await page.evaluate(() => ({
    local: window.localStorage.length,
    session: window.sessionStorage.length,
    cookie: document.cookie,
  }));
  expect(storage.local).toBe(0);
  expect(storage.session).toBe(0);
  expect(storage.cookie).toBe("");

  await loadUnit(page);
  const stats = await page.evaluate(() => window.__tt.getState().stats);
  expect(stats.guesses).toBe(0);
  expect(stats.songs).toBe(0);
  expect(stats.solved).toBe(0);
});

test("returning to the tab resumes a suspended context", async ({ page }) => {
  await page.click("#btn-play");
  await page.waitForFunction(() => window.__tt.getState().contextState === "running");

  await page.evaluate(async () => {
    // Simulate the tab being backgrounded: iOS suspends the context.
    const state = window.__tt.getState();
    if (!state.hasContext) throw new Error("no context to suspend");
    await window.__ttSuspendForTest();
  });
  expect((await page.evaluate(() => window.__tt.getState())).contextState).toBe("suspended");

  await page.evaluate(() => document.dispatchEvent(new Event("visibilitychange")));
  await page.waitForFunction(() => window.__tt.getState().contextState === "running");
});

test("the layout is usable with no horizontal overflow", async ({ page }) => {
  const overflow = await page.evaluate(() => ({
    scroll: document.documentElement.scrollWidth,
    inner: window.innerWidth,
  }));
  expect(overflow.scroll).toBeLessThanOrEqual(overflow.inner + 1);

  for (const sel of ["#btn-play", "#btn-check", "#piano", "#mix-clip", "#mix-drone", "#attribution"]) {
    await expect(page.locator(sel)).toBeVisible();
    const box = await page.locator(sel).boundingBox();
    expect(box).not.toBeNull();
    expect(box.x).toBeGreaterThanOrEqual(-1);
    expect(box.x + box.width).toBeLessThanOrEqual(overflow.inner + 1);
  }

  // Every control the interaction needs must be tappable, not just present.
  const keyBox = await page.locator('.key[data-pc="0"]').boundingBox();
  expect(keyBox.width).toBeGreaterThan(20);
  expect(keyBox.height).toBeGreaterThan(40);
});

test("the faceplate collapses to a single column on a phone", async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== "webkit-iphone", "mobile layout only");
  const rows = await page.evaluate(() => {
    // Only elements that are actually laid out have a left edge — a hidden
    // panel (the error banner, when there is no error) reports 0 and would
    // otherwise look like a second column.
    const panels = [...document.querySelectorAll(".unit > *")]
      .map((p) => p.getBoundingClientRect())
      .filter((r) => r.width > 0 && r.height > 0);
    return panels.map((r) => Math.round(r.left));
  });
  expect(rows.length).toBeGreaterThan(4);   // guard: the filter must not empty the list
  // A single column means every visible panel shares one left edge.
  expect(new Set(rows).size).toBe(1);
});
