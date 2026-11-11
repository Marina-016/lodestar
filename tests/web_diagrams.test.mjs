import test from 'node:test';
import assert from 'node:assert/strict';
import { markdown, linkedUrls } from '../lodestar/web/static/markdown.mjs';

test('only complete Mermaid fences produce diagram cards with escaped source', () => {
  const code = 'flowchart LR\n  A["<img src=x onerror=alert(1)>"] --> B["结束"]';
  const html = markdown('```mermaid\n'+code+'\n```');
  assert.ok(html.includes('class="diagram-card"'));
  assert.ok(html.includes('&lt;img'));
  assert.ok(!html.includes('<img src=x'));
  assert.ok(html.includes('下载 SVG') && html.includes('下载 PNG'));
  assert.deepEqual(linkedUrls('```mermaid\nflowchart LR\n A["https://example.com"]\n```'),[]);
});

test('partial streaming diagrams stay pending and ordinary code stays code', () => {
  const pending = markdown('前言\n\n```mermaid\nflowchart LR\nA --> B');
  assert.ok(pending.includes('图表生成中'));
  assert.ok(!pending.includes('diagram-card'));
  assert.ok(!pending.includes('A --&gt; B'));
  assert.ok(!markdown('```python\nprint(1)\n```').includes('diagram-card'));
  assert.ok(markdown('```mermaid\nmindmap\n root((记忆))\n  短期\n  长期\n```').includes('层级图'));
});
