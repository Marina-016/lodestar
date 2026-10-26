import { escape as esc, linkedUrls, safeUrl } from "./markdown.mjs";

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


export function evidenceHTML(source) {
  const parts = source.evidence_readings || [];
  const depth = value => ({full:"正文片段",abstract:"摘要",web:"网页片段",search_snippet:"检索片段"}[value] || "范围未标注");
  return `<details class="source-evidence" data-evidence-url="${esc(source.url)}"><summary>查看已保存证据 · ${parts.length} 段证据</summary><p class="fineprint">仅显示截至这条回答已保存的片段；不重新联网，不代表覆盖全文或结论已核实。</p>${parts.length ? parts.map((r,i)=>`<section class="evidence-reading"><h4>${i+1}. ${depth(r.read_depth)}</h4><div class="source-meta"><span>${esc(r.source_url || source.url)}</span>${r.message_id!=null ? `<span>消息 #${esc(r.message_id)}</span>` : ""}</div>${r.read_mode === "mock" ? '<p class="evidence-warning">离线夹具，不是真实论文证据。</p>' : ""}${r.note ? `<p class="fineprint">读取说明：${esc(r.note)}</p>` : ""}${r.query ? `<p class="fineprint">查询：${esc(r.query)}</p>` : ""}${r.coverage ? `<p class="fineprint">覆盖：${esc(r.coverage)}</p>` : ""}${r.sections?.length ? `<p class="fineprint">章节：${esc(r.sections.join("、"))}</p>` : ""}${r.truncated || r.context_truncated ? '<p class="evidence-warning">此记录有截断或上下文省略，未显示的内容不能据此判定不存在。</p>' : ""}${r.evidence_spans?.length ? `<p class="fineprint">抽取文本坐标：${r.evidence_spans.map(s=>`${esc(s.start)}–${esc(s.end)}`).join("，")}；是原始抽取坐标，不是此处字符位置或 PDF 页码。</p>` : ""}<pre class="evidence-text">${esc(r.text)}</pre></section>`).join("") : '<p class="empty-state">没有保存可展示的原文片段。引用或阅读深度元数据不能替代正文；早期记录也可能没有保留片段。</p>'}</details>`;
}
