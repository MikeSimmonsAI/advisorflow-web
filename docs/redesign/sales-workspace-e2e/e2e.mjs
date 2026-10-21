// Lane F end-to-end rep journey against the LOCAL app with synthetic data.
import chromium from '@sparticuz/chromium'
import pp from 'puppeteer-core'
import fs from 'node:fs'

const OUT = process.argv[2]
const OUTBOX = process.argv[3]
const APP = 'http://127.0.0.1:4173'
const API = 'http://127.0.0.1:8000'
fs.mkdirSync(OUT, { recursive: true })
const steps = []
const ok = (n, cond, extra = '') => { steps.push(`${cond ? 'PASS' : 'FAIL'}  ${n}${extra ? ' — ' + extra : ''}`); if (!cond) process.exitCode = 1 }
const sleep = ms => new Promise(r => setTimeout(r, ms))

async function token(email) {
  const r = await fetch(API + '/auth/login', { method: 'POST', headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    body: 'username=' + encodeURIComponent(email) + '&password=' + encodeURIComponent('LocalTest!1') })
  return (await r.json()).access_token
}
const api = async (tok, path, opts = {}) => {
  const r = await fetch(API + path, { ...opts, headers: { Authorization: 'Bearer ' + tok, 'Content-Type': 'application/json', ...(opts.headers || {}) } })
  let body = null; try { body = await r.json() } catch {}
  return { status: r.status, body }
}

const browser = await pp.launch({ executablePath: await chromium.executablePath(), args: chromium.args, headless: true })
const blocked = new Set(); const errors = []
async function newPage(w = 1440, h = 900, ctx = null) {
  const page = await (ctx || browser).newPage()
  await page.setViewport({ width: w, height: h })
  await page.setRequestInterception(true)
  page.on('request', r => {
    const u = r.url(); const h = u.startsWith('http') ? new URL(u).hostname : ''
    if (!u.startsWith('http') || ['127.0.0.1', 'localhost'].includes(h)) r.continue()
    else { blocked.add(h); r.abort() }
  })
  page.on('console', m => { if (m.type() === 'error' && !/Failed to load resource|tel:|sms:/.test(m.text())) errors.push(m.text()) })
  page.on('pageerror', e => errors.push('PAGEERROR ' + e.message))
  return page
}
async function login(page, email) {
  await page.goto(APP + '/login', { waitUntil: 'networkidle2' })
  await page.type('input[type=email], input[name=email], input[autocomplete=username]', email)
  await page.type('input[type=password]', 'LocalTest!1')
  await Promise.all([page.waitForNavigation({ waitUntil: 'networkidle2' }).catch(() => {}), page.keyboard.press('Enter')])
  await sleep(1200)
}
const clickText = async (page, sel, text) => {
  const ok = await page.evaluate((sel, text) => {
    const el = [...document.querySelectorAll(sel)].find(e => e.textContent.trim().includes(text) && !e.disabled)
    if (el) { el.click(); return true } return false
  }, sel, text)
  await sleep(700)
  return ok
}
const bodyText = page => page.evaluate(() => document.body.innerText)
const shot = (page, n) => page.screenshot({ path: `${OUT}/${n}.png`, fullPage: true })

process.on('exit', () => { console.log(steps.join('\n')) })
// ── rep journey ───────────────────────────────────────────────────────────
const repTok = await token('rep@example.test')
const page = await newPage()
await login(page, 'rep@example.test')
ok('1. Rep logs in and lands on My Day', page.url().endsWith('/sales') && (await bodyText(page)).includes("Today's Priorities"), page.url())
await shot(page, 'e2e-01-my-day')

