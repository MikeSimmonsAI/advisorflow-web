/**
 * A REGRESSION GUARD WITH A LONG MEMORY.
 *
 * `src/push/runtime.ts` only protects Expo Go for as long as nothing else in
 * the app imports `expo-notifications` statically. One `import * as
 * Notifications from 'expo-notifications'` added to any screen — by anybody, at
 * any point — puts the module back in the launch graph and Expo Go crashes on
 * Android again, with a stack trace pointing at a file that looks innocent.
 *
 * The unit tests in push-runtime.test.ts cannot catch that: they test the
 * runtime module, not the rest of the tree. So this test reads the source.
 *
 * `src/push/runtime.ts` is the single allowed reference, and it uses a lazy
 * `require()` inside a function rather than a top-level import — which is the
 * whole point, and is why the check looks for `import`, not for the package
 * name on its own.
 */

import fs from 'fs';
import path from 'path';

const ROOT = path.resolve(__dirname, '..', '..');
const SCAN_DIRS = ['app', 'src'];
const EXTENSIONS = new Set(['.ts', '.tsx', '.js', '.jsx']);

/** The one file allowed to reach the module, and only through a lazy require. */
const ALLOWED = path.join('src', 'push', 'runtime.ts');

/** `import ... from 'expo-notifications'` and `import 'expo-notifications'`. */
const STATIC_IMPORT = /(^|\n)\s*import\s[^;]*?['"]expo-notifications['"]/;

function walk(dir: string, out: string[] = []): string[] {
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    if (entry.name === 'node_modules' || entry.name.startsWith('.')) continue;
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) walk(full, out);
    else if (EXTENSIONS.has(path.extname(entry.name))) out.push(full);
  }
  return out;
}

describe('expo-notifications stays out of the launch graph', () => {
  const files = SCAN_DIRS
    .map((d) => path.join(ROOT, d))
    .filter((d) => fs.existsSync(d))
    .flatMap((d) => walk(d));

  it('finds application source to scan', () => {
    // A guard that silently scanned nothing would pass forever.
    expect(files.length).toBeGreaterThan(30);
  });

  it('is never imported statically anywhere in app/ or src/', () => {
    const offenders = files.filter((file) => {
      const rel = path.relative(ROOT, file);
      if (rel === ALLOWED) return false;
      if (rel.includes('__tests__')) return false;   // tests mock it deliberately
      return STATIC_IMPORT.test(fs.readFileSync(file, 'utf8'));
    }).map((f) => path.relative(ROOT, f));

    expect(offenders).toEqual([]);
  });

  it('is reached only through a lazy require in the runtime module', () => {
    const source = fs.readFileSync(path.join(ROOT, ALLOWED), 'utf8');
    expect(source).toMatch(/require\('expo-notifications'\)/);
    expect(STATIC_IMPORT.test(source)).toBe(false);
  });
});
