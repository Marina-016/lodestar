import test from 'node:test';
import assert from 'node:assert/strict';
import {companionPreference, companionPose} from '../lodestar/web/static/companion.mjs';

test('companion preferences recover from invalid storage and constrain saved coordinates', () => {
  for (const value of [undefined, null, false, 'oops', {position: {x: Infinity, y: 0}}, {position: {x: '0.4', y: 0.2}}]) {
    assert.deepEqual(companionPreference(value), {visible: true, position: null, size: 'auto', motion: true});
  }
  assert.deepEqual(companionPreference({visible: false, position: {x: -100, y: 100}}), {visible: false, position: {x: 0, y: 1}, size: 'auto', motion: true});
  assert.deepEqual(companionPreference({position: {x: 0.25, y: 0.75}, extra: 'discard'}), {visible: true, position: {x: 0.25, y: 0.75}, size: 'auto', motion: true});
});

test('explicit size survives reload with bounds while invalid and legacy values stay automatic', () => {
  assert.equal(companionPreference({size: -10}).size, 64);
  assert.equal(companionPreference({size: 500}).size, 240);
  assert.equal(companionPreference({size: 180}).size, 180);
  for (const size of ['180', 'auto', NaN, Infinity, null]) assert.equal(companionPreference({size}).size, 'auto');
  assert.equal(companionPreference({motion: false}).motion, false);
});

test('poses reflect actual status and never keep thinking after stop, idle or error', () => {
  assert.equal(companionPose('lolo 接到问题啦，稍等一下…', true), 'thinking');
  assert.equal(companionPose('正在阅读论文…', true), 'reading');
  assert.equal(companionPose('正在搜索网页…', true), 'reading');
  assert.equal(companionPose('lolo 正在写给你…', true), 'writing');
  assert.equal(companionPose('收到，lolo 正在停下…', true), 'idle');
  assert.equal(companionPose('本地服务未连接，请刷新重试', false), 'idle');
  assert.equal(companionPose('', false), 'idle');
});
