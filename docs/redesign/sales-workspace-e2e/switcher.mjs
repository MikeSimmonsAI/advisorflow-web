// #300 — Sales header switchers removed; one "Other Workspaces" entry.
// LOCAL app, synthetic data (run_local.py + fixture_other_workspaces.py).
// Any request to a non-local host is aborted. Usage: node switcher.mjs OUT [before]
import pp from 'puppeteer-core'
import fs from 'node:fs'
const OUT = process.argv[2]; const BEFORE = process.argv[3] === 'before'
const APP = process.env.SW_APP || 'http://localhost:5173'  // must be an origin the backend allows (README)
fs.mkdirSync(OUT, { recursive: true })
const steps = []
const ok = (n, c, x = '') => { steps.push(`${c ? 'PASS' : 'FAIL'}  ${n}${x ? ' — ' + x : ''}`); if (!c) process.exitCode = 1 }
const sleep = ms => new Promise(r => setTimeout(r, ms))
process.on('exit', () => console.log(steps.join('\n')))
// Browser: SW_CHROME=<path to a headless-shell build> (see README), otherwise @sparticuz/chromium.
async function launchOpts() {
  if (process.env.SW_CHROME) return { executablePath: process.env.SW_CHROME, args: ['--no-sandbox', '--disable-dev-shm-usage'], headless: true }
  const chromium = (await import('@sparticuz/chromium')).default
  return { executablePath: await chromium.executablePath(), args: chromium.args, headless: true }
}
const browser = await pp.launch(await launchOpts())
const blocked = new Set(); const errors = []
let failContexts = false
const page = await browser.newPage()
await page.setRequestInterception(true)
page.on('request', r => {
  const u = r.url(); const host = u.startsWith('http') ? new URL(u).hostname : ''
  if (failContexts && u.includes('/auth/my-contexts')) return r.respond({ status: 500, contentType: 'application/json', body: '{"detail":"boom"}' })
  if (!u.startsWith('http') || ['127.0.0.1', 'localhost'].includes(host)) r.continue()
  else { blocked.add(host); r.abort() }
})
page.on('console', m => { if (m.type() === 'error' && !/Failed to load resource|net::ERR|500/.test(m.text())) errors.push(m.text()) })
page.on('pageerror', e => errors.push('PAGEERROR ' + e.message))
const vp = (w, h) => page.setViewport({ width: w, height: h, hasTouch: w < 821, isMobile: w < 821 })
const text = () => page.evaluate(() => document.body.innerText)
const shot = (n, full = false) => page.screenshot({ path: `${OUT}/${n}.png`, fullPage: full })
const visible = sel => page.$$eval(sel, els => els.filter(e => { const r = e.getBoundingClientRect(); const s = getComputedStyle(e); return r.width > 0 && r.height > 0 && s.display !== 'none' && s.visibility !== 'hidden' }).map(e => e.innerText.trim()))
const headerText = () => page.$eval('.sw-topbar', e => e.innerText)
const overflow = () => page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)
async function login(email) {
  await page.goto(APP + '/login', { waitUntil: 'networkidle2' })
  await page.evaluate(() => { localStorage.clear(); sessionStorage.clear() })
  await page.goto(APP + '/login', { waitUntil: 'networkidle2' })
  await page.type('input[type=email], input[name=email], input[autocomplete=username]', email)
  await page.type('input[type=password]', 'LocalTest!1')
  await Promise.all([page.waitForNavigation({ waitUntil: 'networkidle2' }).catch(() => {}), page.keyboard.press('Enter')])
  await sleep(900)
}
const goSales = async () => { await page.goto(APP + '/sales', { waitUntil: 'networkidle2' }); await sleep(900) }

if (BEFORE) {
  await vp(1440, 900); await login('multi@example.test'); await goSales()
  await shot('before-sales-header-desktop')
  const sw = await page.$('.ctx-switch-btn'); if (sw) { await sw.click(); await sleep(400) }
  await shot('before-switch-view-open-desktop')
  await vp(390, 844); await goSales(); await shot('before-sales-phone')
  console.log('before captured')
  await browser.close(); process.exit(0)
}