await clickText(page, 'button.ss-qgreen', 'Start Follow Ups')
ok('2a. Start Follow Ups opens the list', page.url().endsWith('/sales/follow-ups'))
await clickText(page, 'button.ss-pill', 'Overdue')
const overdueRows = await page.$$eval('.ss-listrow strong', els => els.map(e => e.textContent))
ok('2b. Overdue filter shows only the overdue follow-up', overdueRows.length === 1 && overdueRows[0] === 'Testfamily Alvarez', overdueRows.join(', '))
ok('2c. Another rep\'s overdue deal is not on this list', !(await bodyText(page)).includes('Testfamily Zed'))
await shot(page, 'e2e-02-follow-ups-overdue')
await clickText(page, '.ss-listrow button', 'Open')
ok('3. Opens Lead Details', /\/sales\/leads\//.test(page.url()) && (await bodyText(page)).includes('Testfamily Alvarez'))
const oppId = page.url().split('/').pop()

await clickText(page, '.ss-detail-actions button', 'Call')
ok('4a. Call opens "What happened?"', (await bodyText(page)).includes('What happened?'))
const outcomeLabels = await page.$$eval('.ss-outcome', els => els.map(e => e.textContent.trim()))
ok('4b. Six outcome choices', outcomeLabels.join('|') === 'Connected|No Answer|Left Voicemail|Interested|Not Interested|Book a Meeting', outcomeLabels.join(', '))
await clickText(page, '.ss-outcome', 'Interested')
await page.type('#ss-call-note', 'Wants to see the platform (local test)')
const suggested = await page.$eval('#ss-next', e => e.value)
ok('5a. Picking an outcome suggests the next step', suggested === 'Book a demo', suggested)
await shot(page, 'e2e-03-what-happened')
await clickText(page, '.ss-modal button', 'Save Call Result')
await sleep(800)
let d = (await api(repTok, '/sales/opportunities/' + oppId)).body
ok('5b. Call result saved to the real history', d.timeline.some(e => e.event_type === 'call_outcome' && e.summary.startsWith('Call: Interested')))
ok('5c. Prospect → Contacted after a real conversation', d.stage === 'contacted', d.stage)
ok('5d. Next action and date saved', d.next_action === 'Book a demo' && !!d.next_action_due_at, d.next_action + ' ' + d.next_action_due_at)

await clickText(page, '.ss-detail-actions button', 'Text')
ok('4c. Text opens the message panel', (await bodyText(page)).includes('Nothing is sent from here'))
const smsHref = await page.$eval('a.ss-btn.ss-blue', a => a.getAttribute('href'))
ok('4d. Text uses the phone\'s own messages app', smsHref.startsWith('sms:2145550110?&body='), smsHref.slice(0, 30))
await clickText(page, '.ss-modal button', 'I sent it')
await sleep(600)
d = (await api(repTok, '/sales/opportunities/' + oppId)).body
ok('4e. Text added to history only when the rep says it was sent', d.timeline.some(e => e.event_type === 'text_logged'))

await clickText(page, '.ss-detail-actions button', 'Book Demo')
await sleep(1200)
ok('6a. Book Demo opens with this contact picked', page.url().includes('/sales/book?opp=' + oppId) && (await page.$eval('#bk-contact', s => s.value)) === oppId)
await clickText(page, 'label.ss-choice', 'Product Demo')
await sleep(1200)
const firstTime = await page.$$eval('.ss-time', b => b.length ? b[0].textContent : null)
await clickText(page, '.ss-time', firstTime || '')
await shot(page, 'e2e-04-book-a-demo')
const outboxBefore = fs.readFileSync(OUTBOX, 'utf8').split('\n').filter(Boolean).length
await clickText(page, '.ss-formside button', 'Create Demo')
await sleep(1500)
ok('6b. Demo booked through the existing scheduler', (await bodyText(page)).includes('Booked.'), firstTime)
const outbox = fs.readFileSync(OUTBOX, 'utf8').split('\n').filter(Boolean).slice(outboxBefore)
ok('6c. Invitation/video/calendar calls were caught by the local recorder (nothing left this machine)',
   outbox.some(l => l.includes('send_prospect_invitation')), outbox.map(l => JSON.parse(l).blocked).join(', '))
await clickText(page, 'button.ss-link', 'Move to Demo Scheduled')
await sleep(600)
d = (await api(repTok, '/sales/opportunities/' + oppId)).body
ok('8a. Deal moved to Demo Scheduled on an explicit click', d.stage === 'demo_proposal', d.stage)
await shot(page, 'e2e-05-booked')
await clickText(page, 'button', 'See it in Appointments')
await sleep(1500)
const evt = await page.$$eval('.ss-event', b => b.map(e => e.textContent))
ok('7. The demo shows on the Appointments calendar', evt.some(t => t.includes('Testfamily Alvarez') || t.includes('Alvarez')), evt.join(' | '))
await shot(page, 'e2e-06-appointments')

await page.goto(APP + '/sales/pipeline', { waitUntil: 'networkidle2' }); await sleep(800)
const demoCol = await page.$$eval('.ss-boardcol', cols => cols.map(c => c.innerText))
ok('8b. Pipeline shows the deal under Demo Scheduled', demoCol[2].includes('Testfamily Alvarez'))
await shot(page, 'e2e-07-pipeline')

await page.goto(APP + '/sales/leads/' + oppId, { waitUntil: 'networkidle2' }); await sleep(800)
await page.select('#ss-stage-' + oppId, 'won'); await sleep(500)
ok('9a. Won asks for one confirming click', (await bodyText(page)).includes('Mark this deal Won?'))
await clickText(page, 'button', 'Yes, mark Won'); await sleep(1200)
d = (await api(repTok, '/sales/opportunities/' + oppId)).body
ok('9b. Deal is Won', d.stage === 'won' && d.status === 'won')
ok('9c. Hand-off to onboarding shown (no customer created)', (await bodyText(page)).includes('Won — ready to hand off to onboarding') && !d.customer_organization_id)
await shot(page, 'e2e-08-won-handoff')
await clickText(page, 'button', 'Open Sold / Onboarding'); await sleep(1500)
ok('10. Opens the existing Sold / Onboarding area', page.url().endsWith('/sales/onboarding'))
await shot(page, 'e2e-09-onboarding')

// keyboard: Tab reaches a row's Call button; Enter opens the panel; Escape closes it
await page.goto(APP + '/sales/follow-ups', { waitUntil: 'networkidle2' }); await sleep(800)
let reached = false
for (let i = 0; i < 60 && !reached; i++) {
  await page.keyboard.press('Tab')
  reached = await page.evaluate(() => (document.activeElement?.getAttribute('aria-label') || '').startsWith('Call '))
}
ok('11a. Keyboard: Tab reaches a Call button', reached)
await page.keyboard.press('Enter'); await sleep(600)
ok('11b. Keyboard: Enter opens "What happened?"', (await bodyText(page)).includes('What happened?'))
await page.keyboard.press('Escape'); await sleep(400)
ok('11c. Keyboard: Escape closes it', !(await bodyText(page)).includes('What happened?'))

const repNav = await page.$$eval('.sw-sidebar nav a', a => a.map(x => x.textContent.trim()))
ok('12a. Rep sees the seven-item nav and no manager items', ['My Day', 'Leads', 'Follow Ups', 'Appointments', 'Demos to Build', 'Pipeline', 'Reports'].every(l => repNav.some(n => n.startsWith(l))) && !repNav.some(n => /Team Command|Salespeople|Team Pipeline/.test(n)), repNav.join(', '))

// ── server-side permissions (not just hidden links) ───────────────────────
const others = await api(repTok, '/sales/opportunities')
const all = others.body.stages.flatMap(s => s.opportunities)
ok('13a. Rep\'s deal list excludes another rep\'s deal', !all.some(o => o.contact_name === 'Testfamily Zed'))
const mgrTok = await token('manager@example.test')
const mgrAll = (await api(mgrTok, '/sales/opportunities')).body.stages.flatMap(s => s.opportunities)
const zed = mgrAll.find(o => o.contact_name === 'Testfamily Zed')
const peek = await api(repTok, '/sales/opportunities/' + zed.id)
ok('13b. Rep cannot open another rep\'s deal', [403, 404].includes(peek.status), 'HTTP ' + peek.status)
const poke = await api(repTok, '/sales/opportunities/' + zed.id + '/notes', { method: 'POST', body: JSON.stringify({ summary: 'should fail', event_type: 'call_outcome' }) })
ok('13c. Rep cannot log a call on another rep\'s deal', [403, 404].includes(poke.status), 'HTTP ' + poke.status)
const mgrOnly = await api(repTok, '/sales/manager/overview')
ok('13d. Manager-only data refused to a rep', mgrOnly.status === 403, 'HTTP ' + mgrOnly.status)

const mpage = page
await mpage.evaluate(() => { localStorage.clear(); sessionStorage.clear() })
const cdp = await mpage.createCDPSession(); await cdp.send('Network.clearBrowserCookies')
await login(mpage, 'manager@example.test')
const mgrNav = await mpage.$$eval('.sw-sidebar nav a', a => a.map(x => x.textContent.trim()))
ok('14. Manager keeps MY TEAM (Team Command etc.) alongside the simple nav', mgrNav.some(n => n.startsWith('Team Command')) && mgrNav.some(n => n.startsWith('Follow Ups')), mgrNav.join(', '))
const mgrFollow = await mpage.goto(APP + '/sales/follow-ups', { waitUntil: 'networkidle2' }).then(() => sleep(800)).then(() => bodyText(mpage))
ok('14a. Manager\'s Follow Ups badge counts their own list (none)', mgrNav.some(n => n === 'Follow Ups'), mgrNav.find(n => n.startsWith('Follow Ups')))
ok('14b. Manager\'s Follow Ups is their own book, not the whole team', !mgrFollow.includes('Testfamily Zed') && !mgrFollow.includes('Testfamily Brooks'))
await mpage.screenshot({ path: `${OUT}/e2e-10-manager-nav.png`, fullPage: true })

// ── accessibility (axe-core) on the redesigned screens, as the rep ─────────
await mpage.evaluate(() => { localStorage.clear(); sessionStorage.clear() })
await cdp.send('Network.clearBrowserCookies')
await login(mpage, 'rep@example.test')
const axeSrc = fs.readFileSync(new URL('./node_modules/axe-core/axe.min.js', import.meta.url), 'utf8')
const leadId = (await api(repTok, '/sales/opportunities')).body.stages.flatMap(s => s.opportunities)[0].id
for (const [n, path] of [['My Day', '/sales'], ['Follow Ups', '/sales/follow-ups'], ['Lead Details', '/sales/leads/' + leadId],
    ['Book a Demo', '/sales/book?opp=' + leadId], ['Pipeline', '/sales/pipeline'], ['Appointments', '/sales/appointments'], ['Reports', '/sales/my-results']]) {
  await mpage.goto(APP + path, { waitUntil: 'networkidle2' }); await sleep(700)
  await mpage.evaluate(axeSrc)
  const res = await mpage.evaluate(async () => (await window.axe.run(document, { resultTypes: ['violations'] })).violations
    .filter(v => ['serious', 'critical'].includes(v.impact)).map(v => v.id + '(' + v.nodes.length + '): ' + v.nodes.slice(0, 2).map(x => x.target.join(' ')).join(' ; ')))
  ok('15. Accessibility, no serious/critical issues — ' + n, res.length === 0, res.join(' | '))
}
console.log('blocked hosts:', [...blocked].join(', ') || 'none')
console.log('console errors:', errors.length ? errors.join('\n  ') : 'none')
await browser.close()
