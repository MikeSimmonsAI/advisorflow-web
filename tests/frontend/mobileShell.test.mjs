/**
 * MOBILE / PWA SHELL — pure rules.
 *
 *     node tests/frontend/mobileShell.test.mjs
 *
 * Pins: brand skins chosen from the workspace's own data (no customer named),
 * refusal reasons always come from the server and an unreadable answer is a
 * refusal, Needs Attention ranking, the push status decision, and the service
 * worker's "app shell only, never API data" cache rule (read from the real
 * public/sw.js).
 */
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'
import vm from 'node:vm'
import { mobileSkin, brandVars, SKINS, luminance, needsAttention, refusalReasons, canCompose,
         groupByDay, workspaceChoices, parseTs, badgeCount, relTime, NOT_AVAILABLE, greetingName } from '../../frontend/src/mobile/mobileHelpers.js'
import { WHOLESALE_FEATURE as RULES_WHOLESALE } from '../../frontend/src/auth/workspaceRules.js'
import { WHOLESALE_FEATURE } from '../../frontend/src/mobile/mobileHelpers.js'
import { pushStatus, PUSH_STATUS, subscriptionBody, urlBase64ToUint8Array } from '../../frontend/src/mobile/push.js'

// Day boundaries ('today') are local-time by design; pin the zone so the
// fixtures below mean the same thing on every machine.
process.env.TZ = 'UTC'

let passed = 0
const failures = []
function check(name, fn) { try { fn(); passed += 1 } catch (e) { failures.push(name + '\n    ' + e.message) } }
function eq(a, b, msg) { if (JSON.stringify(a) !== JSON.stringify(b)) throw new Error((msg || '') + ' expected ' + JSON.stringify(b) + ' got ' + JSON.stringify(a)) }

const root = join(dirname(fileURLToPath(import.meta.url)), '..', '..')

// ── skins ──
check('energy industry wears the energy skin', () => eq(mobileSkin({ industry: 'Energy' }), SKINS.ENERGY))
check('black primary (own colours) -> luxe personality', () =>
  eq(mobileSkin({ industry: 'insurance', brand_color_primary: '#0a0a0a', brand_color_accent: '#c9a227' }), SKINS.LUXE))
check('wholesale industry -> wholesale skin', () => { eq(mobileSkin({ industry: 'wholesale_real_estate' }), SKINS.WHOLESALE); eq(mobileSkin({ industry: 'wholesale' }), SKINS.WHOLESALE) })
check('wholesale key matches workspaceRules', () => eq(WHOLESALE_FEATURE, RULES_WHOLESALE))
check('real_estate + wholesale module -> wholesale; real_estate alone -> not', () => {
  eq(mobileSkin({ industry: 'real_estate', enabled_features: ['leads', 'wholesale_real_estate'] }), SKINS.WHOLESALE)
  eq(mobileSkin({ industry: 'real_estate', enabled_features: ['leads'] }), SKINS.PLATFORM)
  eq(mobileSkin({ industry: 'real_estate', enabled_features: null }), SKINS.PLATFORM)
  eq(mobileSkin({ industry: 'insurance', enabled_features: ['wholesale_real_estate'], brand_color_primary: '#000' }), SKINS.LUXE)
})
check('wholesale module path -> wholesale skin', () => eq(mobileSkin({ industry: 'insurance' }, { pathname: '/m/wholesale/deals' }), SKINS.WHOLESALE))
check('vertical beats colours (energy with dark primary stays energy)', () =>
  eq(mobileSkin({ industry: 'energy', brand_color_primary: '#000000' }), SKINS.ENERGY))
check('nothing known -> platform', () => { eq(mobileSkin(null), SKINS.PLATFORM); eq(mobileSkin({ brand_color_primary: '#2fb6ff' }), SKINS.PLATFORM) })
check('three brands are three different skins', () => {
  const s = new Set([mobileSkin({ brand_color_primary: '#000' }), mobileSkin({ industry: 'energy' }), mobileSkin({ industry: 'real_estate', enabled_features: ['wholesale_real_estate'] })])
  eq(s.size, 3)
})
check('brandVars carries only the workspace own colours', () => {
  eq(brandVars({ brand_color_primary: '#000', brand_color_accent: '#C9A227' }), { '--m-brand': '#000000', '--m-accent': '#c9a227', '--m-on-brand': '#ffffff' })
  eq(brandVars({}), {})
  eq(brandVars({ brand_color_primary: 'not-a-colour' }), {})
  eq(brandVars({ brand_color_primary: '#ffffff' })['--m-on-brand'], '#0b1220')
})
check('luminance sane', () => { eq(luminance('#000000'), 0); eq(Math.round(luminance('#ffffff')), 1); eq(luminance('zz'), null) })

