/**
 * THE MASTER LEAD BROWSER'S DEFAULT MUST BE THE SAFE ONE.
 *
 * Runs under plain `node`, no bundler and no DOM, which is why
 * `src/pages/god/masterLeadQuery.js` imports nothing:
 *
 *     node tests/frontend/masterLeadQuery.test.mjs
 *
 * The thing being defended is small and easy to break: the browser must ask
 * for production records only unless an operator says otherwise, and the
 * Stage 1 column set must actually contain the columns Stage 1 promised.
 */
import {
  MASTER_COLUMNS, PAGE_SIZE, buildMasterParams, buildTenantParams,
  formatSeen, isProductionOnly,
} from '../../frontend/src/pages/god/masterLeadQuery.js'

let passed = 0
const failures = []

function check(name, fn) {
  try { fn(); passed += 1 }
  catch (e) { failures.push(name + '\n    ' + e.message) }
}

function eq(actual, expected, what) {
  const a = JSON.stringify(actual), b = JSON.stringify(expected)
  if (a !== b) throw new Error((what || 'value') + ': expected ' + b + ', got ' + a)
}

/* ── the default ──────────────────────────────────────────────────────────── */

check('production only is the default', () => {
  const p = buildMasterParams({})
  if ('include_synthetic' in p) {
    throw new Error('the default query asked for synthetic records')
  }
  eq(isProductionOnly(false), true, 'isProductionOnly(false)')
  eq(isProductionOnly(true), false, 'isProductionOnly(true)')
})

check('QA and test records are opt-in, and asked for explicitly', () => {
  const p = buildMasterParams({ includeSynthetic: true })
  eq(p.include_synthetic, true, 'include_synthetic')
})

check('an empty filter is omitted rather than sent as an empty string', () => {
  const p = buildMasterParams({ search: '', platformId: '', orgId: '' })
  eq(Object.keys(p).sort(), ['limit', 'skip'], 'keys')
})

check('every master filter reaches the query under the name the API uses', () => {
  const p = buildMasterParams({
    search: 'ada', platformId: 'plat-1', orgId: 'org-1', source: 'import',
    includeSynthetic: true, needsReview: true, skip: 100, limit: 25,
  })
  eq(p, {
    skip: 100, limit: 25, search: 'ada', platform_id: 'plat-1',
    org_id: 'org-1', source: 'import', include_synthetic: true,
    needs_review: true,
  }, 'master params')
})

/* ── the two views must not swap their parameters ─────────────────────────── */

check('the master view filters platform by ID, the tenant view by slug', () => {
  const master = buildMasterParams({ platformId: 'plat-1' })
  const tenant = buildTenantParams({ platformSlug: 'evosyspro' })
  eq(master.platform_id, 'plat-1', 'master platform_id')
  if ('platform_slug' in master) throw new Error('master sent a slug')
  eq(tenant.platform_slug, 'evosyspro', 'tenant platform_slug')
  if ('platform_id' in tenant) throw new Error('tenant sent an id')
})

check('the tenant view keeps status and never sends include_synthetic', () => {
  const p = buildTenantParams({ status: 'dnc' })
  eq(p.status, 'dnc', 'status')
  if ('include_synthetic' in p) throw new Error('tenant query sent include_synthetic')
})

/* ── the Stage 1 columns ──────────────────────────────────────────────────── */

check('the Stage 1 column set is the one that was asked for', () => {
  for (const required of ['Platform', 'Organization', 'Name', 'Email', 'Phone',
                          'Source', 'First Seen', 'Last Seen', 'Occurrences']) {
    if (!MASTER_COLUMNS.includes(required)) {
      throw new Error('missing column: ' + required)
    }
  }
  eq(MASTER_COLUMNS.length, 9, 'column count')
})

check('paging is consistent between the two views', () => {
  eq(buildMasterParams({}).limit, PAGE_SIZE, 'master limit')
  eq(buildTenantParams({}).limit, PAGE_SIZE, 'tenant limit')
})

/* ── dates ────────────────────────────────────────────────────────────────── */

check('a missing or unparseable timestamp renders as a dash, never as Invalid Date', () => {
  eq(formatSeen(null), '—', 'null')
  eq(formatSeen(''), '—', 'empty')
  eq(formatSeen('not-a-date'), '—', 'garbage')
  if (formatSeen('2026-09-14T16:38:45Z') === '—') {
    throw new Error('a real timestamp rendered as a dash')
  }
})

/* ── report ───────────────────────────────────────────────────────────────── */

if (failures.length) {
  console.error('\nFAILED (' + failures.length + '):')
  for (const f of failures) console.error('  ✗ ' + f)
  console.error('\n' + passed + ' passed, ' + failures.length + ' failed')
  process.exit(1)
}
console.log(passed + ' checks passed')