const VIEWS = [[1440, 900, 'desktop-1440'], [1280, 800, 'desktop-1280'], [820, 1180, 'tablet-820'], [390, 844, 'phone-390'], [375, 667, 'phone-375']]
const CASES = [
  ['rep', 'rep@example.test', 0],
  ['manager-1', 'manager@example.test', 1],
  ['multi', 'multi@example.test', 7],
  ['owner', 'owner@example.test', 0],
]
for (const [who, email, expect] of CASES) {
  await vp(1440, 900); await login(email)
  for (const [w, h, vname] of VIEWS) {
    await vp(w, h); await goSales()
    const head = await headerText()
    ok(`H. ${who} ${vname}: header has no Switch program / Switch View`, !/Switch program|Switch View|WORKSPACES|WORKSPACE\b/i.test(head) && !(await page.$('.ctx-switch-wrap')) && !(await page.$('.program-switch')), head.replace(/\n/g, ' | '))
    ok(`O. ${who} ${vname}: no sideways scroll`, (await overflow()) <= 0)
    const phone = w <= 820
    const side = await visible('.ss-otherws-side')
    if (!phone) {
      ok(`S. ${who} ${vname}: sidebar entry ${expect ? 'shown once' : 'absent'}`, expect ? side.length === 1 && side[0].includes('Other Workspaces') : side.length === 0, side.join(' / '))
      if (expect) ok(`S. ${who} ${vname}: says how many`, side[0].includes(expect === 1 ? '1 other business' : expect + ' other businesses'), side[0])
      if (vname === 'desktop-1440' || vname === 'tablet-820') await shot(`after-${who}-${vname}`)
    } else {
      ok(`S. ${who} ${vname}: no sidebar entry on a phone (it lives in More)`, side.length === 0)
      await page.tap('.ss-mmorebtn'); await sleep(300)
      const inMenu = await visible('#ss-mmore-panel .ss-otherws-menu')
      ok(`M. ${who} ${vname}: More ${expect ? 'has' : 'has no'} Other Workspaces (once)`, expect ? inMenu.length === 1 : inMenu.length === 0, inMenu.join(' / '))
      if (expect) {
        const sz = await page.$eval('#ss-mmore-panel .ss-otherws-menu', e => e.getBoundingClientRect().height)
        ok(`M. ${who} ${vname}: menu entry >=44px tall`, sz >= 44, String(sz))
      }
      ok(`O. ${who} ${vname}: no sideways scroll with More open`, (await overflow()) <= 0)
      if (vname === 'phone-390') await shot(`after-${who}-${vname}-more`)
      await page.keyboard.press('Escape'); await sleep(200)
    }
  }
  if (who === 'owner') {
    await vp(1440, 900); await goSales()
    ok('G. owner: God Mode return bar still on /sales', /back to command center/i.test(await text()))
  }
}

// ── manager with ONE other workspace: opens it, and Back Office returns ───
await vp(1440, 900); await login('manager@example.test'); await goSales()
const repBefore = await text()
await page.focus('.ss-otherws-side'); await Promise.all([page.waitForNavigation({ waitUntil: 'networkidle2' }).catch(() => {}), page.keyboard.press('Enter')]); await sleep(1200)
let path = await page.evaluate(() => location.pathname)
ok('N1. manager-1: keyboard Enter on the entry opens the launcher, which goes straight into the one workspace', path.startsWith('/workspace/'), path)
await shot('after-manager-1-entered-workspace')
// That workspace's own (unchanged) Admin menu has the established "Back Office" return.
await page.evaluate(() => [...document.querySelectorAll('button')].find(e => /Admin/.test(e.textContent))?.click()); await sleep(500)
await page.evaluate(() => { const el = [...document.querySelectorAll('[role=menuitem],button,a')].filter(e => /^Back Office/.test(e.textContent.trim())).pop(); el && el.click() }); await sleep(1500)
path = await page.evaluate(() => location.pathname)
ok('N2. manager-1: "Back Office" in that workspace returns to Sales', path === '/sales' && (await text()).includes('Morgan'), path)
const wsKey = await page.evaluate(() => Object.keys(localStorage).filter(k => /workspace/i.test(k) && localStorage.getItem(k)).join(','))
ok('N3. manager-1: workspace context cleared on return (no cross-tenant carry-over)', wsKey === '' || !/workspace_context|ws_ctx/i.test(wsKey), wsKey)

