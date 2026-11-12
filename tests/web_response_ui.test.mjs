import test from 'node:test';
import assert from 'node:assert/strict';
import { waitingHTML,waitingCopy,responseNotice,canContinue,answerBody } from '../lodestar/web/static/response-ui.mjs';

test('continue requires interrupted prose and copying excludes error chrome',()=>{
  const partial={kind:'error',content:'prose plus failure',metadata:{partial_answer:'prose'}};
  assert.equal(canContinue(partial),true);assert.equal(answerBody(partial),'prose');
  assert.equal(canContinue({kind:'error',metadata:{}}),false);
  assert.equal(canContinue({kind:'text',content:'complete'}),false);
  assert.equal(canContinue({...partial,kind:'cancelled'}),true);
});
test('waiting shows honest phases and elapsed time without invented progress',()=>{
  assert.match(waitingHTML(),/wait-spark/); assert.match(waitingHTML(),/0 秒/);
  assert.doesNotMatch(waitingHTML(),/\d+%/);
  assert.match(waitingCopy('read_paper').detail,/读取相关段落/);
  assert.match(waitingCopy('model_request',15).detail,/还没开始输出/);
  assert.match(waitingCopy('model_request',30).detail,/可以先停止/);
});
test('friendly notices retain diagnostics safely and distinguish cancellation',()=>{
  const failed=responseNotice('error',{model_error:{code:'authentication',http_status:403}});
  assert.match(failed,/这次没接上/); assert.match(failed,/HTTP 403/);
  assert.match(failed,/查看错误详情/);
  assert.doesNotMatch(responseNotice('error',{},'<script>bad</script>'),/<script>/);
  assert.match(responseNotice('cancelled'),/lolo 已停下/);
  assert.doesNotMatch(responseNotice('cancelled'),/查看错误详情/);
});
