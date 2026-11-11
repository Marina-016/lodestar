import test from 'node:test';
import assert from 'node:assert/strict';
import { splitModelFollowups, storedFollowups, visibleAnswer } from '../lodestar/web/static/followups.mjs';

test('reads up to three model-authored follow-up prompts and removes the trailer', () => {
  const value = '这是实际回答。\n<!--lodestar-followups:{"items":[{"prompt":"请比较两个方案的实现代价。"},{"prompt":"请给我一个最小验证步骤。"}]}-->';
  const parsed = splitModelFollowups(value);
  assert.equal(parsed.answer, '这是实际回答。');
  assert.deepEqual(parsed.items.map((item) => item.prompt), ['请比较两个方案的实现代价。', '请给我一个最小验证步骤。']);
});

test('does not manufacture cards and hides an incomplete streamed trailer', () => {
  assert.deepEqual(storedFollowups(undefined), []);
  assert.deepEqual(splitModelFollowups('普通回答').items, []);
  assert.equal(visibleAnswer('回答正文\n<!--lodestar-followups:{"items":'), '回答正文');
  assert.equal(splitModelFollowups('回答\n<!--lodestar-followups:{bad}-->').answer, '回答\n<!--lodestar-followups:{bad}-->');
});
