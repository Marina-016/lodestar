import test from 'node:test';
import assert from 'node:assert/strict';
import { normalizeAppearance,appearanceTokens,contrastRatio } from '../lodestar/web/static/navigation.mjs';

test('appearance defaults preserve monochrome controls and neutral user bubbles',()=>{
  const tokens=appearanceTokens({});
  assert.equal(tokens['--primary-bg'],'#1d1d1f');
  assert.equal(tokens['--user-bubble-bg'],'#f5f5f7');
  assert.equal(tokens['--user-bubble-ink'],'#1d1d1f');
  assert.equal(tokens['--accent'],'#0071e3');
});
test('stored preferences accept only full hex colors and normalize independently',()=>{
  assert.deepEqual(normalizeAppearance({accent:'#AABBCC',bubble:'#253950',extra:'ignored'}),{accent:'#aabbcc',bubble:'#253950'});
  for(const value of ['url(https://example.test)','red','#fff','#000000ff',';color:red',null,{}])
    assert.deepEqual(normalizeAppearance({accent:value,bubble:value}),{accent:'default',bubble:'default'});
});
test('bubble and button text retain contrast for light, saturated and dark custom colors',()=>{
  for(const r of [0,51,102,153,204,255]) for(const g of [0,51,102,153,204,255]) for(const b of [0,51,102,153,204,255]) {
    const color='#'+[r,g,b].map(v=>v.toString(16).padStart(2,'0')).join('');
    const tokens=appearanceTokens({accent:color,bubble:color});
    assert.ok(contrastRatio(color,tokens['--user-bubble-ink'])>=4.5,color);
    assert.ok(contrastRatio(color,tokens['--primary-fg'])>=4.5,color);
    assert.ok(contrastRatio(tokens['--accent'],'#ffffff')>=4.5,color);
  }
});
test('changing bubble color does not silently change accent and reset covers previous tokens',()=>{
  const bubble=appearanceTokens({bubble:'#253950'});
  assert.equal(bubble['--primary-bg'],'#1d1d1f');
  assert.equal(bubble['--user-bubble-ink'],'#ffffff');
  const colored=appearanceTokens({accent:'#7764c0',bubble:'#253950'}),reset=appearanceTokens({});
  assert.deepEqual(Object.keys(colored).sort(),Object.keys(reset).sort());
  assert.notEqual(colored['--primary-bg'],reset['--primary-bg']);
  assert.equal(reset['--user-bubble-shadow'],'none');
});
