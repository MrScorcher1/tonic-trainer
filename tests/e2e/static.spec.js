/* GATES S3 and S4 — the assertions that only make sense on the static build.
 *
 * The claim these defend is narrow and worth stating exactly: the initial
 * payload contains no answers, and no answer is even REQUESTED until the user
 * commits. That is obfuscation, not secrecy — 24 possibilities means anyone can
 * brute-force one — but "peeking requires writing code" is testable, and this is
 * where it gets tested.
 */

const { test, expect } = require("@playwright/test");
const { serveLocalClips } = require("./helpers");

test.beforeEach(async ({ page }) => {
  await serveLocalClips(page);
});

async function waitForPuzzle(page) {
  await page.waitForFunction(() => window.__tt && window.__tt.getState().puzzleId !== null,
    null, { timeout: 20_000 });
}

test("GATE S3: 50 draws return at least 40 distinct puzzles", async ({ page }) => {
  await page.goto("/");
  await waitForPuzzle(page);
  const ids = await page.evaluate(async () => {
    const seen = new Set();
    for (let i = 0; i < 50; i += 1) {
      await window.__tt.reload();
      seen.add(window.__tt.getState().puzzleId);
    }
    return [...seen];
  });
  expect(ids.length).toBeGreaterThanOrEqual(40);
});

test("the manifest response body carries no answer field", async ({ page }) => {
  const bodies = [];
  page.on("response", async (response) => {
    if (response.url().endsWith("manifest.json")) bodies.push(await response.text());
  });
  await page.goto("/");
  await waitForPuzzle(page);

  expect(bodies.length).toBeGreaterThan(0);
  for (const body of bodies) {
    expect(body).not.toContain("tonic_pc");
    expect(body).not.toContain("key_display");
    // A key name in the payload would spoil a puzzle just as effectively.
    expect(body).not.toMatch(/\b[A-G][#b]?\s+(major|minor)\b/i);
  }
});

test("no answer file is requested before submission", async ({ page }) => {
  const answerRequests = [];
  page.on("request", (request) => {
    if (request.url().includes("/answers/")) answerRequests.push(request.url());
  });

  await page.goto("/");
  await waitForPuzzle(page);
  await page.click("#btn-play");
  await page.click('.key[data-pc="4"]');
  await page.click("#btn-minor");
  // Everything short of committing: load, play, explore the keyboard, set mode.
  expect(answerRequests).toEqual([]);

  await page.click("#btn-check");
  await expect(page.locator("#result")).toBeVisible();
  expect(answerRequests.length).toBe(1);
  expect(answerRequests[0]).toMatch(/\/answers\/fma-\d{6}\.json$/);
});

test("the scored answer agrees with the published verifier", async ({ page }) => {
  await page.goto("/");
  await waitForPuzzle(page);

  const { id } = await page.evaluate(() => ({ id: window.__tt.getState().puzzleId }));
  await page.click('.key[data-pc="0"]');
  await page.click("#btn-check");
  await expect(page.locator("#result-key")).not.toHaveText("—");

  const shown = await page.locator("#result-key").innerText();
  const recovered = await page.evaluate(async (puzzleId) => {
    const verifier = (await (await fetch(`answers/${puzzleId}.json`)).json()).h;
    const answer = await window.TTScoring.recoverAnswer(puzzleId, verifier);
    return window.TTScoring.displayKey(answer.tonic_pc, answer.mode);
  }, id);
  expect(shown).toBe(recovered);
});

test("a failed audio fetch names the likely cause instead of failing silently", async ({ page }) => {
  await page.route("**/clips/**", (route) => route.abort("failed"));
  await page.goto("/");
  await expect(page.locator("#error")).toBeVisible({ timeout: 20_000 });

  const text = await page.locator("#error").innerText();
  expect(text).toMatch(/AUDIO HOST/i);
  expect(text).toMatch(/Access-Control-Allow-Origin|rate limit|offline/i);
  expect(text).toMatch(/fallback server/i);
  await expect(page.locator("#status-line")).toHaveText(/AUDIO UNAVAILABLE/);
});

test("a rate-limited response is named as such", async ({ page }) => {
  await page.route("**/clips/**", (route) => route.fulfill({ status: 429, body: "" }));
  await page.goto("/");
  await expect(page.locator("#error")).toBeVisible({ timeout: 20_000 });
  await expect(page.locator("#error")).toContainText("429");
  await expect(page.locator("#error")).toContainText("rate limit");
});

/* REMOVAL-OK: "the pool selector still turns untagged off" tested the tier
 * scheme's single pool selector. Tiers are gone — they claimed a difficulty
 * ranking that measurement inverted — and `untagged` is now the genre
 * `Ungenred`. The capability it guarded (excluding ungenred tracks) survives as
 * a genre selection, which is what this asserts.
 */
test("the genre filter narrows the corpus, and ungenred is part of the default", async ({ page }) => {
  await page.goto("/");
  await waitForPuzzle(page);

  const genres = await page.evaluate(async () => {
    const seen = new Set();
    for (let i = 0; i < 60; i += 1) {
      await window.__tt.reload();
      seen.add(window.__tt.getState().puzzleGenre);
    }
    return [...seen];
  });
  expect(genres).toContain("Ungenred");
  expect(genres.length).toBeGreaterThan(1);

  await page.selectOption("#genre-select", "Rock");
  await waitForPuzzle(page);
  const rockOnly = await page.evaluate(async () => {
    const seen = new Set();
    for (let i = 0; i < 30; i += 1) {
      await window.__tt.reload();
      seen.add(window.__tt.getState().puzzleGenre);
    }
    return [...seen];
  });
  expect(rockOnly).toEqual(["Rock"]);
});
