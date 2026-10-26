// Program Access expansion - six accounts, LOCAL app + synthetic fixture only.
// Any request to a non-local host is aborted. Usage: node program_access.mjs OUT
import chromium from '@sparticuz/chromium'
import pp from 'puppeteer-core'
import fs from 'node:fs'
const OUT = process.argv[2]
const APP = process.env.SW_APP || 'http://127.0.0.1:4173'
const API = 'http://127.0.0.1:8000'
fs.mkdirSync(OUT, { recursive: true })
const steps = []
const ok = (n, c, x = '') => { steps.push(`${c ? 'PASS' : 'FAIL'}  ${n}${x ? ' — ' + x : ''}`); if (!c) process.exitCode = 1 }
const sleep = ms => new Promise(r => setTimeout(r, ms))
process.on('exit', () => console.log(steps.join('\n')))
const browser = await pp.launch({ executablePath: await chromium.executablePath(), args: chromium.args, headless: true })
const blocked = new Set(); const errors = []
const guarded = new WeakSet()
function guard(page) {
  if (guarded.has(page)) return
  guarded.add(page)
  page.setRequestInterception(true)
  page.on('request', r => { const u = r.url(); const h = u.startsWith('http') ? new URL(u).hostname : ''
    if (!u.startsWith('http') || ['127.0.0.1', 'localhost'].includes(h)) r.continue(); else { blocked.add(h); r.abort() } })
  page.on('pageerror', e => errors.push('PAGEERROR ' + e.message))
  page.on('console', m => { if (m.type() === 'error' && !/Failed to load resource|net::ERR|40[134]/.test(m.text())) errors.push(m.text()) })
}
browser.on('targetcreated', async t => { if (t.type() === 'page') { const p = await t.page(); if (p) guard(p) } })
const page = await browser.newPage(); guard(page)
const vp = (w, h) => page.setViewport({ width: w, height: h, isMobile: w < 821, hasTouch: w < 821 })
const text = () => page.evaluate(() => document.body.innerText)
const path = () => page.evaluate(() => location.pathname)
const shot = (n, full = true) => page.screenshot({ path: `${OUT}/${n}.png`, fullPage: full })
const overflow = () => page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)
async function token(email, tries = 2) {
  try { return await tokenOnce(email) } catch (e) { if (tries > 1) { await sleep(500); return token(email, tries - 1) } throw e }
}
async function tokenOnce(email) {
  const r = await fetch(API + '/auth/login', { method: 'POST', headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    body: 'username=' + encodeURIComponent(email) + '&password=' + encodeURIComponent('LocalTest!1') })
  return (await r.json()).access_token
}
const apiGet = async (tok, p, ws) => (await fetch(API + p, { headers: { Authorization: 'Bearer ' + tok, ...(ws ? { 'X-Workspace-Id': ws } : {}) } })).status
async function login(email) {
  await page.goto(APP + '/login', { waitUntil: 'networkidle2' })
  await page.evaluate(() => { localStorage.clear(); sessionStorage.clear() })
  await page.goto(APP + '/login', { waitUntil: 'networkidle2' })
  await page.type('input[type=email], input[name=email], input[autocomplete=username]', email)
  await page.type('input[type=password]', 'LocalTest!1')
  await Promise.all([page.waitForNavigation({ waitUntil: 'networkidle2' }).catch(() => {}), page.keyboard.press('Enter')])
  await sleep(1500)
}
const cardNames = sel => page.$$eval(sel + ' [role=listitem] .ev-wcard-name', e => e.map(x => x.textContent.trim()))
const cardKinds = sel => page.$$eval(sel + ' [role=listitem] .ev-wcard-kind', e => e.map(x => x.textContent.trim()))
const WS = '[aria-label="Your programs"]'; const PV = '[aria-label="Previews"]'
async function openCard(sel, name) {
  const ok2 = await page.evaluate((sel, name) => { const li = [...document.querySelectorAll(sel + ' [role=listitem]')].find(x => x.querySelector('.ev-wcard-name')?.textContent.trim() === name)
    const b = li && li.querySelector('button.ev-wcard-open'); if (b) { b.click(); return true } return false }, sel, name)
  await sleep(1800); return ok2
}
// Isolation: every company has one marker lead named after its slug. A login
// may only ever receive markers of companies it belongs to - whatever header it sends.
async function markers(tok, ws) {
  const r = await fetch(API + '/leads/?limit=500', { headers: { Authorization: 'Bearer ' + tok, ...(ws ? { 'X-Workspace-Id': ws } : {}) } })
  const body = r.status === 200 ? await r.text() : ''
  return { status: r.status, slugs: [...new Set([...body.matchAll(/marker-([a-z0-9-]+)@example\.test/g)].map(m => m[1]))].sort() }
}
const BAD = /not found|access denied|forbidden|something went wrong|unavailable|no access/i

