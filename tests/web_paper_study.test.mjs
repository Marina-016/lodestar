import test from 'node:test';
import assert from 'node:assert/strict';
import {studyPanel,observationText} from '../lodestar/web/static/paper-study.mjs';

test('study notes, observations and origin identifiers cannot inject markup',()=>{
 const attack='<img src=x onerror=alert(1)>';
 const html=studyPanel({paper:{study:{status:'later',mastery:'unknown',note:'</textarea>'+attack},evidence_mastery:'unknown'},
  evidence:[{technology:attack,method:attack,event:'explained',evidence:attack}],
  origins:[{item_id:'" onclick="alert(1)',message_id:8,label:attack}]});
 assert.ok(!html.includes('<img'));assert.ok(!html.includes('</textarea><img'));
 assert.ok(html.includes('&lt;img'));assert.ok(html.includes('item=%22%20onclick%3D%22alert(1)'));
 assert.ok(html.includes('尚无掌握验证'));assert.ok(html.includes('助手讲解'));
});

test('a self-report correction is readable without promoting it to a demonstrated skill',()=>{
 const evidence=JSON.stringify({message_id:12,content:'我只看了摘要，还不会实现。',corrects:4});
 assert.equal(observationText(evidence),'我只看了摘要，还不会实现。');
 assert.equal(observationText('not JSON'),'not JSON');
 const html=studyPanel({paper:{study:{status:'read',mastery:'unknown',note:''},evidence_mastery:'unknown'},
  evidence:[{technology:'缓存',method:'对齐',event:'self_report',evidence}],origins:[]});
 assert.ok(html.includes('我只看了摘要，还不会实现。'));
 assert.ok(html.includes('用户自述'));assert.ok(html.includes('尚无掌握验证'));
});
