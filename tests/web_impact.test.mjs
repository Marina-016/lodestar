import test from 'node:test';
import assert from 'node:assert/strict';
import { impactPanel,impactReference } from '../lodestar/web/static/impact.mjs';

test('citation cards distinguish zero, missing, percentile and stale evidence',()=>{
  const impact={recent:true,providers:{semantic_scholar:{status:'ok',fetched_at:'2026-11-11T00:00:00Z',
    data:{citation_count:0,influential_citation_count:null}},openalex:{status:'rate_limited',stale:true,
    data:{citation_count:12,fwci:1.75,citation_percentile:.92,subfield:'<script>bad</script>'}}}};
  const html=impactPanel(impact);
  assert.match(html,/>0<\/dd>/); assert.match(html,/未知/); assert.match(html,/92\.0%/);
  assert.match(html,/影响力尚待观察/); assert.match(html,/接口限流/); assert.match(html,/保留上次数据/);
  assert.doesNotMatch(html,/<script>/);
  const reference=impactReference(impact);
  assert.match(reference,/Semantic Scholar：引用数 0/); assert.match(reference,/OpenAlex：引用数 12/);
  assert.match(reference,/旧数据待刷新/); assert.match(reference,/不能当作质量评分/);
});

test('opening an empty panel only offers an explicit query and unsafe links stay inert',()=>{
  assert.match(impactPanel(),/查询指标/);
  assert.equal(impactReference({providers:{}}),'');
  const html=impactPanel({providers:{openalex:{data:{record_url:'javascript:alert(1)',fwci:NaN,citation_percentile:2}}}});
  assert.doesNotMatch(html,/javascript:/); assert.doesNotMatch(html,/200\.0%/);
  assert.doesNotMatch(impactPanel({supported:false,note:'请提供 DOI'}),/data-action="refresh-impact"/);
});
