// Runs the shipped extension code (vendor/defuddle.js + extract.js) against
// saved page fixtures in jsdom: `node --test` from this directory.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { JSDOM } from "jsdom";

const read = (p) => readFileSync(new URL(p, import.meta.url), "utf8");
const VENDOR = read("../vendor/defuddle.js");
const EXTRACT = read("../extract.js");

// jsdom's selector engine rejects `:has()` nested inside `:not()` — valid CSS
// that Chrome supports and Defuddle's removal selectors use. Retry those calls
// without the clause, so the real extraction path runs instead of Defuddle's
// catch-all bailing out to the raw <body>.
function patchNotHas(win) {
  const strip = (s) => String(s).replace(/:not\(\s*:has\([^()]*\)\s*\)/g, "");
  for (const proto of [win.Element.prototype, win.Document.prototype]) {
    for (const name of ["querySelector", "querySelectorAll", "matches", "closest"]) {
      const orig = proto[name];
      if (!orig) continue;
      proto[name] = function (selector, ...rest) {
        try {
          return orig.call(this, selector, ...rest);
        } catch {
          return orig.call(this, strip(selector), ...rest);
        }
      };
    }
  }
}

function extract(fixture, url) {
  const dom = new JSDOM(read(`./fixtures/${fixture}`), { url, runScripts: "outside-only" });
  dom.window.console.error = () => {};
  patchNotHas(dom.window);
  dom.window.eval(VENDOR);
  dom.window.eval(EXTRACT);
  return dom.window.eval("__nodeRelsExtract()");
}

const cases = [
  ["article.html", "https://journal.example.com/boring-infrastructure", "Boring Infrastructure", "novelty budget"],
  ["paywall.html", "https://meridian.example.com/grid-rebuild", "Grid Rebuild", "transformer"],
  ["spa.html", "https://docs.example.com/release-notes/4-2", "Release notes 4.2", "ingestion pipeline"],
];

for (const [fixture, url, titlePart, bodyPart] of cases) {
  test(`extracts ${fixture}`, () => {
    const r = extract(fixture, url);
    assert.match(r.title, new RegExp(titlePart, "i"));
    assert.ok(r.markdown.length > 500, `markdown too short (${r.markdown.length} chars)`);
    assert.match(r.markdown, new RegExp(bodyPart, "i"));
    assert.doesNotMatch(r.markdown, /<(div|script|nav|footer)\b/i, "HTML leaked into the markdown");
  });
}

test("metadata and markdown structure survive, page chrome does not", () => {
  const r = extract("article.html", "https://journal.example.com/boring-infrastructure");
  assert.equal(r.author, "Jordan Ellery");
  assert.equal(r.published, "2025-03-11");
  assert.equal(r.site, "Example Journal");
  assert.match(r.markdown, /^## Novelty has a carrying cost$/m);
  assert.match(r.markdown, /^- Every dependency/m);
  assert.match(r.markdown, /^> Choose boring technology/m);
  assert.doesNotMatch(r.markdown, /All rights reserved/i);
  assert.doesNotMatch(r.markdown, /Popular|Archive/);
});
