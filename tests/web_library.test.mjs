import test from 'node:test';
import assert from 'node:assert/strict';
import { noteRow, noteDetail, projectRow, projectDetail, stamp } from '../lodestar/web/static/library-views.mjs';
const note={id:'m1',content:'<script>untrusted</script>',scope:'project',revision:2,status:'active',source:'user'};
test('memory content and identifiers are escaped, not executable HTML',()=>{
  const html=noteRow({...note,id:'" onclick="bad'},null);
  assert.ok(html.includes('&lt;script&gt;')); assert.ok(!html.includes('<script>'));
  assert.ok(!html.includes('data-entry="" onclick="bad'));
});
test('content history discloses limits and absence of complete historical scope',()=>{
  const html=noteDetail(note,{revisions:[{revision:1,content:'<img src=x>',created_at:'2026-10-23'}],truncated:true});
  assert.ok(html.includes('最近 40')); assert.ok(html.includes('旧版修订没有完整'));
  assert.ok(!html.includes('<img src=x>'));
});
test('project overview distinguishes real checkout from metadata-only import',()=>{
  const p={id:1,name:'Project',memory_count:0,status:'active'};
  assert.ok(projectRow({...p,has_workspace:true},null).includes('Git 工作区'));
  assert.ok(projectRow({...p,repository:{}},null).includes('待建立工作区'));
});
test('repository README is plain escaped data, not remote HTML/images',()=>{
  const html=projectDetail({id:1,name:'Project',description:'',status:'active',memory_count:0,tasks:[],versions:[],
    repository:{repository:'https://github.com/a/b',branch:'master',commit_sha:'a'.repeat(40),allow_model_readme:false},
    readme:'<img src="https://example.invalid/private">',workspace:null},'主对话');
  assert.ok(!html.includes('<img src=')); assert.ok(html.includes('仅本地保存'));
  assert.ok(html.includes('还没有成功建立本地工作区'));
});
test('unsafe repository links and remote commit subjects cannot inject markup',()=>{
  const html=projectDetail({id:1,name:'Project',status:'active',memory_count:0,tasks:[],versions:[],readme:'',
    repository:{repository:'javascript:alert(1)',branch:'master',commit_sha:'a'.repeat(40)},
    workspace:{path:'C:/fixture',branch:'master',commit_sha:'a'.repeat(40),commits:[{sha:'b'.repeat(40),subject:'<svg onload=bad>',date:'2026-10-23'}]}},'主对话');
  assert.ok(!html.includes('href="javascript:')); assert.ok(!html.includes('<svg onload='));
  assert.ok(html.includes('此页不代写代码或自动提交、推送'));
});
test('SQLite UTC timestamps and explicit UTC ISO dates display the same local time',()=>{
  assert.equal(stamp('2026-10-26 01:31:00'),stamp('2026-10-26T01:31:00Z'));
  assert.equal(stamp('<svg>'),'&lt;svg&gt;');
});
