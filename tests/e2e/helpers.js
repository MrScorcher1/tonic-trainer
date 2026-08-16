/* Shared e2e helpers.
 *
 * The deployed site streams clips from the Hugging Face CDN. The suite serves
 * those same clips off local disk instead, by intercepting the request. Two
 * reasons, and the second is the important one:
 *
 *  1. Hermetic. The suite does not depend on a third party being up, on network
 *    latency, or on the /resolve/ rate limit (3000 req / 5 min per IP) — which
 *    a full run would otherwise eat into.
 *  2. It tests OUR code. Whether HF sends Access-Control-Allow-Origin is not
 *    something this repo can fix or should assert; that is verified by the
 *    fallback server's startup probe and by a curl against the live URL.
 *
 * What this therefore does NOT cover, stated plainly so nobody assumes
 * otherwise: real CDN reachability, real CORS headers, and real signed-URL
 * expiry. Those are external, and the app's error path (tested separately) is
 * what surfaces them when they break.
 */

const path = require("path");
const fs = require("fs");

const CLIP_ROOT = path.resolve(__dirname, "../../build/clips");

/** Serve any CDN clip request from build/clips/NNN/NNNNNN.mp3. */
async function serveLocalClips(page) {
  await page.route("**/resolve/main/clips/**", (route) => {
    const url = new URL(route.request().url());
    const rel = url.pathname.split("/clips/").pop();
    const file = path.join(CLIP_ROOT, rel);
    if (!fs.existsSync(file)) {
      // Loud, not silent: a missing clip means the manifest and the clip tree
      // disagree, which is a real defect rather than a test-environment quirk.
      return route.fulfill({ status: 404, body: `no local clip at ${file}` });
    }
    return route.fulfill({
      status: 200,
      contentType: "audio/mpeg",
      headers: { "access-control-allow-origin": "*" },
      body: fs.readFileSync(file),
    });
  });
}

module.exports = { serveLocalClips, CLIP_ROOT };
