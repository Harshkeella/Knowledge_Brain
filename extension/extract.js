// Injected into the active tab (after vendor/defuddle.js) by popup.js.
// Runs Defuddle against the live, fully-rendered DOM and returns Markdown —
// no server-side scrape, so logged-in and JS-rendered pages work.

// Per-site overrides for pages the general pass gets wrong. Defuddle already
// ships extractors for the usual suspects (ChatGPT, Claude, GitHub, Hacker
// News, Bluesky…), so only add an entry here when you've seen its output be
// wrong on a specific site. Each value: (document) => { title?, markdown }.
const NODE_RELS_EXTRACTORS = {
  // Illustrative — plain-text <pre> pages Defuddle scores as boilerplate.
  "datatracker.ietf.org": (doc) => ({
    title: doc.title,
    markdown: doc.querySelector("pre")?.textContent ?? "",
  }),
};

function nodeRelsCustomExtractor(host) {
  for (const [domain, fn] of Object.entries(NODE_RELS_EXTRACTORS)) {
    if (host === domain || host.endsWith("." + domain)) return fn;
  }
  return null;
}

window.__nodeRelsExtract = function () {
  const host = location.hostname.replace(/^www\./, "");
  const custom = nodeRelsCustomExtractor(host);

  if (custom) {
    const r = custom(document) || {};
    return {
      title: (r.title || document.title || location.href).trim(),
      markdown: (r.markdown || "").trim(),
      site: host,
      url: location.href,
      extractor: `custom:${host}`,
    };
  }

  // vendor/defuddle.js is Defuddle's "full" build, whose markdown:true emits
  // Markdown straight into `content` — no separate HTML->Markdown converter.
  const r = new Defuddle(document, { url: location.href, markdown: true }).parse();
  return {
    title: (r.title || document.title || location.href).trim(),
    author: r.author || "",
    published: r.published || "",
    site: r.site || r.domain || host,
    url: location.href,
    markdown: (r.content || "").trim(),
    extractor: r.extractorType || "defuddle",
  };
};
