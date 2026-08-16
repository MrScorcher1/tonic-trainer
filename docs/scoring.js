/* The `relative_error` taxonomy, ported from the server's scoring.py.
 *
 * This is the pedagogically central part of the app — the app's whole claim is
 * that it tells you HOW you missed, not just that you did. So this is a port,
 * not a rewrite: the same buckets, the same boundaries, and the same fixture
 * table the Python tests use (see tests/e2e/scoring.spec.js). A behaviour change
 * here is a regression.
 */

const EXACT = "exact";
const RELATIVE = "relative";
const PARALLEL = "parallel";
const SEMITONE = "semitone";
const FIFTH = "fifth";
const OTHER = "other";

const NOTE_NAMES_SHARP = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"];

// Conventional spelling for display — the key signature a musician would write.
// Mirrors DISPLAY_SPELLING in normalize.py; differs from the sharp names at
// pitch classes 1 and 8 (major) and 3 (both modes).
const DISPLAY_NAMES = {
  major: ["C", "Db", "D", "Eb", "E", "F", "F#", "G", "Ab", "A", "Bb", "B"],
  minor: ["C", "C#", "D", "Eb", "E", "F", "F#", "G", "G#", "A", "Bb", "B"],
};

function displayKey(tonicPc, mode) {
  const names = DISPLAY_NAMES[mode];
  if (!names) throw new Error(`unknown mode: ${mode}`);
  const pc = ((tonicPc % 12) + 12) % 12;
  return `${names[pc]} ${mode === "major" ? "Major" : "minor"}`;
}

/**
 * Classify a guess against the answer.
 *
 * exact    — tonic and mode both right.
 * relative — same pitch collection, wrong mode (C major vs A minor). The
 *            relative minor sits 3 semitones below its major.
 * parallel — right tonic, wrong mode.
 * semitone — tonic off by one either way.
 * fifth    — tonic off by a fifth either way (7 or 5 semitones).
 * other    — everything else.
 */
function classify(guessPc, guessMode, actualPc, actualMode) {
  if (!["major", "minor"].includes(guessMode) || !["major", "minor"].includes(actualMode)) {
    throw new Error(`mode must be 'major' or 'minor', got ${guessMode} / ${actualMode}`);
  }
  const g = ((Number(guessPc) % 12) + 12) % 12;
  const a = ((Number(actualPc) % 12) + 12) % 12;

  if (g === a && guessMode === actualMode) return EXACT;

  if (guessMode !== actualMode) {
    if (g === a) return PARALLEL;
    if (guessMode === "minor" && g === (((a - 3) % 12) + 12) % 12) return RELATIVE;
    if (guessMode === "major" && g === (a + 3) % 12) return RELATIVE;
  }

  const interval = (((g - a) % 12) + 12) % 12;
  if (interval === 1 || interval === 11) return SEMITONE;
  if (interval === 5 || interval === 7) return FIFTH;
  return OTHER;
}

/** Plain-language reading of a miss, for the result panel. */
function explain(bucket, keyDisplay, guessDisplay) {
  switch (bucket) {
    case EXACT:
      return `Exactly right — the key is ${keyDisplay}.`;
    case RELATIVE:
      return `You found the right notes but heard the wrong home. You guessed ${guessDisplay}; ` +
        `the answer is ${keyDisplay} — its relative key. Same seven notes, different tonic.`;
    case PARALLEL:
      return `Right tonic, wrong colour. You guessed ${guessDisplay}; the answer is ${keyDisplay}. ` +
        "Listen to the third above the tonic.";
    case SEMITONE:
      return `One semitone off. You guessed ${guessDisplay}; the answer is ${keyDisplay}. ` +
        "Your drone was a hair sharp or flat against the track.";
    case FIFTH:
      return `Off by a fifth. You guessed ${guessDisplay}; the answer is ${keyDisplay}. ` +
        "Easy to lock onto the dominant instead of the tonic.";
    default:
      return `Not this time. You guessed ${guessDisplay}; the answer is ${keyDisplay}.`;
  }
}

/**
 * Recover the answer from a verifier by brute force — the same 24 hashes the
 * build script could have precomputed, deliberately not precomputed here.
 *
 * There is no secret being protected: 24 possibilities means anyone can do
 * this. What the scheme buys is that the answer is not *readable* — it never
 * appears in a response body, so it cannot be glimpsed in the network tab or
 * found by idly opening devtools. Peeking requires writing code.
 */
async function recoverAnswer(puzzleId, storedHash) {
  const encoder = new TextEncoder();
  for (let pc = 0; pc < 12; pc += 1) {
    for (const mode of ["major", "minor"]) {
      const digest = await crypto.subtle.digest(
        "SHA-256", encoder.encode(`${puzzleId}:${pc}:${mode}`),
      );
      const hex = [...new Uint8Array(digest)]
        .map((b) => b.toString(16).padStart(2, "0"))
        .join("");
      if (hex === storedHash) return { tonic_pc: pc, mode };
    }
  }
  throw new Error(`no (tonic, mode) pair hashes to the stored verifier for ${puzzleId}`);
}

window.TTScoring = { classify, explain, displayKey, recoverAnswer, NOTE_NAMES_SHARP };
