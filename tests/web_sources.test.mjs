import test from "node:test";
import assert from "node:assert/strict";
import { sourceGroups, evidenceHTML } from "../lodestar/web/static/sources.mjs";
import { linkedUrls, markdown } from "../lodestar/web/static/markdown.mjs";

const paper = { url: "https://arxiv.org/abs/2601.00001v1", title: "Useful paper" };
const junk = { url: "https://dictionary.example/most", title: "Meaning of most" };
const old = { url: "https://video.example/", title: "Old video homepage" };
const search = (sources) => ({ action: "search_web", result: { sources } });

test("references contain cited pages; unused current results remain auditable", () => {
  const groups = sourceGroups({ content: `[paper](${paper.url})`, metadata: {
    candidates: [old, junk, paper], dialogue_events: [search([junk, paper])] } });
  assert.deepEqual(groups.references.map((s) => s.title), ["Useful paper"]);
  assert.deepEqual(groups.retrieval.map((s) => s.title), ["Meaning of most"]);
});
test("follow-up citation may reuse evidence without pretending to search again", () => {
  const groups = sourceGroups({ content: `[explain](${paper.url})`, metadata: {
    candidates: [paper, old], source_details: [{ ...paper, read_depth: "full" }] } });
  assert.equal(groups.references[0].read_depth, "full");
  assert.deepEqual(groups.retrieval, []);
});
test("changing topic does not display inherited candidate lists", () => {
  assert.deepEqual(sourceGroups({ content: "Hello", metadata: { candidates: [old, paper] } }),
    { references: [], retrieval: [] });
});
test("code examples are not citations and link order matches the answer", () => {
  const text = `\`https://code.example/\`\n\n\`\`\`python\nurl = "https://code.example/"\n\`\`\`\n\n[second](https://two.example/?a=1&b=2) [first](https://one.example/) [again](https://one.example/#part)`;
  const groups = sourceGroups({ content: text });
  assert.deepEqual(groups.references.map((s) => s.url), ["https://two.example/?a=1&b=2", "https://one.example/"]);
  assert.ok(markdown(text).includes("a=1&amp;b=2"));
  assert.equal(linkedUrls(text).length, 3);
});
test("unobserved answer links are disclosed; unsafe links are excluded", () => {
  const groups = sourceGroups({ content: "[unverified](https://unknown.example/) [unsafe](javascript:alert)" });
  assert.equal(groups.references.length, 1);
  assert.equal(groups.references[0].observed, false);
});
test("versionless arXiv citation resolves only one known version", () => {
  const content = "[paper](https://arxiv.org/abs/2601.00001)";
  const unique = sourceGroups({ content, metadata: { candidates: [paper], dialogue_events: [search([paper])] } });
  assert.equal(unique.references[0].title, paper.title);
  assert.equal(unique.retrieval.length, 0);
  const ambiguous = sourceGroups({ content, metadata: { candidates: [paper, { ...paper, url: paper.url.replace('v1','v2') }] } });
  assert.equal(ambiguous.references[0].observed, false);
});
test("different explicit arXiv versions retain separate provenance", () => {
  const groups = sourceGroups({ content: "[v2](https://arxiv.org/abs/2601.00001v2)", metadata: { candidates: [paper] } });
  assert.equal(groups.references[0].observed, false);
});
test("successful webpage reads have a webpage depth; failed reads add no evidence", () => {
  const url = "https://official.example/release";
  const groups = sourceGroups({ content: `[release](${url})`, metadata: { dialogue_events: [
    { action: "read_webpage", params: { url }, result: { title: "Release", text: "Body excerpt" } },
    { action: "read_webpage", params: { url: old.url }, result: { error: "timeout" } }] } });
  assert.equal(groups.references[0].read_depth, "web");
  assert.deepEqual(groups.retrieval, []);
});
test("later abstract reads do not erase persisted body-reading depth", () => {
  const groups = sourceGroups({ content: `[paper](${paper.url})`, metadata: {
    source_details: [{ ...paper, read_depth: "full" }], dialogue_events: [
      { action: "read_paper", params: { url: paper.url }, result: { text: "Abstract", read_depth: "abstract" } }] } });
  assert.equal(groups.references[0].read_depth, "full");
});

