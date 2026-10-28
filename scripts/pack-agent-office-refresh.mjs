#!/usr/bin/env node
// Pack generated art; no artwork generation, service calls, or deployment.
import { readFile, writeFile } from 'node:fs/promises';
import { createHash } from 'node:crypto';
import { execFileSync } from 'node:child_process';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';
import { parseArgs } from 'node:util';
import { resolve } from 'node:path';

const { values } = parseArgs({ options: { 'image-library': { type: 'string' } } });
const sharp = createRequire(import.meta.url)(values['image-library'] ? resolve(values['image-library']) : 'sharp');
if (sharp.versions.sharp !== '0.35.5') throw new Error('Use sharp 0.35.5 for reproducible packing');
const root = fileURLToPath(new URL('../', import.meta.url));
const art = resolve(root, 'deploy/agent-office/art'), assets = resolve(root, 'deploy/agent-office/public/assets');
const spec = JSON.parse(await readFile(resolve(art, 'robot-refresh-prompts.json'), 'utf8'));
const base = name => execFileSync('git', ['show', `${spec.reference.revision}:deploy/agent-office/public/assets/${name}`], { cwd: root, maxBuffer: 20 * 1024 * 1024 });
const hash = data => createHash('sha256').update(data).digest('hex');
const blank = (width, height) => sharp({ create: { width, height, channels: 4, background: '#00000000' } });
const raw = buffer => sharp(buffer).ensureAlpha().raw().toBuffer();
const sheets = ['coordination', 'operations', 'household', 'planning'];
const layers = new Map(), frames = new Map(), receipt = [];

// Remove isolated generation specks while retaining separate hands and tools.
async function clean(buffer) {
  const { data, info } = await sharp(buffer).ensureAlpha().raw().toBuffer({ resolveWithObject: true });
  const visited = new Uint8Array(info.width * info.height), components = [];
  for (let p = 0; p < visited.length; p++) {
    if (visited[p] || data[p * 4 + 3] < 32) continue;
    const points = [p]; visited[p] = 1;
    for (let i = 0; i < points.length; i++) {
      const current = points[i], x = current % info.width, y = Math.floor(current / info.width);
      for (const next of [x > 0 ? current - 1 : -1, x + 1 < info.width ? current + 1 : -1,
        y > 0 ? current - info.width : -1, y + 1 < info.height ? current + info.width : -1]) {
        if (next < 0 || visited[next] || data[next * 4 + 3] < 32) continue;
        visited[next] = 1; points.push(next);
      }
    }
    components.push(points);
  }
  const minimum = Math.max(8, Math.max(...components.map(c => c.length)) * .002), kept = new Uint8Array(visited.length);
  for (const component of components) if (component.length >= minimum) for (const p of component) kept[p] = 1;
  for (let p = 0; p < kept.length; p++) if (!kept[p]) data.fill(0, p * 4, p * 4 + 4);
  return sharp(data, { raw: info }).png().toBuffer();
}

// Trim alpha gutters and anchor on the head so tools do not shift the body.
async function bounds(buffer) {
  const { data, info } = await sharp(buffer).ensureAlpha().raw().toBuffer({ resolveWithObject: true });
  let left = info.width, top = info.height, right = -1, bottom = -1;
  for (let y = 0; y < info.height; y++) for (let x = 0; x < info.width; x++) {
    if (data[(y * info.width + x) * 4 + 3] < 32) continue;
    left = Math.min(left, x); right = Math.max(right, x); top = Math.min(top, y); bottom = Math.max(bottom, y);
  }
  if (right < left) throw new Error('Empty source pose');
  const headPixels = [];
  for (let y = top; y <= top + (bottom - top) * .28; y++) for (let x = left; x <= right; x++) {
    if (data[(y * info.width + x) * 4 + 3] < 32) continue;
    headPixels.push(x);
  }
  headPixels.sort((a, b) => a - b);
  return { left, top, width: right - left + 1, height: bottom - top + 1, anchor: headPixels[Math.floor(headPixels.length / 2)] };
}

for (const design of spec.designs) {
  const source = await readFile(resolve(art, design.source)), meta = await sharp(source).metadata(), cells = [];
  if (!meta.hasAlpha) throw new Error(`${design.name}: RGBA source required`);
  for (const row of design.sourceRows) for (let column = 0; column < 5; column++) {
    const left = Math.round(column * meta.width / 5), top = Math.round(row * meta.height / 4);
    const width = Math.round((column + 1) * meta.width / 5) - left, height = Math.round((row + 1) * meta.height / 4) - top;
    const buffer = await clean(await sharp(source).extract({ left, top, width, height }).png().toBuffer());
    cells.push({ buffer, box: await bounds(buffer) });
  }
  const scale = Math.min(design.height / Math.max(...cells.map(c => c.box.height)),
    56 / Math.max(...cells.map(c => Math.max(c.box.anchor - c.box.left, c.box.left + c.box.width - c.box.anchor))));
  const normalized = [];
  for (const { buffer, box } of cells) {
    const width = Math.max(1, Math.round(box.width * scale)), height = Math.max(1, Math.round(box.height * scale));
    const input = await sharp(buffer).extract({ left: box.left, top: box.top, width: box.width, height: box.height })
      .resize(width, height, { kernel: 'nearest' }).png().toBuffer();
    const left = Math.round(64 - (box.anchor - box.left) * scale), top = 154 - (design.name === 'Nova' ? 12 : 0) - height;
    if (left < 0 || left + width > 128 || top < 0) throw new Error(`${design.name}: clipped pose`);
    normalized.push(await blank(128, 160).composite([{ input, left, top }]).png().toBuffer());
  }
  frames.set(design.index, normalized);
  const sheet = sheets[Math.floor(design.index / 3)], row = design.index % 3;
  for (const mode of ['walk', 'work']) {
    const name = `${mode}-${sheet}.png`, columns = mode === 'walk' ? 12 : 8, poses = [];
    for (let direction = 0; direction < 4; direction++) {
      const offset = direction * 5;
      poses.push({ input: normalized[offset + (mode === 'walk' ? 1 : 3)], left: direction * 256, top: 0 });
      poses.push({ input: normalized[offset + (mode === 'walk' ? 2 : 4)], left: direction * 256 + 128, top: 0 });
      if (mode === 'walk') poses.push({ input: normalized[offset], left: (8 + direction) * 128, top: 0 });
    }
    const input = await blank(columns * 128, 160).composite(poses).png().toBuffer(), pending = layers.get(name) ?? [];
    pending.push({ input, left: 0, top: row * 160, blend: 'source' }); layers.set(name, pending);
  }
  receipt.push({ name: design.name, index: design.index, source: `../../art/${design.source}`, sha256: hash(source), sourceRows: design.sourceRows,
    maximumHeight: Math.max(...cells.map(c => Math.round(c.box.height * scale))) });
}