// ── A: wholesale@ ─────────────────────────────────────────────────────────
const tokA = await token('wholesale@evosyspro.live')
const ctxA = await (await fetch(API + '/auth/my-contexts', { headers: { Authorization: 'Bearer ' + tokA } })).json()
const org = n => ctxA.workspace_contexts.find(w => w.organization_name === n)?.organization_id
await vp(1440, 900); await login('wholesale@evosyspro.live')
ok('A1. wholesale@ lands on Program Access', (await path()) === '/workspaces' && (await text()).includes('Choose your'))
const namesA = await cardNames(WS)
const expectA = ['SCI', 'Atlantis Light & Power', 'Roofing', 'Wholesale', 'Restland Cemetery and Funeral Home', 'Insurance', 'Back Office']
ok('A2. wholesale@ sees exactly its 7 authorized cards, no duplicates', JSON.stringify([...namesA].sort()) === JSON.stringify([...expectA].sort()) && new Set(namesA).size === namesA.length, namesA.join(' | '))
const pvA = await cardNames(PV); const pvKinds = await cardKinds(PV)
const kindsA = await cardKinds(WS)
ok('A3. 4 previews, separate and labelled (CCB, Countryside, VP Wraps, Building Equity)', JSON.stringify(pvA) === JSON.stringify(['Building Equity Investments LLC', 'Commercial Cleaning Blueprint', 'Countryside Land Partners', 'VP Wraps Vehicle Protection']) && pvKinds.every(k => k.startsWith('PREVIEW')), pvA.join(' | '))
ok('A4. Atlantis (onboarding) has a workspace card and no preview', namesA.includes('Atlantis Light & Power') && !pvA.includes('Atlantis Light & Power'))
ok('A5. Commercial Cleaning Blueprint is a preview only (no workspace seat yet)', !namesA.includes('Commercial Cleaning Blueprint') && pvA.includes('Commercial Cleaning Blueprint'))
await shot('program-access-desktop-wholesale')
ok('A6. desktop: no sideways scroll', (await overflow()) <= 0)
const dest = { 'SCI': /^\/(program|workspace)\//, 'Atlantis Light & Power': /^\/workspace\//, 'Roofing': /^\/(program\/roofing|workspace\/)/, 'Wholesale': /^\/wholesale\//,
  'Restland Cemetery and Funeral Home': /^\/workspace\//, 'Insurance': /^\/(workspace|agency)\b/, 'Back Office': /^\/sales/ }
for (const n of expectA) {
  await page.goto(APP + '/workspaces', { waitUntil: 'networkidle2' }); await sleep(900)
  const clicked = await openCard(WS, n)
  const p1 = await path(); const t1 = await text()
  await page.reload({ waitUntil: 'networkidle2' }); await sleep(1500)
  const p2 = await path(); const t2 = await text()
  ok(`A7. "${n}" opens its destination and survives refresh`, clicked && dest[n].test(p1) && dest[n].test(p2) && !BAD.test(t1.slice(0, 600)) && !BAD.test(t2.slice(0, 600)), p1 + ' -> ' + p2)
  if (n === 'Back Office') ok('A7b. Back Office opens the real Sales Workspace', (await text()).includes('My Day') || (await text()).includes('Sales'))
}
await page.goto(APP + '/workspaces', { waitUntil: 'networkidle2' }); await sleep(900)
ok('A8. navigating back to Program Access works', (await path()) === '/workspaces' && (await cardNames(WS)).length === 7)
// previews are REAL LINKS (same tab): a real click opens that prospect's demo,
// Back returns to Program Access. No pop-up involved, so nothing can block it.
for (const n of pvA) {
  await page.goto(APP + '/workspaces', { waitUntil: 'networkidle2' }); await sleep(900)
  const tag = await page.evaluate((n) => { const li = [...document.querySelectorAll('[aria-label="Previews"] [role=listitem]')].find(x => x.querySelector('.ev-wcard-name')?.textContent.trim() === n); const a = li && li.querySelector('.ev-wcard-open'); return a ? a.tagName + '|' + (a.getAttribute('href') || '') : '' }, n)
  const box = await page.evaluate((n) => { const li = [...document.querySelectorAll('[aria-label="Previews"] [role=listitem]')].find(x => x.querySelector('.ev-wcard-name')?.textContent.trim() === n); const a = li.querySelector('.ev-wcard-open'); a.scrollIntoView({ block: 'center' }); const r = a.getBoundingClientRect(); return [r.x + r.width / 2, r.y + r.height / 2] }, n)
  await Promise.all([page.waitForNavigation({ waitUntil: 'networkidle2' }).catch(() => {}), page.mouse.click(box[0], box[1])]); await sleep(1800)
  const u = page.url()
  let inner = ''
  for (const f of page.frames()) inner += ' ' + (await f.evaluate(() => document.body ? document.body.innerText : '').catch(() => ''))
  ok(`A9. preview "${n}" is a real link; a real click opens that prospect's demo`, tag.startsWith('A|') && /\/demo\//.test(u) && inner.includes(n) && !/not found/i.test(inner), tag.split('|')[0] + ' ' + u.replace(/demo\/.{12}.*/, 'demo/…'))
  if (n === 'Countryside Land Partners') await page.screenshot({ path: `${OUT}/preview-countryside-local.png` })
  await page.goBack({ waitUntil: 'networkidle2' }); await sleep(900)
  ok(`A9c. Back returns to Program Access from "${n}"`, (await path()) === '/workspaces')
}
if (process.env.PA_DB) {
  const { execSync } = await import('node:child_process')
  const views = execSync(`/home/claude/venv/bin/python -c "import sqlite3;print(sqlite3.connect('${process.env.PA_DB}').execute('select coalesce(sum(view_count),0) from demo_sites').fetchone()[0])"`).toString().trim()
  ok('A9b. opening previews from Program Access did not count as prospect views', views === '0', 'views=' + views)
}
// cross-tenant / direct access with wholesale@'s token
const ma = await markers(tokA, org('Atlantis Light & Power'))
ok('A10. wholesale@ in Atlantis sees Atlantis data only', ma.status === 200 && JSON.stringify(ma.slugs) === '["atlantis-light-power"]', JSON.stringify(ma))
const ccbId = (await (await fetch(API + '/auth/my-contexts', { headers: { Authorization: 'Bearer ' + await token('clean@example.test') } })).json()).workspace_contexts[0].organization_id
const xs = await markers(tokA, ccbId)
ok('A11. wholesale@ asking for Commercial Cleaning Blueprint gets none of its data (no seat)', !xs.slugs.includes('commercial-cleaning-blueprint'), JSON.stringify(xs))
await page.goto(APP + '/god', { waitUntil: 'networkidle2' }); await sleep(900)
const g = await apiGet(tokA, '/god/stats'); const g2 = await apiGet(tokA, '/god/platforms')
ok('A12. God Mode stays owner-only (wholesale@ refused by the God API)', [401, 403, 404].includes(g) && [401, 403, 404].includes(g2), g + '/' + g2)
// phone
await vp(390, 844); await page.goto(APP + '/workspaces', { waitUntil: 'networkidle2' }); await sleep(900)
ok('A13. phone: all cards and previews present, no sideways scroll', (await cardNames(WS)).length === 7 && (await cardNames(PV)).length === 4 && (await overflow()) <= 0, String(await overflow()))
await shot('program-access-phone-wholesale')
await vp(1440, 900); await page.goto(APP + '/workspaces', { waitUntil: 'networkidle2' }); await sleep(900)
const so = !!(await page.$('.pa-signout'))
await Promise.all([page.waitForNavigation({ waitUntil: 'networkidle2', timeout: 8000 }).catch(() => {}), page.click('.pa-signout')]); await sleep(800)
ok('A14. Sign out returns to login', so && /\/login/.test(await path()), await path())

// ── A (Sales side): one Other Workspaces entry, no Switch View (#300) ─────
await login('wholesale@evosyspro.live')
await page.goto(APP + '/sales', { waitUntil: 'networkidle2' }); await sleep(1500)
const side = await page.$$eval('.ss-otherws-side', e => e.map(x => x.innerText.replace(/\s+/g, ' ').trim()))
ok('A15. Sales: one "Other Workspaces" entry, no Switch View / Switch program', side.length === 1 && side[0].includes('Other Workspaces') && !(await page.$('.ctx-switch-wrap')) && !(await page.$('.program-switch')), side.join(' | '))
await page.click('.ss-otherws-side'); await sleep(1500)
ok('A16. Other Workspaces opens Program Access', (await path()) === '/workspaces')
await vp(390, 844); await page.goto(APP + '/sales', { waitUntil: 'networkidle2' }); await sleep(1200)
await page.tap('.ss-mmorebtn'); await sleep(400)
const mm = await page.$$eval('#ss-mmore-panel .ss-otherws-menu', e => e.length)
ok('A17. phone: Other Workspaces is in the More menu', mm === 1)
await shot('sales-phone-more-other-workspaces', false)
await vp(1440, 900)

// ── G: sales manager with no customer workspaces ────────────────────────
await login('salesmgr@example.test')
ok('G0. sales manager lands in Sales', (await path()) === '/sales', await path())
await page.goto(APP + '/workspaces', { waitUntil: 'networkidle2' }); await sleep(900)
const nG = await cardNames(WS)
ok('G0b. sales manager: Back Office only + the brand previews, no client workspaces', JSON.stringify(nG) === '["Back Office"]' && (await cardNames(PV)).length === 4, nG.join(' | '))
const xg = await markers(await token('salesmgr@example.test'), org('Restland Cemetery and Funeral Home'))
ok('G0c. sales manager asking for Restland gets none of its data', xg.slugs.length === 0, JSON.stringify(xg))

// ── B: normal salesperson ────────────────────────────────────────────────
await login('rep@example.test')
ok('B1. salesperson lands in Sales (no launcher detour)', (await path()) === '/sales', await path())
await page.goto(APP + '/workspaces', { waitUntil: 'networkidle2' }); await sleep(900)
const nB = await cardNames(WS); const pB = await page.$$(PV)
ok('B2. salesperson: only Back Office, no client cards, no previews', JSON.stringify(nB) === '["Back Office"]' && pB.length === 0, nB.join(' | '))
const tokB = await token('rep@example.test')
const xb = await markers(tokB, org('Atlantis Light & Power'))
ok('B3. salesperson asking for a client workspace gets none of its data', xb.slugs.length === 0, JSON.stringify(xb))

// ── C: multi-company manager ─────────────────────────────────────────────
await login('multi@example.test')
const nC = await cardNames(WS)
ok('C1. multi manager: its 3 workspaces + Back Office', JSON.stringify([...nC].sort()) === JSON.stringify(['Atlantis Light & Power', 'Back Office', 'Restland Cemetery and Funeral Home', 'SCI']), nC.join(' | '))
ok('C2. multi manager: brand previews (sales manager of that brand)', (await cardNames(PV)).length === 4)
ok('C3. multi manager: no Roofing / Wholesale / Insurance cards', !nC.some(n => ['Roofing', 'Wholesale', 'Insurance'].includes(n)))
await shot('program-access-desktop-multi-manager')
const xc = await markers(await token('multi@example.test'), org('EvoSys Pro Roofing'))
ok('C4. multi manager asking for Roofing gets none of its data', !xc.slugs.includes('evosys-pro-roofing'), JSON.stringify(xc))

// ── D: one client workspace (Commercial Cleaning Blueprint) ──────────────
await login('clean@example.test')
const pD = await path()
ok('D1. single-workspace user goes straight into Commercial Cleaning Blueprint', pD === '/workspace/' + ccbId, pD)
await sleep(800); const tD = await text()
ok('D2. the workspace loads under its own name', tD.includes('Commercial Cleaning Blueprint') && !BAD.test(tD.slice(0, 600)))
await shot('destination-commercial-cleaning-blueprint', false)
await page.reload({ waitUntil: 'networkidle2' }); await sleep(1200)
ok('D3. refresh keeps the same workspace', (await path()) === pD)
const tokD = await token('clean@example.test')
const md = await markers(tokD, null); const xd = await markers(tokD, org('Atlantis Light & Power'))
ok('D4. cleaning owner sees only its own data, even when it names another company', JSON.stringify(md.slugs) === '["commercial-cleaning-blueprint"]' && !xd.slugs.includes('atlantis-light-power'), JSON.stringify([md, xd]))
const sd = await apiGet(tokD, '/sales/me')
ok('D5. cleaning owner has no Sales Back Office', sd === 403 || sd === 404, String(sd))
const pvD = (await (await fetch(API + '/auth/my-contexts', { headers: { Authorization: 'Bearer ' + tokD } })).json()).preview_contexts
ok('D6. cleaning owner sees no sales previews', Array.isArray(pvD) && pvD.length === 0)

// ── E: executive ─────────────────────────────────────────────────────────
await login('exec@example.test')
const nE = await cardNames(WS)
ok('E1. executive: 2 workspaces + Executive Suite, no Back Office, no previews', JSON.stringify([...nE].sort()) === JSON.stringify(['Atlantis Light & Power', 'Executive Suite', 'Roofing']) && (await page.$$(PV)).length === 0, nE.join(' | '))
await shot('program-access-desktop-executive', false)
await openCard(WS, 'Executive Suite')
ok('E2. Executive Suite opens /executive', /^\/executive/.test(await path()) && !BAD.test((await text()).slice(0, 600)), await path())
const ee = await apiGet(await token('rep@example.test'), '/executive/command-center')
ok('E3. a salesperson is refused the executive API', [401, 403, 404].includes(ee), String(ee))

// ── F: GOD ADMIN ─────────────────────────────────────────────────────────
await login('owner@example.test')
ok('F1. owner lands in God Mode', /^\/god/.test(await path()), await path())
const tokF = await token('owner@example.test')
const ctxF = await (await fetch(API + '/auth/my-contexts', { headers: { Authorization: 'Bearer ' + tokF } })).json()
ok('F0. owner reaches the God API', (await apiGet(tokF, '/god/stats')) === 200)
ok('F2. owner has no preview or customer cards by default (God Mode has its own home)', ctxF.preview_contexts.length === 0 && ctxF.workspace_contexts.length === 0)

// ── After owner approval (LOCAL rehearsal of the prepared script) ─────────
if (process.env.PA_GRANT_CMD) {
  const { execSync } = await import('node:child_process')
  const out = execSync(process.env.PA_GRANT_CMD + ' --apply --approved-by "Local rehearsal" --confirm "GRANT WHOLESALE CCB WORKSPACE"').toString()
  ok('G1. approval script applies once (local database only)', /DONE: workspace_membership_granted/.test(out), out.trim().split('\n').pop())
  const again = execSync(process.env.PA_GRANT_CMD + ' --apply --approved-by "Local rehearsal" --confirm "GRANT WHOLESALE CCB WORKSPACE"').toString()
  ok('G2. re-running changes nothing', /Already in place/.test(again))
  await vp(1440, 900); await login('wholesale@evosyspro.live')
  const n2 = await cardNames(WS); const k2 = await cardKinds(WS); const p2 = await cardNames(PV)
  ok('G3. after approval: Commercial Cleaning Blueprint card (COMMERCIAL CLEANING), preview not duplicated', n2.includes('Commercial Cleaning Blueprint') && k2.includes('COMMERCIAL CLEANING') && !p2.includes('Commercial Cleaning Blueprint') && p2.length === 3, n2.join(' | ') + ' || ' + p2.join(' | '))
  const looks = await page.evaluate(() => ({ ccb: [...document.querySelectorAll('[aria-label="Your programs"] .ev-wcard')].find(c => c.querySelector('.ev-wcard-name').textContent.trim() === 'Commercial Cleaning Blueprint')?.dataset.variant,
    pv: [...document.querySelectorAll('[aria-label="Previews"] .ev-wcard')].map(c => c.dataset.variant) }))
  ok('G3b. Commercial Cleaning has its own look (not the grey general one); previews have the preview look', looks.ccb === 'cleaning' && looks.pv.every(v => v === 'preview'), JSON.stringify(looks))
  await page.evaluate(() => document.querySelector('[aria-label="Your programs"]').scrollIntoView({ block: 'start' })); await sleep(300)
  await shot('program-access-desktop-wholesale-after-ccb-approval')
  await openCard(WS, 'Commercial Cleaning Blueprint')
  const pg = await path()
  ok('G4. the card opens the Commercial Cleaning Blueprint workspace', pg === '/workspace/' + ccbId && (await text()).includes('Commercial Cleaning Blueprint'), pg)
  const mg = await markers(await token('wholesale@evosyspro.live'), ccbId)
  ok('G5. inside it wholesale@ sees that company\'s data only', JSON.stringify(mg.slugs) === '["commercial-cleaning-blueprint"]', JSON.stringify(mg))
  await shot('destination-commercial-cleaning-blueprint-as-wholesale', false)
  const rv = execSync(process.env.PA_GRANT_CMD + ' --revoke --approved-by "Local rehearsal" --confirm "GRANT WHOLESALE CCB WORKSPACE"').toString()
  const n3 = (await (await fetch(API + '/auth/my-contexts', { headers: { Authorization: 'Bearer ' + await token('wholesale@evosyspro.live') } })).json()).workspace_contexts.map(w => w.organization_name)
  ok('G6. rollback removes the seat again', /revoked/.test(rv) && !n3.includes('Commercial Cleaning Blueprint'))
}

console.log('blocked hosts:', [...blocked].join(', ') || 'none')
console.log('console errors:', errors.length ? errors.join('\n  ') : 'none')
await browser.close()