// ── refusal reasons ──
check('gate allowed -> no reasons', () => eq(refusalReasons({ allowed: true, reasons: [] }), []))
check('gate refused -> server reasons verbatim', () => {
  const r = refusalReasons({ allowed: false, reasons: [{ code: 'NO_CONSENT', label: 'No SMS consent on record.' }, { code: 'QUIET_HOURS', label: 'Outside contact hours.' }] })
  eq(r.map(x => x.code), ['NO_CONSENT', 'QUIET_HOURS'])
  eq(r[0].label, 'No SMS consent on record.')
})
check('409 error from send -> detail.reasons', () => {
  const e = new Error('No SMS consent on record.'); e.status = 409
  e.detail = { message: 'No SMS consent on record.', reasons: [{ code: 'DNC', label: 'Contact is on the do-not-contact list.' }] }
  eq(refusalReasons(e), [{ code: 'DNC', label: 'Contact is on the do-not-contact list.' }])
})
check('error with string detail -> one refusal', () => { const e = new Error('Blocked'); e.detail = 'Blocked'; eq(refusalReasons(e)[0].label, 'Blocked') })
check('unreadable gate is a refusal, never an allow', () => {
  eq(refusalReasons(null).length, 1); eq(refusalReasons({}).length, 1); eq(refusalReasons({ allowed: false }).length, 1)
})
check('canCompose only on explicit allowed and not observing', () => {
  eq(canCompose({ allowed: true }), true); eq(canCompose({ allowed: 'yes' }), false); eq(canCompose(null), false)
  eq(canCompose({ allowed: true }, { observing: true }), false)
})

// ── needs attention ──
check('ranking: hot, overdue, attention, today appt, due today', () => {
  const now = new Date('2026-10-01T15:00:00')
  const items = needsAttention({
    replies: [
      { id: 'r1', lead_id: 'L1', needs_attention: true, is_hot: false, received_at: '2026-10-01T10:00:00', contact_name: 'A', classification: 'question' },
      { id: 'r2', lead_id: 'L2', needs_attention: false, is_hot: true, received_at: '2026-10-01T12:00:00', contact_name: 'B' },
      { id: 'r3', lead_id: 'L3', needs_attention: false, is_hot: false, contact_name: 'C' },
    ],
    tasks: [
      { id: 't1', status: 'open', due_at: '2026-09-30T09:00:00', title: 'Overdue' },
      { id: 't2', status: 'open', due_at: '2026-10-01T20:00:00', title: 'Today' },
      { id: 't3', status: 'open', due_at: '2026-10-09T09:00:00', title: 'Later' },
      { id: 't4', status: 'done', due_at: '2026-09-30T09:00:00', title: 'Done' },
    ],
    appointments: [
      { id: 'a1', lead_id: 'L9', status: 'booked', booked_time: '2026-10-01T17:00:00', lead_name: 'Appt' },
      { id: 'a2', lead_id: 'L9', status: 'cancelled', booked_time: '2026-10-01T18:00:00' },
      { id: 'a3', lead_id: 'L9', status: 'booked', booked_time: '2026-10-01T09:00:00' },
    ],
  }, now)
  eq(items.map(i => i.id), ['reply:r2', 'task:t1', 'reply:r1', 'appt:a1', 'task:t2'])
  eq(items[0].href, '/m/conversations/L2')
})
check('missing name is Not yet available, never invented', () => {
  const [it] = needsAttention({ replies: [{ id: 'x', needs_attention: true }] })
  eq(it.title, NOT_AVAILABLE)
})

// ── misc presentation ──
check('groupByDay orders and labels', () => {
  const now = new Date('2026-10-01T08:00:00')
  const g = groupByDay([{ id: 2, booked_time: '2026-10-02T09:00:00' }, { id: 1, booked_time: '2026-10-01T09:00:00' }, { id: 3 }], now)
  eq(g.map(x => x.label), ['Today', 'Tomorrow']); eq(g[0].items[0].id, 1)
})
check('workspaceChoices marks active', () => {
  const c = workspaceChoices({ workspace_contexts: [{ organization_id: 'o1', organization_name: 'One', role: 'admin' }, { organization_id: 'o2', organization_name: 'Two' }] }, 'o2')
  eq(c.map(x => x.active), [false, true]); eq(workspaceChoices(null, 'x'), [])
})
check('badgeCount', () => { eq(badgeCount(0), ''); eq(badgeCount(undefined), ''); eq(badgeCount(5), '5'); eq(badgeCount(140), '99+') })
check('relTime', () => { const n = new Date('2026-10-01T12:00:00Z'); eq(relTime('2026-10-01T11:55:00Z', n), '5m'); eq(relTime(null, n), ''); eq(relTime('2026-10-01T15:00:00Z', n), 'in 3h') })

check('naive server timestamps are UTC; zoned ones respected', () => {
  eq(parseTs('2026-10-01T10:00:00'), Date.UTC(2026, 9, 1, 10, 0, 0))
  eq(parseTs('2026-10-01T10:00:00.123456'), Date.UTC(2026, 9, 1, 10, 0, 0, 123))
  eq(parseTs('2026-10-01T10:00:00Z'), Date.UTC(2026, 9, 1, 10, 0, 0))
  eq(parseTs('2026-10-01T10:00:00-05:00'), Date.UTC(2026, 9, 1, 15, 0, 0))
  if (!isNaN(parseTs(null)) || !isNaN(parseTs('garbage'))) throw new Error('bad input must be NaN')
})
check('overdue naive due_at counts as overdue regardless of local zone', () => {
  const now = new Date(Date.UTC(2026, 9, 1, 12, 0, 0))
  const [it] = needsAttention({ tasks: [{ id: 't', status: 'open', due_at: '2026-10-01T10:00:00', title: 'x' }] }, now)
  eq(it.badge, 'Overdue')
  eq(relTime('2026-10-01T10:00:00', now), '2h')
})

