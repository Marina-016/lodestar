import { linkedUrls, safeUrl } from "./markdown.mjs";

function key(value) {
  const safe = safeUrl(value);
  if (!safe) return "";
  const url = new URL(safe);
  url.hash = "";
  return url.href;
}
function paperId(value) {
  const url = new URL(value);
  return url.hostname === "arxiv.org"
    ? url.pathname.match(/^\/abs\/(\d{4}\.\d{4,5})(?:v\d+)?$/)?.[1] : null;
}

// Candidate context stays available for follow-ups. Only this answer's links
// become references; unused observations from this turn stay in the audit list.
export function sourceGroups(message, history = []) {
  const known = new Map(), observed = new Set();
  const add = (source, current = false) => {
    const url = key(source?.url);
    if (!url) return;
    const fields = Object.fromEntries(Object.entries(source).filter(([, v]) => v != null && v !== ""));
    if (known.get(url)?.read_depth === "full" && fields.read_depth !== "full") delete fields.read_depth;
    known.set(url, { ...known.get(url), ...fields, url });
    if (current) observed.add(url);
  };
  const observe = (meta, current) => {
    for (const source of meta.candidates || []) add(source);
    for (const source of meta.source_details || []) add(source);
    for (const event of meta.dialogue_events || []) {
      const result = event.result || {};
      for (const source of result.sources || []) add(source, current);
      if (["read_paper", "read_webpage"].includes(event.action) && !result.error && result.text && event.params?.url)
        add({ ...result, url: event.params.url,
          read_depth: result.read_depth || (event.action === "read_webpage" ? "web" : "abstract") }, current);
      if (event.action === "verify_paper" && !result.error && result.record_url)
        add({ url: result.record_url, title: "论文发表记录", related_paper_url: event.params?.url }, current);
    }
  };
  const index = history.findIndex((m) => m === message || (message?.id != null && String(m.id) === String(message.id)));
  // Resolve older citations from this space's history without importing later
  // observations into an earlier answer or displaying inherited unused sources.
  for (const previous of history.slice(0, Math.max(0, index)))
    if (previous.role === "assistant") observe(previous.metadata || {}, false);
  observe(message?.metadata || {}, true);
  const used = new Set(), references = [];
  for (const link of linkedUrls(message?.content)) {
    const url = key(link);
    let matched = known.has(url) ? url : null;
    // Resolve only a versionless arXiv citation with one unambiguous candidate.
    if (!matched && /^https:\/\/arxiv\.org\/abs\/\d{4}\.\d{4,5}$/.test(url)) {
      const matches = [...known.keys()].filter((value) => paperId(value) === paperId(url));
      if (matches.length === 1) matched = matches[0];
    }
    if (used.has(matched || url)) continue;
    used.add(matched || url);
    references.push(matched ? { ...known.get(matched), url, observed: true }
      : { url, title: url, observed: false });
  }
  return { references, retrieval: [...observed].filter((url) => !used.has(url)).map((url) => known.get(url)) };
}
