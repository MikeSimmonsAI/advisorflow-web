/**
 * LANE-E [parity] #272: the browser half of BookaBoost Wholesale parity.
 *
 *   - server text naming the acquisition engine reaches a second Wholesale
 *     tenant in neutral wording; EvoSys text is untouched
 *   - one Back Office card per brand for someone on two brands' sales teams,
 *     each carrying its brand; one brand = the same single card as before
 *   - the Back Office screens name the brand being sold, "EvoSys Pro" until
 *     the server says otherwise
 *   - the field app installs under a neutral name when there is no engine name
 *
 *     node --test tests/frontend/bookaboostWholesaleParity.test.mjs
 */
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import test from 'node:test'
import assert from 'node:assert/strict'
import { neutralEngineText, neutralizeDeep } from '../../frontend/src/pages/wholesale/engineText.js'
import { programCards } from '../../frontend/src/pages/program/programAccessModel.js'

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..', '..', 'frontend')
const src = (p) => readFileSync(join(ROOT, p), 'utf8')

test('server text naming the engine becomes neutral wording', () => {
  assert.equal(neutralEngineText('EvoSense is paused for this workspace.'), 'Acquisition is paused for this workspace.')
  assert.equal(neutralEngineText('Promoted from EvoSense discovery (1 Main St)'), 'Promoted from Acquisition discovery (1 Main St)')
  assert.equal(neutralEngineText('Tell EvoSense what to hunt'), 'Tell the engine what to hunt')
  assert.equal(neutralEngineText('Put EvoSys Field on your home screen'), 'Put the field app on your home screen')
  assert.equal(neutralEngineText('no engine name here'), 'no engine name here')
  assert.equal(neutralEngineText('source: evosense'), 'source: evosense')        // identifiers untouched
})

test('a whole JSON answer is neutralized, keys and non-strings left alone', () => {
  const body = { EvoSense: 1, note: 'EvoSense hands off', items: [{ why: 'Suppressed in EvoSense', n: 3 }], ok: true, x: null }
  assert.deepEqual(neutralizeDeep(body), { EvoSense: 1, note: 'Acquisition hands off',
                                          items: [{ why: 'Suppressed in Acquisition', n: 3 }], ok: true, x: null })
})

test('the client neutralizes only Wholesale answers, and only without an engine name', () => {
  const c = src('src/api/client.js')
  assert.match(c, /function _neutralEngineFor\(path\)/)
  assert.match(c, /\^\\\/\(wholesale\|evosense\)/)
  assert.match(c, /return !engineName\(getBranding\(\)\)/)
  // The screens' own wording and the client share one rule.
  assert.match(src('src/pages/wholesale/engine.js'), /return neutralEngineText\(text\)/)
})

const BASE = { has_back_office: true, back_office_path: '/sales', workspace_contexts: [], product_contexts: [] }

test('one Back Office card per brand, each carrying its brand', () => {
  const ctx = { ...BASE, back_office_destinations: [
    { brand_sales_org_id: 'bso-bb', platform_id: 'plt-bb', platform_name: 'BookaBoost', label: 'BookaBoost Sales', path: '/sales' },
    { brand_sales_org_id: 'bso-evo', platform_id: 'plt-evo', platform_name: 'EvoSys Pro', label: 'EvoSys Pro Sales', path: '/sales' },
  ] }
  const cards = programCards(ctx).visible.filter((c) => c.type === 'back_office')
  assert.equal(cards.length, 2)
  assert.deepEqual(cards.map((c) => c.orgLabel).sort(), ['BookaBoost', 'EvoSys Pro'])
  assert.deepEqual(cards.map((c) => c.platform_id).sort(), ['plt-bb', 'plt-evo'])
  assert.ok(cards.every((c) => c.display === 'Back Office' && c.path === '/sales'))
  assert.equal(new Set(cards.map((c) => c.organization_id)).size, 2)
})

test('one brand: the same single Back Office card as before, no brand carried', () => {
  for (const ctx of [BASE, { ...BASE, back_office_destinations: [
    { brand_sales_org_id: 'bso-evo', platform_id: 'plt-evo', platform_name: 'EvoSys Pro', path: '/sales' }] }]) {
    const cards = programCards(ctx).visible.filter((c) => c.type === 'back_office')
    assert.equal(cards.length, 1)
    assert.equal(cards[0].organization_id, 'back-office')
    assert.equal(cards[0].orgLabel, null)
    assert.equal(cards[0].platform_id, undefined)
    assert.equal(cards[0].path, '/sales')
  }
})

test('choosing a brand Back Office sends that brand with /sales requests', () => {
  const app = src('src/App.jsx')
  assert.match(app, /if \(item\.platform_id\) setBrandContext\(item\.platform_id, item\.platform_name \|\| ''\)/)
})

test('Back Office screens name the brand being sold; EvoSys Pro until told otherwise', () => {
  const sb = src('src/pages/sales/salesBrand.js')
  assert.match(sb, /const DEFAULT = \{ name: 'EvoSys Pro', short: 'EvoSys' \}/)
  assert.match(src('src/pages/sales/SalesShell.jsx'), /setSalesBrand\(me\)/)
  for (const f of ['TeamCalendar.jsx', 'BookAppointment.jsx', 'SimpleParts.jsx', 'TeamAvailability.jsx']) {
    const code = src('src/pages/sales/' + f).split('\n').filter((l) => !/^\s*(\/\/|\*|\/\*)/.test(l)).join('\n')
    assert.ok(!/EvoSys/.test(code.replace(/\/\*[\s\S]*?\*\//g, '').replace(/\{\/\*[\s\S]*?\*\/\}/g, '')), f + ' still hard-codes EvoSys')
    assert.match(code, /useSalesBrand\(\)/, f)
  }
})

test('the field app installs under a neutral name without an engine name', () => {
  const evo = JSON.parse(src('public/manifest-field.webmanifest'))
  const neutral = JSON.parse(src('public/manifest-field-neutral.webmanifest'))
  assert.equal(evo.short_name, 'EvoSys Field')
  assert.ok(!JSON.stringify(neutral).includes('EvoSys'))
  assert.match(src('src/pages/wholesale/appts/fieldApp.js'), /engine\(\) \? FIELD\.manifest : NEUTRAL_MANIFEST/)
})

test('Wholesale screens touched here carry no unconditional EvoSys text', () => {
  assert.match(src('src/components/Layout.jsx'), /WHOLESALE_ENGINE \? ' — EvoSense acquisition and wholesale deal operations' : ' — acquisition and wholesale deal operations'/)
  assert.match(src('src/pages/investor/InvestorPortal.jsx'), /engine\(\) \? 'Sign in to EvoSys to preview your Deal Room\.' : 'Sign in to preview your Deal Room\.'/)
})