// ── push ──
check('push: unsupported / unconfigured / denied / available / subscribed', () => {
  eq(pushStatus({ hasServiceWorker: false, hasPushManager: true }), PUSH_STATUS.UNSUPPORTED)
  eq(pushStatus({ hasServiceWorker: true, hasPushManager: true, serverConfig: null }), PUSH_STATUS.UNCONFIGURED)
  eq(pushStatus({ hasServiceWorker: true, hasPushManager: true, serverConfig: { enabled: true } }), PUSH_STATUS.UNCONFIGURED)
  const cfg = { enabled: true, vapid_public_key: 'BAAA' }
  eq(pushStatus({ hasServiceWorker: true, hasPushManager: true, serverConfig: cfg, permission: 'denied' }), PUSH_STATUS.DENIED)
  eq(pushStatus({ hasServiceWorker: true, hasPushManager: true, serverConfig: cfg, permission: 'default' }), PUSH_STATUS.AVAILABLE)
  eq(pushStatus({ hasServiceWorker: true, hasPushManager: true, serverConfig: cfg, subscribed: true }), PUSH_STATUS.SUBSCRIBED)
})
check('push subscription body shape', () => {
  eq(subscriptionBody({ endpoint: 'https://p/1', keys: { p256dh: 'a', auth: 'b' } }, 'UA'),
     { endpoint: 'https://p/1', keys: { p256dh: 'a', auth: 'b' }, user_agent: 'UA', platform: 'web' })
  eq(subscriptionBody(null), null)
  eq(Array.from(urlBase64ToUint8Array('AQID')), [1, 2, 3])
})

// ── service worker cache rule (the real file) ──
const swSrc = readFileSync(join(root, 'frontend', 'public', 'sw.js'), 'utf8')
const sandbox = { self: {}, URL }
vm.createContext(sandbox)
vm.runInContext(swSrc, sandbox)
const { shouldCache } = sandbox.self.__mshell
const O = 'https://app.example.test'
check('sw caches shell + hashed assets', () => {
  eq(shouldCache(O + '/', O, 'GET', false), true)
  eq(shouldCache(O + '/index.html', O, 'GET', false), true)
  eq(shouldCache(O + '/assets/index-abc123.js', O, 'GET', false), true)
  eq(shouldCache(O + '/manifest.webmanifest', O, 'GET', false), true)
})
check('sw never caches API / data / authed / cross-origin / non-GET', () => {
  eq(shouldCache('https://api.example.test/communications/replies', O, 'GET', false), false)
  eq(shouldCache(O + '/communications/thread/L1', O, 'GET', false), false)
  eq(shouldCache(O + '/auth/my-contexts', O, 'GET', false), false)
  eq(shouldCache(O + '/notifications/', O, 'GET', false), false)
  eq(shouldCache(O + '/assets/x.js', O, 'GET', true), false)
  eq(shouldCache(O + '/assets/x.js', O, 'POST', false), false)
  eq(shouldCache(O + '/?token=abc', O, 'GET', false), false)
})
check('sw has no generic network-to-cache write for navigations', () => {
  // The navigation handler must only READ the precached shell.
  const nav = swSrc.slice(swSrc.indexOf('isNavigation(req)) {'), swSrc.indexOf('if (!shouldCache'))
  if (/c\.put|cache\.put/.test(nav)) throw new Error('navigation responses are being cached')
})

// ── manifest ──
check('manifest is valid, brand-neutral, starts at /m', () => {
  const m = JSON.parse(readFileSync(join(root, 'frontend', 'public', 'manifest.webmanifest'), 'utf8'))
  eq(m.start_url, '/m'); eq(m.display, 'standalone')
  if (!m.icons || !m.icons.length) throw new Error('no icons')
  if (/bookaboost|evosys|advisorflow|max life|atlantis/i.test(JSON.stringify(m))) throw new Error('manifest names a brand')
})

check('greeting skips DEMO/QA labels and never invents a name', () => {
  eq(greetingName('DEMO Owner Morgan Hale'), 'Morgan')
  eq(greetingName('Erin Admin (QA)'), 'Erin')
  eq(greetingName('Maya Thompson'), 'Maya')
  eq(greetingName('DEMO'), null); eq(greetingName(''), null); eq(greetingName(null), null)
})

if (failures.length) {
  console.error('FAILED ' + failures.length + ' / ' + (passed + failures.length))
  failures.forEach(f => console.error(' - ' + f))
  process.exit(1)
}
console.log('mobileShell: ' + passed + ' checks passed')
