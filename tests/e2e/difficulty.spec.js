/* GATE D1 (browser half) — the rating loop is honest and cheap.
 *
 * The assertion this file exists for is the anchoring guard: the computed
 * difficulty must not be visible before the player rates. If it were, players
 * would echo it, votes would confirm the prior, and the whole feedback loop
 * would be theatre that looks validated while learning nothing.
 *
 * The rest defends the constraints that make the loop affordable and reliable:
 * batched writes (KV allows 1,000 writes/day, not 100,000), an exit flush that
 * works on iOS, and failing soft when the ratings service is absent or broken.
 */

const { test, expect } = require("@playwright/test");
const { serveLocalClips } = require("./helpers");

const ENDPOINT = "https://ratings.test/collect";

/** Point the page at a stubbed ratings endpoint and count what it sends. */
async function withRatings(page, { failing = false } = {}) {
  const posts = [];

  await page.route("**/config.json", async (route) => {
    const response = await route.fetch();
    const config = await response.json();
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ ...config, ratings_endpoint: ENDPOINT }),
    });
  });

  await page.route(`${ENDPOINT}**`, async (route) => {
    if (failing) return route.fulfill({ status: 500, body: "{}" });
    if (route.request().method() === "GET") {
      return route.fulfill({
        status: 200, contentType: "application/json",
        body: JSON.stringify({ songs: {} }),
      });
    }
    posts.push(JSON.parse(route.request().postData() || "{}"));
    return route.fulfill({ status: 200, contentType: "application/json", body: '{"accepted":1}' });
  });

  // sendBeacon is not routable, so record it in the page instead.
  await page.addInitScript(() => {
    window.__beacons = [];
    window.__unloadHandlers = [];
    const originalAdd = window.addEventListener.bind(window);
    window.addEventListener = (type, ...rest) => {
      if (type === "beforeunload" || type === "unload") window.__unloadHandlers.push(type);
      return originalAdd(type, ...rest);
    };
    navigator.sendBeacon = (url, body) => {
      window.__beacons.push({ url, body: String(body && body.text ? "blob" : body) });
      return true;
    };
  });

  await serveLocalClips(page);
  return posts;
}

async function answerAndRate(page, rating) {
  await page.click('.key[data-pc="0"]');
  await page.click("#btn-check");
  await expect(page.locator("#result")).toBeVisible();
  if (rating) await page.click(`.btn--rate[data-rating="${rating}"]`);
  await page.click("#btn-next");
  await page.waitForFunction(() => window.__tt.getState().answered === false);
}

/* REMOVAL-OK: "the computed difficulty is absent from the DOM until the player
 * rates" and "skipping the rating also reveals the difficulty" asserted the
 * anchoring guard, which the USER RETIRED explicitly on 2026-08-16 ("I want ppl
 * to be able to see the difficulty from the beginning tho even before answering
 * idc about anchoring bias"). The assertions are inverted below rather than
 * deleted, so the record shows the design changed rather than a test quietly
 * disappearing. What the retirement costs is narrowed in the README: ratings are
 * now anchored, so only DISAGREEMENT with the prior is evidence.
 */
test("the computed difficulty is on the readout from the moment a puzzle loads", async ({ page }) => {
  await withRatings(page);
  await page.goto("/");
  await page.waitForFunction(() => window.__tt.getState().puzzleId !== null);

  // Visible before playing, before answering, before rating.
  await expect(page.locator("#difficulty-dots")).toBeVisible();
  const dots = await page.locator("#difficulty-dots i").count();
  expect(dots).toBe(3);
  const filled = await page.locator('#difficulty-dots i[data-filled="true"]').count();
  expect(filled).toBeGreaterThanOrEqual(1);
  expect(filled).toBeLessThanOrEqual(3);

  const level = await page.evaluate(() => window.__tt.getState().puzzleDifficulty);
  expect(filled).toBe(level);
  await expect(page.locator("#difficulty-note")).toContainText(`computed ${level}`);
});

test("rating is optional: the player can advance without ever rating", async ({ page }) => {
  await withRatings(page);
  await page.goto("/");
  await page.waitForFunction(() => window.__tt.getState().puzzleId !== null);

  const first = await page.evaluate(() => window.__tt.getState().puzzleId);
  await page.click('.key[data-pc="0"]');
  await page.click("#btn-check");
  await expect(page.locator("#result")).toBeVisible();

  // Straight to the next clip, no rating given.
  await page.click("#btn-next");
  await page.waitForFunction(
    (prev) => window.__tt.getState().puzzleId !== null && window.__tt.getState().answered === false,
    first,
  );
  expect(await page.evaluate(() => window.TTRatings.debug().session.length)).toBe(0);
  await expect(page.locator("#difficulty-dots")).toBeVisible();
});

test("the app does not auto-advance after a submission", async ({ page }) => {
  await withRatings(page);
  await page.goto("/");
  await page.waitForFunction(() => window.__tt.getState().puzzleId !== null);

  const first = await page.evaluate(() => window.__tt.getState().puzzleId);
  await page.click('.key[data-pc="0"]');
  await page.click("#btn-check");
  await expect(page.locator("#result")).toBeVisible();

  await page.waitForTimeout(2000);
  await expect(page.locator("#result")).toBeVisible();
  expect(await page.evaluate(() => window.__tt.getState().puzzleId)).toBe(first);
  expect(await page.evaluate(() => window.__tt.getState().answered)).toBe(true);
});

