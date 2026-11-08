// Packs Relit into one self-contained HTML file: page, renderer, libraries
// and the engine (as base64). Usage: node tools/make-single.mjs
// Needs esbuild (npm i esbuild@0.24.0).
import { build } from 'esbuild';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = path.join(path.dirname(fileURLToPath(import.meta.url)), '..');
const out = await build({
  entryPoints: [path.join(root, 'main.js')], bundle: true, format: 'iife', minify: true,
  legalComments: 'eof', write: false, target: 'es2020',
});
const js = out.outputFiles[0].text.replace(/<\/script/gi, '<\\/script');
const wasm = fs.readFileSync(path.join(root, 'doom.wasm')).toString('base64');
const ai = fs.readFileSync(path.join(root, 'relit-ai.bin')).toString('base64');
let html = fs.readFileSync(path.join(root, 'index.html'), 'utf8');
const tag = '<script type="module" src="main.js"></script>';
if (!html.includes(tag)) throw new Error('script tag not found in index.html');
html = html.replace(tag, () => `<script>window.RELIT_WASM="${wasm}";window.RELIT_AI="${ai}";</script>\n<script>${js}</script>`);
fs.mkdirSync(path.join(root, 'dist'), { recursive: true });
fs.writeFileSync(path.join(root, 'dist', 'relit.html'), html);
console.log('dist/relit.html', (html.length / 1024 / 1024).toFixed(2), 'MB');
