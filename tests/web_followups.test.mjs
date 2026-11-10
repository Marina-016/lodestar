import test from 'node:test';
import assert from 'node:assert/strict';
import { suggestedPrompts } from '../lodestar/web/static/followups.mjs';

test('substantive sourced answer offers two evidence-aware next prompts', () => {
  const steps = suggestedPrompts({answer: '这是一个足够长的回答。'.repeat(20), userPrompt: 'OpenAI 最新发布了什么？', sourceCount: 2});
  assert.equal(steps.length, 2);
  assert.match(steps[0].prompt, /来源/);
  assert.match(steps[1].prompt, /下一步/);
});

test('short answers and errors do not manufacture next-step choices', () => {
  assert.deepEqual(suggestedPrompts({answer: '简短回答', userPrompt: '', sourceCount: 0}), []);
  assert.deepEqual(suggestedPrompts({answer: '这是一个足够长的回答。'.repeat(20), error: true}), []);
});
