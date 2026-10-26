import test from 'node:test';
import assert from 'node:assert/strict';
import { defaultModel, modelOptionsHTML, hiddenModelsHTML } from '../lodestar/web/static/models.mjs';

const data = (changes = {}) => ({item:{settings:'{}'},configuration:{model:'bad'},
  model_selection:{current_blocked:true,recommended_model:'good'},...changes});

test('exhausted current model is never reinserted in options', () => {
  const html = modelOptionsHTML(['good'],'bad',{good:'recently_available'});
  assert.ok(!html.includes('value="bad"'));
  assert.ok(html.includes('请选择模型'));
  assert.ok(html.includes('最近可用'));
});
test('unverified is not presented as working or free', () => {
  assert.ok(modelOptionsHTML(['unknown'],'unknown').includes('未验证'));
  assert.equal(defaultModel(data({model_selection:{recommended_model:null}})),null);
});
test('fresh default never switches active runs or explicit manual choices', () => {
  assert.equal(defaultModel(data()),'good');
  assert.equal(defaultModel(data({run:{status:'running'}})),null);
  assert.equal(defaultModel(data({item:{settings:'{"model":"bad"}'},
    model_selection:{current_blocked:false,recommended_model:'good'}})),null);
});
test('models and recovery controls escape untrusted IDs and reason strings', () => {
  const bad = '<img src=x onerror=alert(1)>';
  const html = modelOptionsHTML([bad],bad) + hiddenModelsHTML([{model:bad,reason:bad}]);
  assert.ok(!html.includes('<img'));
  assert.ok(html.includes('&lt;img'));
  assert.ok(html.includes('重新允许尝试'));
});