// ── multi-workspace manager: full launcher, pick one, come back ───────────
await login('multi@example.test'); await goSales()
await page.click('.ss-otherws-side'); await sleep(1500)
path = await page.evaluate(() => location.pathname)
const cards = await page.$$eval('.pa-grid [role=listitem]', l => l.map(x => x.innerText.split('\n').filter(Boolean).join(' ')))
ok('L1. multi: opens the existing /workspaces launcher', path === '/workspaces' && (await text()).includes('Choose your'), path)
const names = ['Light & Power', 'Roofing', 'Memorial', 'Insurance', 'Holdings', 'Realty']
ok('L2. multi: launcher lists the 6 authorized workspaces + Executive Suite + Back Office, nothing else', cards.length === 8 && names.every(n => cards.some(c => c.includes(n))) && cards.some(c => /Executive/.test(c)) && cards.some(c => /Back Office/.test(c)), cards.length + ': ' + cards.map(c => c.slice(0, 40)).join(' || '))
await shot('after-multi-launcher', true)
const pick = await page.evaluateHandle(() => { const li = [...document.querySelectorAll('.pa-grid [role=listitem]')].find(x => x.innerText.includes('Testco Realty') || x.innerText.includes('Realty')); return li && (li.querySelector('button,a') || li) })
await pick.asElement().click(); await sleep(1500)
path = await page.evaluate(() => location.pathname)
ok('L3. multi: choosing a card opens that workspace', path.startsWith('/workspace/'), path)
await page.goto(APP + '/workspaces', { waitUntil: 'networkidle2' }); await sleep(900)
const bo = await page.evaluateHandle(() => { const li = [...document.querySelectorAll('.pa-grid [role=listitem]')].find(x => /Back Office/.test(x.innerText)); return li && (li.querySelector('button,a') || li) })
await bo.asElement().click(); await sleep(1500)
path = await page.evaluate(() => location.pathname)
ok('L4. multi: Back Office card returns to Sales with the same person', path === '/sales' && (await text()).includes('Jordan'), path)
// phone path closes the menu on navigation
await vp(390, 844); await goSales()
await page.tap('.ss-mmorebtn'); await sleep(300)
await page.tap('#ss-mmore-panel .ss-otherws-menu'); await sleep(1500)
path = await page.evaluate(() => location.pathname)
ok('L5. multi phone: More → Other Workspaces opens /workspaces', path === '/workspaces', path)
await shot('after-multi-phone-launcher')
await page.goBack({ waitUntil: 'networkidle2' }); await sleep(800)
ok('L6. multi phone: back to Sales, More menu closed', (await page.evaluate(() => location.pathname)) === '/sales' && !(await page.$('#ss-mmore-panel')))

// ── /auth/my-contexts fails: say so, offer retry, never guess ─────────────
await vp(1440, 900)
await page.evaluate(() => { localStorage.clear(); sessionStorage.clear() })
await login('multi@example.test')
failContexts = true
await page.goto(APP + '/sales', { waitUntil: 'networkidle2' }); await sleep(1000)
const errLine = await visible('.ss-otherws-side.ss-otherws-err')
ok('E1. contexts error: honest message + Try again, no list shown', errLine.length === 1 && errLine[0].includes('Try again') && (await visible('a.ss-otherws-side')).length === 0, errLine.join(''))
await shot('after-contexts-error-desktop')
failContexts = false
await page.evaluate(() => [...document.querySelectorAll('.ss-otherws-err button')][0].click()); await sleep(1200)
const after = await visible('a.ss-otherws-side')
ok('E2. contexts error: Try again recovers the real entry', after.length === 1 && after[0].includes('7 other businesses'), after.join(''))

// ── accessibility on /sales (multi, desktop + phone More open) ───────────
const axeSrc = fs.readFileSync(new URL('./node_modules/axe-core/axe.min.js', import.meta.url), 'utf8')
const runAxe = async () => { await page.evaluate(axeSrc); return page.evaluate(async () => (await window.axe.run(document, { resultTypes: ['violations'] })).violations
  .filter(v => ['serious', 'critical'].includes(v.impact)).map(v => v.id + '(' + v.nodes.length + '): ' + v.nodes.map(n => n.target.join(' ') + ' ' + (n.any[0]?.message || '')).join(' ; '))) }
await goSales(); let ax = await runAxe(); ok('A1. axe /sales desktop: no serious/critical', ax.length === 0, ax.join(' '))
await vp(390, 844); await goSales(); await page.tap('.ss-mmorebtn'); await sleep(300); ax = await runAxe(); ok('A2. axe /sales phone with More open: no serious/critical', ax.length === 0, ax.join(' '))

console.log('blocked hosts:', [...blocked].join(', ') || 'none')
console.log('console errors:', errors.length ? errors.join('\n  ') : 'none')
await browser.close()