// Compare decoded pixels, since PNG encoding can change without changing art.
async function preserve(before, after, region) {
  const a = await raw(await sharp(before).extract(region).png().toBuffer());
  const b = await raw(await sharp(after).extract(region).png().toBuffer());
  if (!a.equals(b)) throw new Error('Changed protected robot pixels');
}
const metadata = JSON.parse(base('walking-art.json'));
// Direct RGBA copying avoids premultiplication roundoff in protected pixels.
async function replace(original, overlays) {
  const { data, info } = await sharp(original).ensureAlpha().raw().toBuffer({ resolveWithObject: true });
  for (const { input, left, top } of overlays) {
    const overlay = await sharp(input).ensureAlpha().raw().toBuffer({ resolveWithObject: true });
    for (let y = 0; y < overlay.info.height; y++) {
      const start = y * overlay.info.width * 4;
      overlay.data.copy(data, ((top + y) * info.width + left) * 4, start, start + overlay.info.width * 4);
    }
  }
  return sharp(data, { raw: info }).png().toBuffer();
}
for (const [name, overlays] of layers) {
  const original = base(name), output = await replace(original, overlays);
  const width = (await sharp(output).metadata()).width;
  for (let row = 0; row < 3; row++) if (!overlays.some(layer => layer.top === row * 160)) {
    await preserve(original, output, { left: 0, top: row * 160, width, height: 160 });
  }
  await writeFile(resolve(assets, name), output); metadata.sha256[name] = hash(output);
}

const original = base('agents.png'), portraitMeta = await sharp(original).metadata(), portraits = [];
function portraitRegion(index) {
  const left = Math.round((index % 4) * portraitMeta.width / 4), top = Math.round(Math.floor(index / 4) * portraitMeta.height / 3);
  return { left, top, width: Math.round((index % 4 + 1) * portraitMeta.width / 4) - left,
    height: Math.round((Math.floor(index / 4) + 1) * portraitMeta.height / 3) - top };
}
for (const design of spec.designs) {
  const { left, top, width, height } = portraitRegion(design.index);
  const input = await sharp(frames.get(design.index)[0]).resize(width, height, { fit: 'contain', kernel: 'nearest', background: '#00000000' }).png().toBuffer();
  portraits.push({ input, left, top, blend: 'source' });
}
const portrait = await replace(original, portraits);
for (const index of [0, 1, 4, 5, 7]) await preserve(original, portrait, portraitRegion(index));
await writeFile(resolve(assets, 'agents.png'), portrait); metadata.sha256['agents.png'] = hash(portrait);
metadata.refresh = { date: '2026-10-28', baseRevision: spec.reference.revision, preserved: ['Atlas', 'Luna', 'Orion', 'Vega', 'Lyra'],
  prompts: '../../art/robot-refresh-prompts.json', packer: '../../../../scripts/pack-agent-office-refresh.mjs',
  normalization: '5x4 source cells; isolated alpha specks removed; shared scale across all 20 poses per character; upper-body alpha median anchor; nearest-neighbor resize; baseline 154; Nova hover offset 12. Base normalization applies only to preserved rows.', sources: receipt };
await writeFile(resolve(assets, 'walking-art.json'), JSON.stringify(metadata, null, 2) + '\n');

const preview = [];
for (const [i, design] of spec.designs.entries()) {
  const left = (i % 4) * 288, top = Math.floor(i / 4) * 260;
  preview.push({ input: frames.get(design.index)[0], left: left + 12, top: top + 36 });
  preview.push({ input: frames.get(design.index)[3], left: left + 144, top: top + 36 });
  const label = Buffer.from(`<svg width="288" height="52"><text x="144" y="23" fill="#e4eaf2" text-anchor="middle" font-family="sans-serif" font-size="18">${design.name}</text><text x="144" y="44" fill="#a5b3c6" text-anchor="middle" font-family="sans-serif" font-size="12">${design.kind}</text></svg>`);
  preview.push({ input: label, left, top: top + 202 });
}
await sharp({ create: { width: 1152, height: 520, channels: 4, background: '#172232' } }).composite(preview).png().toFile(resolve(art, 'robot-refresh-preview.png'));
console.log('Packed seven designs; the other five robots remain pixel-identical.');
