import test from 'node:test';
import assert from 'node:assert/strict';
import { streamDisplay } from '../lodestar/web/static/stream.mjs';

test('network batches render progressively without splitting emoji and stop cleanly', () => {
  let nextId = 0;
  const frames = new Map(), rendered = [];
  const display = streamDisplay(text => rendered.push(text), {
    schedule: cb => { frames.set(++nextId, cb); return nextId; },
    cancel: id => frames.delete(id),
  });
  const text = '新的内容🙂'.repeat(15);
  display.update(text);
  for (let time = 40; frames.size && time < 4000; time += 40) {
    const batch = [...frames.values()]; frames.clear();
    for (const cb of batch) cb(time);
  }
  assert.ok(rendered.length > 1);
  assert.ok(rendered[0].length < text.length);
  assert.equal(rendered.at(-1), text);
  assert.ok(rendered.every(value => text.startsWith(value) && !/[\uD800-\uDBFF]$/.test(value)));
  display.update(text + '追加');
  display.stop();
  assert.equal(frames.size, 0);
});

test('reading above the stream pauses paint while new text accumulates and catches up on return',()=>{
  const frames=new Map(),rendered=[];let id=0;
  const display=streamDisplay(text=>rendered.push(text),{
    schedule:cb=>{frames.set(++id,cb);return id;},cancel:id=>frames.delete(id),
  });
  display.update('最初的正文');
  display.pause();
  assert.equal(frames.size,0);
  display.update('最初的正文🙂更多内容');display.update('最初的正文🙂更多内容\n完整的尾部');
  assert.equal(frames.size,0);assert.equal(rendered.length,0);
  display.resume();
  assert.deepEqual(rendered,['最初的正文🙂更多内容\n完整的尾部']);
  display.stop();display.pause();display.resume();
  assert.equal(rendered.length,1);
});
test('returning before the first token preserves the waiting card',()=>{
  const rendered=[];
  const display=streamDisplay(text=>rendered.push(text),{schedule:()=>1,cancel:()=>{}});
  display.pause();display.resume();
  assert.deepEqual(rendered,[]);
  display.stop();
});
