/* GATE S2 — the ported JS scoring reproduces the server's verdicts.
 *
 * The fixture table below is the SAME table as the Python suite's
 * `test_relative_error_taxonomy` parametrisation, copied case for case. The
 * taxonomy is what the app teaches, so a behaviour change here is a regression,
 * not a rewrite — which is why these are ported rather than re-derived.
 */

const { test, expect } = require("@playwright/test");

// (guessPc, guessMode, actualPc, actualMode, expected) — mirrors scoring.py's tests.
const CASES = [
  [0, "major", 0, "major", "exact"],
  [9, "minor", 0, "major", "relative"],
  [0, "major", 9, "minor", "relative"],
  [0, "minor", 0, "major", "parallel"],
  [1, "major", 0, "major", "semitone"],
  [11, "major", 0, "major", "semitone"],
  [7, "major", 0, "major", "fifth"],
  [5, "major", 0, "major", "fifth"],
  [6, "major", 0, "major", "other"],
  [4, "minor", 0, "major", "other"],
];

test.beforeEach(async ({ page }) => {
  await page.goto("/");
  await page.waitForFunction(() => Boolean(window.TTScoring));
});

test("the ported taxonomy matches the server's fixtures case for case", async ({ page }) => {
  const results = await page.evaluate(
    (cases) => cases.map(([gp, gm, ap, am]) => window.TTScoring.classify(gp, gm, ap, am)),
    CASES,
  );
  expect(results).toEqual(CASES.map((c) => c[4]));
});

test("every relative pair in both directions scores relative", async ({ page }) => {
  const bad = await page.evaluate(() => {
    const out = [];
    for (let pc = 0; pc < 12; pc += 1) {
      const relMinor = ((pc - 3) % 12 + 12) % 12;
      if (window.TTScoring.classify(relMinor, "minor", pc, "major") !== "relative") {
        out.push(`major ${pc}`);
      }
      const relMajor = (pc + 3) % 12;
      if (window.TTScoring.classify(relMajor, "major", pc, "minor") !== "relative") {
        out.push(`minor ${pc}`);
      }
    }
    return out;
  });
  expect(bad).toEqual([]);
});

test("display spelling matches the server's conventional table", async ({ page }) => {
  const labels = await page.evaluate(() => {
    const out = {};
    for (let pc = 0; pc < 12; pc += 1) {
      out[`${pc}-major`] = window.TTScoring.displayKey(pc, "major");
      out[`${pc}-minor`] = window.TTScoring.displayKey(pc, "minor");
    }
    return out;
  });
  // The cases where the source's own spelling differs from the conventional one.
  expect(labels["3-major"]).toBe("Eb Major");
  expect(labels["3-minor"]).toBe("Eb minor");
  expect(labels["1-major"]).toBe("Db Major");
  expect(labels["1-minor"]).toBe("C# minor");
  expect(labels["8-major"]).toBe("Ab Major");
  expect(labels["8-minor"]).toBe("G# minor");
  expect(labels["10-major"]).toBe("Bb Major");
  expect(labels["0-major"]).toBe("C Major");
});

test("an invalid mode raises rather than scoring something", async ({ page }) => {
  const threw = await page.evaluate(() => {
    try {
      window.TTScoring.classify(0, "dorian", 0, "major");
      return false;
    } catch (err) {
      return true;
    }
  });
  expect(threw).toBe(true);
});

test("brute force recovers the answer a verifier stands for", async ({ page }) => {
  const recovered = await page.evaluate(async () => {
    // Hash the same way the build script does, then recover it.
    const encoder = new TextEncoder();
    const digest = await crypto.subtle.digest("SHA-256", encoder.encode("fma-000173:7:major"));
    const hex = [...new Uint8Array(digest)].map((b) => b.toString(16).padStart(2, "0")).join("");
    return window.TTScoring.recoverAnswer("fma-000173", hex);
  });
  expect(recovered).toEqual({ tonic_pc: 7, mode: "major" });
});

test("a verifier from a different puzzle id does not resolve", async ({ page }) => {
  // The id is the salt: the same key under another id must not brute-force,
  // which is what stops all 3321 files collapsing to a 24-entry table.
  const failed = await page.evaluate(async () => {
    const encoder = new TextEncoder();
    const digest = await crypto.subtle.digest("SHA-256", encoder.encode("fma-000173:7:major"));
    const hex = [...new Uint8Array(digest)].map((b) => b.toString(16).padStart(2, "0")).join("");
    try {
      await window.TTScoring.recoverAnswer("fma-999999", hex);
      return false;
    } catch (err) {
      return true;
    }
  });
  expect(failed).toBe(true);
});
