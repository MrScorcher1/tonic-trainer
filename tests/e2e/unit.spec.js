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

test("submitting a guess renders a result panel and the attribution stays visible", async ({ page }) => {
  const artist = await page.locator("#track-artist").innerText();
  const license = await page.locator("#track-license").innerText();

  await page.click('.key[data-pc="4"]');
  await page.click("#btn-check");
  await expect(page.locator("#result")).toBeVisible();
  await expect(page.locator("#result-key")).not.toHaveText("—");
  await expect(page.locator("#result-text")).not.toHaveText("");

  const attribution = await page.locator("#attribution").innerText();
  expect(attribution).toContain(artist);
  expect(attribution).toContain(license);
  await expect(page.locator("#attribution")).toBeVisible();

  // The answer is only allowed to appear after the guess is committed.
  expect(await page.evaluate(() => document.body.innerText)).toMatch(KEY_NAME_IN_TEXT);
});

test("session stats are ephemeral: no storage, and a reload resets them", async ({ page }) => {
  await page.click('.key[data-pc="9"]');
  await page.click("#btn-check");
  await page.waitForFunction(() => window.__tt.getState().stats.attempts === 1);

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
  expect(stats.attempts).toBe(0);
  expect(stats.correct).toBe(0);
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
