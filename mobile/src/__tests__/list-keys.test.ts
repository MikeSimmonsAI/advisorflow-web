/**
 * NO SCREEN IN THIS APP MAY KEY A LIST ON ITS LOOP INDEX.
 *
 * The bug Mike hit — "Encountered two children with the same key" on Manager
 * Today — was one instance of a pattern this codebase had sixteen of:
 *
 *     key={String(record.id ?? i)}
 *
 * It reads as defensive and is the opposite. When the id is present but
 * REPEATED (one deal raising two exceptions, one meeting listed under two
 * attendees, two audit rows sharing a join id) the fallback never runs and both
 * rows get the same key. When the id is absent the key becomes a position,
 * which is stable only until the list reorders — at which point React reuses
 * the wrong row's state.
 *
 * `rowKey(parts, i)` fixes both: every discriminator participates, and the
 * index is appended rather than substituted.
 *
 * This test walks every screen. It is deliberately a source sweep — a rendered
 * test needs a payload, and the whole failure was that the real payload was not
 * the assumed one.
 */

import fs from 'fs';
import path from 'path';

const APP = path.join(__dirname, '..', '..', 'app');

function screens(dir: string, out: string[] = []): string[] {
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) screens(full, out);
    else if (entry.name.endsWith('.tsx')) out.push(full);
  }
  return out;
}

const FILES = screens(APP);

describe('list keys', () => {
  it('found the screens to check', () => {
    expect(FILES.length).toBeGreaterThan(20);
  });

  it.each(FILES.map((f) => [path.relative(APP, f), f]))(
    '%s does not key on the loop index', (_name, file) => {
      const src = fs.readFileSync(file as string, 'utf8')
        .replace(/\/\*[\s\S]*?\*\//g, ' ')
        .replace(/^\s*\/\/.*$/gm, ' ');

      // `key={i}` — a pure position.
      expect(src).not.toMatch(/key=\{\s*(i|idx|index)\s*\}/);
      // `key={String(x ?? i)}` — a position that only appears when the id is
      // missing, and never when it merely repeats.
      expect(src).not.toMatch(/key=\{String\([^}]*\?\?\s*(i|idx|index)\s*\)\}/);
    });

  it.each(FILES.map((f) => [path.relative(APP, f), f]))(
    '%s gives every keyed list a key expression', (_name, file) => {
      const src = fs.readFileSync(file as string, 'utf8');
      // Every `key={` opens with something. An empty key is a crash waiting.
      expect(src).not.toMatch(/key=\{\s*\}/);
      expect(src).not.toMatch(/key=""/);
    });
});
