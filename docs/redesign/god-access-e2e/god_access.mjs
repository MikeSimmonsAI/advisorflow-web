// God Mode: give an existing login access to one business (local, synthetic).
import chromium from '@sparticuz/chromium'
import pp from 'puppeteer-core'
import fs from 'node:fs'
const OUT = process.argv[2]; const DB = process.env.PA_DB
const APP = process.env.SW_APP || 'http://127.0.0.1:4173'; const API = 'http://127.0.0.1:8000'
fs.mkdirSync(OUT, { recursive: true })
const steps = []; const ok = (n, c, x = '') => { steps.push(`${c ? 'PASS' : 'FAIL'}  ${n}${x ? ' — ' + x : ''}`); if (!c) process.exitCode = 1 }
process.on('exit', () => console.log(steps.join('\n')))
const sleep = ms => new Promise(r => setTimeout(r, ms))
const { execSync } = await import('node:child_process')
const sql = q => execSync(`/home/claude/venv/bin/python -c "import sqlite3,json;print(json.dumps(sqlite3.connect('${DB}').execute(\\"${q}\\").fetchall()))"`).toString().trim()
const browser = await pp.launch({ executablePath: await chromium.executablePath(), args: chromium.args, headless: true })
const page = await browser.newPage(); await page.setViewport({ width: 1440, height: 900 })
const errors = []
page.on('pageerror', e => errors.push(e.message))
await page.setRequestInterception(true)
page.on('request', r => { const u = r.url(); const h = u.startsWith('http') ? new URL(u).hostname : ''; if (!u.startsWith('http') || ['127.0.0.1', 'localhost'].includes(h)) r.continue(); else r.abort() })
const text = () => page.evaluate(() => document.body.innerText)
const path = () => page.evaluate(() => location.pathname + location.search)
async function login(email) {
  await page.goto(APP + '/login', { waitUntil: 'networkidle2' })
  await page.evaluate(() => { localStorage.clear(); sessionStorage.clear() })
  await page.goto(APP + '/login', { waitUntil: 'networkidle2' })
  await page.type('input[type=email], input[name=email], input[autocomplete=username]', email)
  await page.type('input[type=password]', 'LocalTest!1')
  await Promise.all([page.waitForNavigation({ waitUntil: 'networkidle2' }).catch(() => {}), page.keyboard.press('Enter')]); await sleep(1200)
}
const clickText = (sel, t) => page.evaluate((sel, t) => { const el = [...document.querySelectorAll(sel)].find(e => e.textContent.trim() === t || e.textContent.includes(t)); if (el) { el.click(); return true } return false }, sel, t)
const ccb = JSON.parse(sql("select id from organizations where slug='commercial-cleaning-blueprint'"))[0][0]
const ws = JSON.parse(sql("select id from users where email='wholesale@evosyspro.live'"))[0][0]
const mems = () => JSON.parse(sql(`select role,is_active from memberships where user_id='${ws}' and scope_id='${ccb}'`))
const before = JSON.parse(sql(`select count(*) from memberships where user_id='${ws}'`))[0][0]

