// OLP Land Partners and Farm & Ranch workspaces (owner request, Oct 29): guard rails.
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { olpWorkspaceCards, olpWorkspaceView, OLP_WORKSPACES } from '../src/brand/olpNav.js'

const R = (p) => readFileSync(new URL(p, import.meta.url)).toString()
const layout = R('../src/components/Layout.jsx')
const app = R('../src/App.jsx')
const access = R('../src/pages/program/BookaBoostProgramAccess.jsx')

const wholesale = { type: 'product', key: 'wholesale', program: 'wholesale', organization_id: 'org-1', display: 'Wholesale', path: '/wholesale/evosense' }

test('the two cards exist only beside a Wholesale card the server gave this login', () => {
  assert.deepEqual(olpWorkspaceCards([]), [])
  assert.deepEqual(olpWorkspaceCards([{ type: 'executive', program: 'executive' }, { type: 'back_office' }]), [])
  const out = olpWorkspaceCards([wholesale, { type: 'executive' }])
  assert.deepEqual(out.map((c) => c.display), ['Land Partners', 'Farm & Ranch'])
  for (const c of out) {
    assert.equal(c.type, 'product'); assert.equal(c.key, 'wholesale')       // same product, same server check
    assert.equal(c.organization_id, 'org-1')                                 // same workspace, nothing new
  }
})

test('the view parameter only ever opens one of the two fixed screens', () => {
  assert.equal(olpWorkspaceView('land-partners'), '/wholesale/land-partners')
  assert.equal(olpWorkspaceView('farm-ranch'), '/wholesale/farm-ranch')
  for (const bad of [null, '', 'god', '/god', '../god', 'https://evil.example', 'land-partners/../../god', 'LAND-PARTNERS']) {
    assert.equal(olpWorkspaceView(bad), null, String(bad))
  }
  assert.equal(OLP_WORKSPACES.length, 2)
})

test('the workspace hand-off uses the view only for the server-proven Wholesale product', () => {
  assert.match(app, /requestedProduct === 'wholesale' && path \? olpWorkspaceView\(/)
  assert.match(app, /destination=\{view \|\| path\}/)
  assert.match(app, /\+ \(item\.view \? '&view=' \+ encodeURIComponent\(item\.view\) : ''\)/)
})

test('menu items carry the same feature gate as the radar and show only in OLP mode', () => {
  const radar = layout.match(/\{ to: '\/wholesale\/evosense\/overlooked'[^}]*\}/)[0]
  for (const to of ['/wholesale/land-partners', '/wholesale/farm-ranch']) {
    const item = layout.match(new RegExp(`\\{ to: '${to.replace(/\//g, '\\/')}'[^}]*\\}`))[0]
    assert.match(item, /featureKey: 'wholesale_real_estate'/)
    assert.match(item, /bookaboostOnly: true/)
    assert.match(item, /olpOnly: true/)
    assert.match(radar, /featureKey: 'wholesale_real_estate'/)
  }
  assert.match(layout, /if \(item\.olpOnly && !olpBrand\) return false/)
  assert.match(app, /path="\/wholesale\/land-partners" element=\{<ProtectedRoute feature="wholesale_real_estate">/)
  assert.match(app, /path="\/wholesale\/farm-ranch" element=\{<ProtectedRoute feature="wholesale_real_estate">/)
})

test('the cards are added only in OLP mode', () => {
  assert.match(access, /const olpExtras = IS_OLP \? olpWorkspaceCards\(visible\) : \[\]/)
})

test('both screens read only the existing tenant-scoped endpoints and send nothing', () => {
  const lp = R('../src/pages/wholesale/olp/LandPartners.jsx')
  const fr = R('../src/pages/wholesale/olp/FarmRanch.jsx')
  const calls = [...lp.matchAll(/api\.(\w+)\(/g)].map((m) => m[1])
  assert.deepEqual([...new Set(calls)], ['get'])
  // the tools write ONLY to the parcel's existing property-details record
  const tools = R('../src/pages/wholesale/olp/landTools.jsx')
  assert.match(tools, /const RADAR = '\/wholesale\/evosense\/opportunity-radar\?'/)
  assert.match(tools, /const details = \(id\) => `\/wholesale\/evosense\/properties\/\$\{encodeURIComponent\(id\)\}\/type-details`/)
  assert.match(tools, /const facts = \(id\) => `\/wholesale\/evosense\/properties\/\$\{encodeURIComponent\(id\)\}\/type-facts`/)
  const targets = [...tools.matchAll(/api\.(?:get|post)\(([^,)]+)/g)].map((m) => m[1].trim().split(/[ +(]/)[0])
  assert.deepEqual([...new Set(targets)].sort(), ['RADAR', 'details', 'facts'])
  const posts = [...tools.matchAll(/api\.post\(([^,]+),/g)].map((m) => m[1].trim())
  assert.deepEqual(posts, ['facts(id)'])
  assert.match(lp, /'\/wholesale\/evosense\/opportunity-radar\?'/)
  assert.match(lp, /'\/wholesale\/buyer-intel\/buyers\?'/)
  assert.match(fr, /market: 'farm'/)
  assert.ok(!/api\./.test(fr))
})
