import { build } from 'esbuild';
import { readFile, writeFile } from 'node:fs/promises';
import { join } from 'node:path';

const result = await build({
  stdin: { contents: "import mermaid from 'mermaid'; import DOMPurify from 'dompurify'; window.LodestarDiagram = {mermaid, sanitize: value => DOMPurify.sanitize(value, {USE_PROFILES:{svg:true, svgFilters:true}, FORBID_TAGS:['foreignObject','a','image','script']})};", resolveDir: process.cwd() },
  outfile: 'lodestar/web/static/mermaid.bundle.js',
  bundle: true, format: 'iife', platform: 'browser', target: ['es2022'],
  minify: true, legalComments: 'linked', metafile: true,
});
// Preserve package license notices alongside the distributable; no Node runtime
// or network connection is needed to render diagrams in the installed app.
const roots = new Set();
for (const file of Object.keys(result.metafile.inputs)) {
  const match = file.match(/^(.*node_modules\/(?:@[^/]+\/)?[^/]+)\//);
  if (match) roots.add(match[1]);
}
const notices = ['Lodestar diagram renderer — bundled third-party notices\n'];
for (const root of [...roots].sort()) {
  let pkg;
  try { pkg = JSON.parse(await readFile(join(root, 'package.json'), 'utf8')); } catch { continue; }
  let license = '';
  for (const file of ['LICENSE', 'LICENSE.md', 'LICENSE.txt', 'license', 'license.md', 'LICENSE-MIT']) {
    try { license = await readFile(join(root, file), 'utf8'); break; } catch {}
  }
  notices.push(`\n--- ${pkg.name}@${pkg.version} (${pkg.license || 'see package'}) ---\n${license}`);
}
await writeFile('lodestar/web/static/mermaid-notices.txt', notices.join('\n'));
