/**
 * AUTHORITY, PROVED BY READING THE SOURCE.
 *
 * ═══════════════════════════════════════════════════════════════════════════
 * WHAT THIS CAN AND CANNOT PROVE
 * ═══════════════════════════════════════════════════════════════════════════
 *
 * The REAL control is server-side: `require_god`, `require_sales_manager`,
 * `require_brand_executive`, `portfolio_authority()`, workspace membership. A
 * screen that asked for another authority's data would be refused by the API,
 * and nothing in this file changes that.
 *
 * What this file catches is the OTHER failure — the one that does not throw. A
 * workspace advisor's screen importing `owner` from endpoints.ts does not leak
 * data (the server refuses), but it does produce a tile reading "Could not
 * load" on a screen where that tile never belonged, and it means somebody
 * believed the advisor experience should show platform figures. That belief is
 * the bug worth catching early, and it is visible in the imports.
 *
 * So: each route group may reach only the endpoint namespaces its authority
 * owns. This is a design guard, not a security boundary, and it says so.
 */

import fs from 'fs';
import path from 'path';

import { HIDDEN_ROUTES, TABS, type ExperienceKind } from '../experience/resolve';

const ROOT = path.resolve(__dirname, '..', '..');
const APP = path.join(ROOT, 'app');

/** Which endpoint namespaces each experience's screens may import. */
const ALLOWED: Record<string, string[]> = {
  '(owner)': ['owner', 'auth', 'devices'],
  '(exec)': ['executive', 'auth', 'devices'],
  '(manager)': ['manager', 'sales', 'scheduling', 'proposals', 'compensation', 'auth', 'devices'],
  '(sales)': ['sales', 'scheduling', 'proposals', 'compensation', 'manager', 'auth', 'devices'],
  '(advisor)': ['advisor', 'auth', 'devices'],
};

/** Namespaces that must never appear in a group, whatever else it imports. */
const FORBIDDEN: Record<string, string[]> = {
  '(advisor)': ['owner', 'executive', 'manager', 'sales'],
  '(sales)': ['owner', 'executive', 'advisor'],
  '(manager)': ['owner', 'executive', 'advisor'],
  '(exec)': ['owner', 'advisor'],
  '(owner)': ['advisor'],
};

const IMPORT_FROM_ENDPOINTS =
  /import\s*\{([^}]+)\}\s*from\s*['"][^'"]*api\/endpoints['"]/;

function filesIn(group: string): string[] {
  const dir = path.join(APP, group);
  if (!fs.existsSync(dir)) return [];
  return fs.readdirSync(dir)
    .filter((f) => f.endsWith('.tsx') && !f.startsWith('_'))
    .map((f) => path.join(dir, f));
}

function importedNamespaces(file: string): string[] {
  const src = fs.readFileSync(file, 'utf8');
  const m = IMPORT_FROM_ENDPOINTS.exec(src);
  if (!m) return [];
  return m[1].split(',').map((s) => s.trim().split(/\s+as\s+/)[0].trim()).filter(Boolean);
}

describe('each experience reaches only its own authority', () => {
  for (const group of Object.keys(ALLOWED)) {
    it(`${group} imports nothing outside its authority`, () => {
      const offenders: string[] = [];
      for (const file of filesIn(group)) {
        for (const ns of importedNamespaces(file)) {
          if (!ALLOWED[group].includes(ns)) {
            offenders.push(`${path.basename(file)} imports "${ns}"`);
          }
        }
      }
      expect(offenders).toEqual([]);
    });

    it(`${group} never imports a forbidden namespace`, () => {
      const banned = FORBIDDEN[group] ?? [];
      const offenders: string[] = [];
      for (const file of filesIn(group)) {
        for (const ns of importedNamespaces(file)) {
          if (banned.includes(ns)) offenders.push(`${path.basename(file)} imports "${ns}"`);
        }
      }
      expect(offenders).toEqual([]);
    });
  }

  it('no screen reaches a /god path except the owner experience', () => {
    const offenders: string[] = [];
    for (const group of Object.keys(ALLOWED)) {
      if (group === '(owner)') continue;
      for (const file of filesIn(group)) {
        if (/['"]\/god\//.test(fs.readFileSync(file, 'utf8'))) {
          offenders.push(`${group}/${path.basename(file)}`);
        }
      }
    }
    expect(offenders).toEqual([]);
  });
});

describe('navigation points at screens that exist', () => {
  const GROUP: Record<ExperienceKind, string> = {
    owner: '(owner)', executive: '(exec)', manager: '(manager)',
    sales: '(sales)', advisor: '(advisor)',
  };

  for (const kind of Object.keys(GROUP) as ExperienceKind[]) {
    it(`${kind} tabs all resolve to a file`, () => {
      const missing = TABS[kind]
        .map((t) => t.name)
        .filter((name) => !fs.existsSync(path.join(APP, GROUP[kind], `${name}.tsx`)));
      expect(missing).toEqual([]);
    });

    it(`${kind} hidden routes all resolve to a file`, () => {
      const missing = HIDDEN_ROUTES[kind]
        .filter((name) => !fs.existsSync(path.join(APP, GROUP[kind], `${name}.tsx`)));
      expect(missing).toEqual([]);
    });

    it(`${kind} declares every screen in its folder`, () => {
      // A file nobody declared becomes an unlabelled tab in Expo Router. This
      // is what stops "issues" quietly reappearing in the owner's bar.
      const declared = new Set([
        ...TABS[kind].map((t) => t.name),
        ...HIDDEN_ROUTES[kind],
      ]);
      const undeclared = fs.readdirSync(path.join(APP, GROUP[kind]))
        .filter((f) => f.endsWith('.tsx') && !f.startsWith('_'))
        .map((f) => f.replace(/\.tsx$/, ''))
        .filter((name) => !declared.has(name));
      expect(undeclared).toEqual([]);
    });

    it(`${kind} has no more than five tabs`, () => {
      expect(TABS[kind].length).toBeLessThanOrEqual(5);
    });
  }

  it('the owner no longer spends a permanent tab on Issues', () => {
    expect(TABS.owner.map((t) => t.name)).not.toContain('issues');
    expect(TABS.owner.map((t) => t.name)).toContain('activity');
    // Still reachable, just not a tab.
    expect(HIDDEN_ROUTES.owner).toContain('issues');
  });

  it('every experience ends with More', () => {
    for (const kind of Object.keys(GROUP) as ExperienceKind[]) {
      expect(TABS[kind][TABS[kind].length - 1].name).toBe('more');
    }
  });
});