test("older provenance fills evicted candidates without showing unused history", () => {
  const earlier = { id: 1, role: "assistant", metadata: { candidates: [paper, old] } };
  const current = { id: 2, role: "assistant", content: `[paper](${paper.url})` };
  const groups = sourceGroups(current, [earlier, current]);
  assert.equal(groups.references[0].title, paper.title);
  assert.equal(groups.references[0].observed, true);
  assert.deepEqual(groups.retrieval, []);
});
test("future observations cannot retroactively verify an earlier answer", () => {
  const earlier = { id: 1, role: "assistant", content: `[paper](${paper.url})` };
  const future = { id: 2, role: "assistant", metadata: { candidates: [paper] } };
  const groups = sourceGroups(earlier, [earlier, future]);
  assert.equal(groups.references[0].observed, false);
});


test("saved excerpts keep their own depth when later reads return abstract", () => {
  const message = { id: 7, content: `[paper](${paper.url})`, metadata: {dialogue_events: [
    {action:"read_paper",params:{url:paper.url,query:"experiments"},result:{text:"Measured evidence",read_depth:"full",coverage:"query_excerpt",truncated:true,evidence_spans:[{start:40,end:57}],sections:["experiments"]}},
    {action:"read_paper",params:{url:paper.url},result:{text:"Short abstract",read_depth:"abstract"}}
  ]}};
  const source = sourceGroups(message).references[0];
  assert.equal(source.evidence_readings.length,2);
  assert.equal(source.evidence_readings[0].text,"Measured evidence");
  assert.equal(source.evidence_readings[0].read_depth,"full");
  assert.equal(source.evidence_readings[0].message_id,7);
  assert.equal(source.evidence_readings[1].read_depth,"abstract");
});
test("evidence is bounded to the selected answer history and exact paper version", () => {
  const make=(id,url,text)=>({id,role:"assistant",content:`[paper](${url})`,metadata:{dialogue_events:[{action:"read_paper",params:{url},result:{text,read_depth:"full"}}]}});
  const old=make(1,paper.url,"Old body"), current=make(2,paper.url.replace("v1","v2"),"New body"),future=make(3,paper.url,"Future body");
  assert.deepEqual(sourceGroups(old,[old,current,future]).references[0].evidence_readings.map(r=>r.text),["Old body"]);
  assert.deepEqual(sourceGroups(current,[old,current,future]).references[0].evidence_readings.map(r=>r.text),["New body"]);
});
test("metadata-only child provenance never creates an original excerpt", () => {
  const groups=sourceGroups({content:`[paper](${paper.url})`,metadata:{source_details:[{...paper,read_depth:"full",linked_task_source:{task_id:"child"}}]}});
  assert.deepEqual(groups.references[0].evidence_readings,[]);
});

test("saved evidence escapes source content and labels missing text honestly",()=>{
 const html=evidenceHTML({url:paper.url,evidence_readings:[{text:'<img src=x onerror=alert(1)>',query:'<script>',read_depth:'full',truncated:true,evidence_spans:[{start:5,end:20}]}]});
 assert.ok(!html.includes('<img'));
 assert.ok(html.includes('&lt;img'));
 assert.ok(html.includes('不是此处字符位置或 PDF 页码'));
 assert.ok(html.includes('截断'));
 assert.ok(evidenceHTML({...paper,read_depth:'full'}).includes('没有保存可展示的原文片段'));
});

test("original-message links allow only the owned history route",()=>{
 assert.ok(markdown('[old](/history?item=task_1&message=123)').includes('href="/history?item=task_1&amp;message=123"'));
 assert.ok(!markdown('[bad](/history?item=x&message=1&redirect=https://evil.example)').includes('<a'));
 assert.deepEqual(linkedUrls('[old](/history?item=main&message=1)'),[]);
});
