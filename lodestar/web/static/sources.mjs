import { displayHost, escape as esc, linkedUrls, safeUrl } from "./markdown.mjs";

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
  let messageId = null;
  const add = (source, current = false) => {
    const url = key(source?.url);
    if (!url) return;
    const fields = Object.fromEntries(Object.entries(source).filter(([, v]) => v != null && v !== ""));
    if (known.get(url)?.read_depth === "full" && fields.read_depth !== "full") delete fields.read_depth;
    const previous = known.get(url) || {};
    const readings = [...(previous.evidence_readings || [])];
    for (const part of (Array.isArray(source.readings) && source.readings.length ? source.readings : source.content ? [{...source,text:source.content}] : [])) {
      if (typeof part.text !== "string" || !part.text) continue;
      const reading = {...part, read_depth:part.read_depth || source.read_depth || "unknown",
        query:part.query ?? source.read_query ?? "", message_id:messageId, source_url:url};
      if (!readings.some(r=>r.text===reading.text && r.query===reading.query && r.read_depth===reading.read_depth)) readings.push(reading);
    }
    known.set(url, { ...previous, ...fields, url, evidence_readings:readings });
    if (current) observed.add(url);
  };
  const observe = (meta, current, id = null) => {
    messageId = id;
    for (const source of meta.candidates || []) add(source);
    for (const source of meta.source_details || []) add(source);
    for (const event of meta.dialogue_events || []) {
      const result = event.result || {};
      for (const source of result.sources || []) add(source, current);
      if (["read_paper", "read_webpage"].includes(event.action) && !result.error && result.text && event.params?.url)
        add({ ...result, url: event.params.url,
          read_depth: result.read_depth || (event.action === "read_webpage" ? "web" : "abstract"),
          readings:[{...result, query:event.params?.query || ""}] }, current);
      if (event.action === "verify_paper" && !result.error && result.record_url)
        add({ url: result.record_url, title: "论文发表记录", related_paper_url: event.params?.url }, current);
    }
  };
  const index = history.findIndex((m) => m === message || (message?.id != null && String(m.id) === String(message.id)));
  // Resolve older citations from this space's history without importing later
  // observations into an earlier answer or displaying inherited unused sources.
  for (const previous of history.slice(0, Math.max(0, index)))
    if (previous.role === "assistant") observe(previous.metadata || {}, false, previous.id);
  observe(message?.metadata || {}, true, message?.id);
  for (const source of message?.metadata?.referenced_papers || []) add(source);
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
  const body = String(message?.content || '').replace(/\*\*/g, '').toLocaleLowerCase();
  const attached = new Set((message?.metadata?.referenced_papers || []).map(s => key(s.url)));
  for (const [url, source] of known) {
    // Support older answers that named the full paper title but omitted a link.
    // Do not promote inherited, unused candidates into answer references.
    const title = String(source.title || '').toLocaleLowerCase();
    const named = title.length >= 12 && !title.startsWith('http') && body.includes(title);
    if ((attached.has(url) || named) && !used.has(url)) {
      used.add(url);
      references.push({...source, observed: true});
    }
  }
  return { references, retrieval: [...observed].filter((url) => !used.has(url)).map((url) => known.get(url)) };
}


export function evidenceHTML(source) {
  const parts = source.evidence_readings || [];
  const depth = value => ({full:"正文片段",abstract:"论文摘要",web:"网页片段",search_snippet:"检索摘要"}[value] || "原文片段");
  const readings = parts.map((r, i) => {
    const technical = [
      r.message_id != null ? `<p>对应消息：#${esc(r.message_id)}</p>` : "",
      r.query ? `<p>检索问题：${esc(r.query)}</p>` : "",
      r.coverage ? `<p>读取范围标识：<code>${esc(r.coverage)}</code></p>` : "",
      r.note ? `<p>读取说明：${esc(r.note)}</p>` : "",
      r.sections?.length ? `<p>章节记录：${esc(r.sections.join("、"))}</p>` : "",
      r.evidence_spans?.length ? `<p>抽取文本坐标：${r.evidence_spans.map(s => `${esc(s.start)}–${esc(s.end)}`).join("，")}；是原始抽取坐标，不是此处字符位置或 PDF 页码。</p>` : "",
    ].filter(Boolean).join("");
    return `<section class="evidence-reading"><h4>${parts.length > 1 ? `${i+1}. ` : ""}${depth(r.read_depth)}</h4><div class="source-meta"><span>来源 · ${esc(displayHost(r.source_url || source.url) || "未标注")}</span></div>${r.read_mode === "mock" ? '<p class="evidence-warning">离线夹具，不是真实论文证据。</p>' : ""}<pre class="evidence-text" tabindex="0" aria-label="已读取的原文片段">${esc(r.text)}</pre>${r.truncated || r.context_truncated ? '<p class="evidence-note">此片段已截断，仅展示部分原文。</p>' : ""}${technical ? `<details class="evidence-technical"><summary>技术详情</summary><div>${technical}</div></details>` : ""}</section>`;
  }).join("");
  return `<details class="source-evidence" data-evidence-url="${esc(source.url)}"><summary>查看阅读片段${parts.length ? ` · ${parts.length} 段` : ""}</summary>${parts.length ? `<p class="evidence-note">以下是当时读取的原文节选，不代表已阅读全文。</p>${readings}` : '<p class="empty-state">没有保存可展示的原文片段，可点击来源标题查看原文。</p>'}</details>`;
}