test("N ratings produce ceil(N/10) writes, not N", async ({ page }) => {
  const posts = await withRatings(page);
  await page.goto("/");
  await page.waitForFunction(() => window.__tt.getState().puzzleId !== null);

  for (let i = 0; i < 12; i += 1) {
    await answerAndRate(page, (i % 3) + 1);
  }
  await page.waitForTimeout(500);

  // 12 ratings at a batch of 10 is exactly one flush; the remaining 2 wait.
  expect(posts.length).toBe(1);
  expect(posts[0].ratings.length).toBe(10);
  const debug = await page.evaluate(() => window.TTRatings.debug());
  expect(debug.session.length).toBe(12);
  expect(debug.pending.length).toBe(2);
});

test("a pending batch is flushed by sendBeacon when the page is hidden", async ({ page }) => {
  await withRatings(page);
  await page.goto("/");
  await page.waitForFunction(() => window.__tt.getState().puzzleId !== null);

  await answerAndRate(page, 3);
  await answerAndRate(page, 1);
  expect((await page.evaluate(() => window.TTRatings.debug())).pending.length).toBe(2);

  await page.evaluate(() => {
    Object.defineProperty(document, "visibilityState", { value: "hidden", configurable: true });
    document.dispatchEvent(new Event("visibilitychange"));
  });

  const beacons = await page.evaluate(() => window.__beacons);
  expect(beacons.length).toBe(1);
  expect(beacons[0].url).toContain("ratings.test");
  expect((await page.evaluate(() => window.TTRatings.debug())).pending.length).toBe(0);
});

test("no beforeunload or unload handler is registered", async ({ page }) => {
  // iOS Safari frequently never fires these; an exit flush built on them would
  // silently lose most mobile sessions.
  await withRatings(page);
  await page.goto("/");
  await page.waitForFunction(() => window.__tt.getState().puzzleId !== null);
  expect(await page.evaluate(() => window.__unloadHandlers)).toEqual([]);
  const source = await page.evaluate(async () => (await fetch("ratings.js")).text());
  expect(source).not.toMatch(/addEventListener\(\s*["'](beforeunload|unload)["']/);
});

test("a broken ratings endpoint does not break play", async ({ page }) => {
  await withRatings(page, { failing: true });
  await page.goto("/");
  await page.waitForFunction(() => window.__tt.getState().puzzleId !== null);

  await answerAndRate(page, 2);
  await page.waitForFunction(() => window.__tt.getState().puzzleId !== null);

  // Still playing, still scoring, difficulty still shown from the prior.
  await page.click('.key[data-pc="3"]');
  await page.click("#btn-check");
  await expect(page.locator("#result")).toBeVisible();
  await page.click('.btn--rate[data-rating="1"]');
  // The difficulty shown falls back to the algorithmic prior; nothing errors.
  await expect(page.locator("#difficulty-dots")).toBeVisible();
  await expect(page.locator("#error")).toBeHidden();
});

test("ratings survive the session and are lost on reload", async ({ page }) => {
  await withRatings(page);
  await page.goto("/");
  await page.waitForFunction(() => window.__tt.getState().puzzleId !== null);

  await answerAndRate(page, 2);
  await answerAndRate(page, 3);
  expect((await page.evaluate(() => window.TTRatings.debug())).session.length).toBe(2);

  const storage = await page.evaluate(() => ({
    local: window.localStorage.length,
    session: window.sessionStorage.length,
    cookie: document.cookie,
  }));
  expect(storage).toEqual({ local: 0, session: 0, cookie: "" });

  await page.reload();
  await page.waitForFunction(() => window.__tt.getState().puzzleId !== null);
  const after = await page.evaluate(() => window.TTRatings.debug());
  expect(after.session).toEqual([]);
  expect(after.pending).toEqual([]);
});

test("the shrinkage formula reproduces its known cases", async ({ page }) => {
  await withRatings(page);
  await page.goto("/");
  await page.waitForFunction(() => Boolean(window.TTRatings));

  const results = await page.evaluate(() => {
    const s = window.TTRatings.shrink;
    return {
      noVotes1: s(1, 0, 0, 5),
      noVotes3: s(3, 0, 0, 5),
      oneDissenter: s(1, 1, 3, 5),        // one vote barely moves it
      fiveDissenters: s(1, 5, 15, 5),     // equal weight to the algorithm
      manyDissenters: s(1, 50, 150, 5),   // sustained disagreement wins
      clampHigh: s(3, 20, 200, 5),
      clampLow: s(1, 20, 0, 5),
    };
  });

  expect(results.noVotes1).toBe(1);       // n=0 returns EXACTLY the prior
  expect(results.noVotes3).toBe(3);
  expect(results.oneDissenter).toBe(1);
  expect(results.fiveDissenters).toBe(2);
  expect(results.manyDissenters).toBe(3);
  expect(results.clampHigh).toBeLessThanOrEqual(3);
  expect(results.clampLow).toBeGreaterThanOrEqual(1);
});

test("the difficulty filter narrows the pool", async ({ page }) => {
  await withRatings(page);
  await page.goto("/");
  await page.waitForFunction(() => window.__tt.getState().puzzleId !== null);

  await page.selectOption("#difficulty-select", "1");
  await page.waitForFunction(() => window.__tt.getState().puzzleId !== null);
  const levels = await page.evaluate(async () => {
    const seen = new Set();
    for (let i = 0; i < 25; i += 1) {
      await window.__tt.reload();
      seen.add(window.__tt.getState().puzzleDifficulty);
    }
    return [...seen];
  });
  expect(levels).toEqual([1]);
});