await login('owner@example.test')
await page.goto(APP + '/god/customers/' + ccb + '?tab=administration', { waitUntil: 'networkidle2' }); await sleep(1500)
ok('1. the business page (even on its Administration tab) shows a "Manage access" button', await page.evaluate(() => [...document.querySelectorAll('button')].some(b => b.textContent.trim() === 'Manage access')))
await page.screenshot({ path: OUT + '/1-business-page-manage-access.png' })
await clickText('button', 'Manage access'); await sleep(1500)
ok('2. it opens Manage Access with the business named', (await path()).startsWith('/god/access?org=' + ccb) && (await text()).includes('Give someone access to Commercial Cleaning Blueprint'), await path())
await page.type('input[placeholder="Search by name or email…"]', 'wholesale@'); await sleep(600)
const rows = await page.$$eval('table.gm-table tbody tr', r => r.map(x => x.innerText.replace(/\s+/g, ' ')))
ok('3. searching by email finds wholesale@ (no ids typed)', rows.length === 1 && rows[0].includes('wholesale@evosyspro.live') && rows[0].includes('GIVE ACCESS'), rows.join(' | '))
await page.screenshot({ path: OUT + '/2-find-person.png' })
await clickText('button', 'GIVE ACCESS'); await sleep(2500)
const t3 = await text()
ok('4. person page opens with the change already previewed (Workspace Admin in Commercial Cleaning Blueprint)', t3.includes('Ready: Workspace Admin in Commercial Cleaning Blueprint') && t3.includes('CONFIRM AND APPLY') && /\+ .*Commercial Cleaning Blueprint/.test(t3), (await path()))
ok('5. nothing written before confirm', mems().length === 0)
await page.screenshot({ path: OUT + '/3-ready-to-confirm.png' })
await page.setViewport({ width: 390, height: 844, isMobile: true, hasTouch: true }); await sleep(800)
await page.evaluate(() => [...document.querySelectorAll('button')].find(b => b.textContent.includes('CONFIRM AND APPLY'))?.scrollIntoView({ block: 'center' })); await sleep(400)
await page.screenshot({ path: OUT + '/3m-ready-to-confirm-phone.png' })
const over = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)
ok('6. phone: confirm screen reachable', (await text()).includes('CONFIRM AND APPLY'), 'overflow ' + over)
await page.setViewport({ width: 1440, height: 900 }); await sleep(800)
await clickText('button', 'CONFIRM AND APPLY'); await sleep(2500)
const m1 = mems()
ok('7. one active Workspace Admin membership written', JSON.stringify(m1) === '[["org_admin",1]]', JSON.stringify(m1))
const after = JSON.parse(sql(`select count(*) from memberships where user_id='${ws}'`))[0][0]
ok('8. only that one membership added (others untouched)', after === before + 1, before + ' -> ' + after)
const aud = JSON.parse(sql(`select action from audit_log_entries where target_id='${ws}' order by created_at desc limit 3`))
ok('9. the change is in the audit log', aud.length > 0, JSON.stringify(aud))
await page.screenshot({ path: OUT + '/4-applied.png' })
// duplicate prevention
await page.goto(APP + '/god/access/' + ws + '?org=' + ccb, { waitUntil: 'networkidle2' }); await sleep(2000)
ok('10. arriving again says it is already in place; no second row', (await text()).includes('already has Workspace Admin in Commercial Cleaning Blueprint') && mems().length === 1)
// the login now sees the business
const tok = (await (await fetch(API + '/auth/login', { method: 'POST', headers: { 'Content-Type': 'application/x-www-form-urlencoded' }, body: 'username=wholesale%40evosyspro.live&password=LocalTest!1' })).json()).access_token
const ctx = await (await fetch(API + '/auth/my-contexts', { headers: { Authorization: 'Bearer ' + tok } })).json()
ok('11. wholesale@ now has the Commercial Cleaning Blueprint workspace', ctx.workspace_contexts.some(w => w.organization_id === ccb))
// revocation through the same screen
await page.evaluate(() => { window.__r = [...document.querySelectorAll('tr')].find(r => r.innerText.includes('Commercial Cleaning Blueprint') && r.innerText.includes('REMOVE')); })
await page.evaluate(() => [...window.__r.querySelectorAll('button')].find(b => b.textContent.trim() === 'REMOVE').click()); await sleep(500)
await clickText('button', 'PREVIEW'); await sleep(1500)
await clickText('button', 'CONFIRM AND APPLY'); await sleep(2500)
ok('12. revoke through the same review/confirm removes access (row kept, inactive)', JSON.stringify(mems()) === '[["org_admin",0]]', JSON.stringify(mems()))
// non-owner cannot use it
await login('wholesale@evosyspro.live')
await page.goto(APP + '/god/access?org=' + ccb, { waitUntil: 'networkidle2' }); await sleep(1200)
const tn = await text()
ok('13. a non-owner gets no Manage Access screen', !tn.includes('Give someone access') && !tn.includes('GIVE ACCESS'))
const r = await fetch(API + '/god/access/users/' + ws + '/apply', { method: 'POST', headers: { Authorization: 'Bearer ' + tok, 'Content-Type': 'application/json' }, body: JSON.stringify({ operations: [{ op: 'add_membership', scope_type: 'customer_org', scope_id: ccb, role: 'org_admin' }] }) })
ok('14. the apply API refuses a non-owner', [401, 403, 404].includes(r.status), String(r.status))
console.log('page errors:', errors.length ? errors.join(' | ') : 'none')
await browser.close()
