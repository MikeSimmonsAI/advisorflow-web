// Lane F end-to-end rep journey (master prompt 2026-10-21 §13) against the
// LOCAL app with synthetic data. Any request to a non-local host is aborted.
import pp from 'puppeteer-core'
import fs from 'node:fs'
// Browser: SW_CHROME=<path to a headless-shell build> (recommended - see README),
// otherwise @sparticuz/chromium.
async function launchOpts() {
  if (process.env.SW_CHROME) return { executablePath: process.env.SW_CHROME, args: ['--no-sandbox', '--disable-dev-shm-usage'], headless: true }
  const chromium = (await import('@sparticuz/chromium')).default
  return { executablePath: await chromium.executablePath(), args: chromium.args, headless: true }
}

const OUT = process.argv[2]
const OUTBOX = process.argv[3]
const APP = process.env.SW_APP || 'http://localhost:5173'  // must be an origin the backend allows (README)
const API = 'http://127.0.0.1:8000'
fs.mkdirSync(OUT, { recursive: true })
const steps = []
const ok = (n, cond, extra = '') => { steps.push(`${cond ? 'PASS' : 'FAIL'}  ${n}${extra ? ' — ' + extra : ''}`); if (!cond) process.exitCode = 1 }
const sleep = ms => new Promise(r => setTimeout(r, ms))
process.on('exit', () => { console.log(steps.join('\n')) })

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
const outboxLines = () => fs.readFileSync(OUTBOX, 'utf8').split('\n').filter(Boolean)
const setDate = (page, sel, v) => page.$eval(sel, (e, v) => { const set = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set; set.call(e, v); e.dispatchEvent(new Event('input', { bubbles: true })) }, v)

const browser = await pp.launch(await launchOpts())
const blocked = new Set(); const errors = []
async function newPage(w = 1440, h = 900) {
  const page = await browser.newPage()
  await page.setViewport({ width: w, height: h })
  await page.setRequestInterception(true)
  page.on('request', r => {
    const u = r.url(); const host = u.startsWith('http') ? new URL(u).hostname : ''
    if (!u.startsWith('http') || ['127.0.0.1', 'localhost'].includes(host)) r.continue()
    else { blocked.add(host); r.abort() }
  })
  page.on('console', m => { if (m.type() === 'error' && !/Failed to load resource|tel:|sms:|net::ERR/.test(m.text())) errors.push(m.text()) })
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
async function logout(page) {
  await page.evaluate(() => { localStorage.clear(); sessionStorage.clear() })
  const cdp = await page.createCDPSession(); await cdp.send('Network.clearBrowserCookies')
}
const clickText = async (page, sel, text, exact = false) => {
  const done = await page.evaluate((sel, text, exact) => {
    const el = [...document.querySelectorAll(sel)].find(e => (exact ? e.textContent.trim() === text : e.textContent.trim().includes(text)) && !e.disabled)
    if (el) { el.click(); return true } return false
  }, sel, text, exact)
  await sleep(700)
  return done
}
const bodyText = page => page.evaluate(() => document.body.innerText)
const shot = (page, n) => page.screenshot({ path: `${OUT}/${n}.png`, fullPage: true })
const pills = page => page.$$eval('button.ss-pill', b => Object.fromEntries(b.map(x => {
  const m = x.textContent.trim().match(/^(.*) \((\d+)\)$/); return m ? [m[1], Number(m[2])] : [x.textContent.trim(), null] })))
const badge = page => page.$$eval('.sw-sidebar nav a', a => {
  const f = a.find(x => x.textContent.trim().startsWith('Follow Ups')); const c = f && f.querySelector('.sw-count'); return c ? Number(c.textContent) : 0 })
const rowsNames = page => page.$$eval('.ss-listrow .ss-personinfo strong', els => els.map(e => e.textContent))
async function byName(tok, name) {
  const r = (await api(tok, '/sales/opportunities?include_lost=true')).body
  return r.stages.flatMap(s => s.opportunities).concat(r.lost || []).find(o => o.contact_name === name)
}
// Stop the page following tel:/sms: so the test never leaves the browser.
const noDial = page => page.evaluate(() => { try { Object.defineProperty(window, 'location', { value: window.location }) } catch {} })

const repTok = await token('rep@example.test')
// A lead with no phone, due today — Call/Text must not be offered for it.
await api(repTok, '/sales/opportunities', { method: 'POST', body: JSON.stringify({
  company_name: 'Nophone Test Co', contact_name: 'Testfamily Nophone', email: 'nophone@example.test',
  next_action: 'Find a number', next_action_due_at: new Date().toISOString().slice(0, 10) + 'T12:00:00' }) })

// ── 1–4 My Day → Start Follow Ups → Due Now ───────────────────────────────
const page = await newPage()
await login(page, 'rep@example.test')
ok('1. Rep logs in and lands on My Day', page.url().endsWith('/sales') && (await bodyText(page)).includes("Today's Priorities"), page.url())
const repDay = await bodyText(page)
ok('2. Rep My Day: personal numbers only (no team row)', !repDay.includes('Your team') && !repDay.includes('Your numbers'))
await shot(page, 'e2e-01-my-day-rep')
await clickText(page, 'button.ss-qgreen', 'Start Follow Ups')
ok('3. Start Follow Ups opens Follow Ups', page.url().endsWith('/sales/follow-ups'))
let p = await pills(page); let b = await badge(page)
const txt = await bodyText(page)
ok('4a. Filters read Due Now | Overdue | Today | Completed', Object.keys(p).join('|') === 'Due Now|Overdue|Today|Completed', Object.keys(p).join('|'))
ok('4b. "People to Contact Now" / "Overdue first, then today\'s follow-ups."', txt.includes('People to Contact Now') && txt.includes("Overdue first, then today's follow-ups."))
ok('4c. Due Now = Overdue + Today = sidebar badge', p['Due Now'] === p['Overdue'] + p['Today'] && p['Due Now'] === b, JSON.stringify(p) + ' badge ' + b)
const dueRows = await rowsNames(page)
ok('4d. Due Now rows = its count, no duplicates', dueRows.length === p['Due Now'] && new Set(dueRows).size === dueRows.length, dueRows.join(', '))
ok('4e. No-phone lead offers no Call/Text', await page.$$eval('.ss-listrow', rs => {
  const r = rs.find(x => x.innerText.includes('Testfamily Nophone')); return !!r && !r.querySelector('.ss-square') && r.innerText.includes('No phone') }))
await shot(page, 'e2e-02-follow-ups-due-now')
const dueNow0 = p['Due Now']

// ── 5–9 Overdue → Lead Details → Call → No Answer + retry date ───────────
await clickText(page, 'button.ss-pill', 'Overdue')
const over = await rowsNames(page)
ok('5. Overdue filter shows only overdue items', over.length === p['Overdue'] && over.includes('Testfamily Alvarez'), over.join(', '))
await page.$$eval('.ss-listrow', rs => rs.find(x => x.innerText.includes('Testfamily Alvarez')).querySelector('.ss-personinfo').click()); await sleep(900)
ok('6. Opens Lead Details', /\/sales\/leads\//.test(page.url()) && (await bodyText(page)).includes('Testfamily Alvarez'))
const alvarezId = page.url().split('/').pop()
await clickText(page, '.ss-detail-actions button', 'Call')
const callTxt = await bodyText(page)
ok('7a. Call opens "What happened?"; says nothing is recorded yet', callTxt.includes('What happened?') && callTxt.includes('Nothing is recorded until you save'))
ok('7b. Desktop fallback: number + Copy number', callTxt.includes('Copy number') && callTxt.includes('(214) 555-0110'))
let d = (await api(repTok, '/sales/opportunities/' + alvarezId)).body
ok('7c. Clicking Call logged nothing', !d.timeline.some(e => e.event_type === 'call_outcome'))
const outcomes = await page.$$eval('.ss-outcome', els => els.map(e => e.textContent.trim()))
ok('7d. Six outcome choices, approved order', outcomes.join('|') === 'Connected|No Answer|Left Voicemail|Interested|Not Interested|Book a Meeting', outcomes.join(', '))
await shot(page, 'e2e-03-what-happened')
await clickText(page, '.ss-outcome', 'No Answer', true)
const suggested = await page.$eval('#ss-next', e => e.value)
const retry = new Date(Date.now() + 2 * 86400000).toISOString().slice(0, 10)
await setDate(page, '#ss-due', retry)
ok('8a. No Answer suggests a retry the rep can change', suggested === 'Try calling again', suggested)
await clickText(page, '.ss-modal .ss-modalfoot button', 'Save Call Result'); await sleep(900)
d = (await api(repTok, '/sales/opportunities/' + alvarezId)).body
ok('8b. Outcome saved to history', d.timeline.some(e => e.event_type === 'call_outcome' && e.summary.startsWith('Call: No answer')))
ok('9a. Retry date and next action persist', d.next_action === 'Try calling again' && String(d.next_action_due_at).startsWith(retry), d.next_action + ' ' + d.next_action_due_at)
ok('9b. Stage unchanged by No Answer', d.stage === 'prospect', d.stage)
ok('9c. Sidebar badge dropped without a reload', (await badge(page)) === dueNow0 - 1, String(await badge(page)))
await page.reload({ waitUntil: 'networkidle2' }); await sleep(800)
ok('9d. After reload Lead Details shows the new next action', (await page.$eval('#ss-na-' + alvarezId + 'c', e => e.value)) === 'Try calling again')

// ── Text: opened ≠ sent; logged only on "I sent it" ──────────────────────
await clickText(page, '.ss-detail-actions button', 'Text')
ok('T1. Text panel: EvoSys does not send or see delivery', (await bodyText(page)).includes("EvoSys doesn’t send it and can’t see delivery"))
d = (await api(repTok, '/sales/opportunities/' + alvarezId)).body
ok('T2. Opening the Text panel logged nothing', !d.timeline.some(e => e.event_type === 'text_logged'))
await clickText(page, '.ss-modal button', 'I sent it'); await sleep(700)
d = (await api(repTok, '/sales/opportunities/' + alvarezId)).body
ok('T3. Logged only after "I sent it", worded as rep-confirmed', d.timeline.some(e => e.event_type === 'text_logged' && e.summary.startsWith('Text sent (confirmed by rep)')))

await page.goto(APP + '/sales/follow-ups', { waitUntil: 'networkidle2' }); await sleep(900)
p = await pills(page); b = await badge(page)
ok('9e. Rescheduled lead left Due Now; count and badge agree', !(await rowsNames(page)).includes('Testfamily Alvarez') && p['Due Now'] === dueNow0 - 1 && b === p['Due Now'], `due ${dueNow0}→${p['Due Now']} badge ${b}`)

// ── 10–14 Interested → Save & book a demo → book (triple click) ──────────
const chen = await byName(repTok, 'Testfamily Chen')
await page.$$eval('.ss-listrow', rs => rs.find(x => x.innerText.includes('Testfamily Chen')).querySelector('button[aria-label^="Call"]').click()); await sleep(700)
await clickText(page, '.ss-outcome', 'Interested', true)
await clickText(page, '.ss-modal .ss-modalfoot button', 'Save & book a demo'); await sleep(1500)
ok('10a. Interested → Save & book a demo opens Book a Demo, contact picked', page.url().includes('/sales/book?opp=' + chen.id) && (await page.$eval('#bk-contact', s => s.value)) === chen.id)
d = (await api(repTok, '/sales/opportunities/' + chen.id)).body
ok('10b. Interested saved; no booking claimed yet', d.timeline.some(e => e.summary.startsWith('Call: Interested')) && d.appointments.length === 0)
await clickText(page, 'label.ss-choice', 'Product Demo'); await sleep(1400)
const slot = await page.$$eval('.ss-time', b => b.length ? b[0].textContent : null)
ok('11. Open times offered for the day', !!slot, slot)
await clickText(page, '.ss-time', slot || '')
await shot(page, 'e2e-04-book-a-demo')
const ob0 = outboxLines().length
await page.evaluate(() => { const b = [...document.querySelectorAll('.ss-formside button')].find(x => x.textContent.includes('Create Demo')); b.click(); b.click(); b.click() })
await sleep(2200)
const bookedTxt = await bodyText(page)
ok('12. Booked through the existing scheduler', bookedTxt.includes('Booked.'))
ok('12b. Confirmation states the real invitation status, no delivery claim', /No invitation has gone out yet|handed to the mail service/.test(bookedTxt), (bookedTxt.match(/(No invitation[^.]*\.|Invitation email[^.]*\.)/) || [''])[0])
d = (await api(repTok, '/sales/opportunities/' + chen.id)).body
ok('13a. Triple-click created exactly one appointment', d.appointments.length === 1, String(d.appointments.length))
const ob = outboxLines().slice(ob0).map(l => JSON.parse(l).blocked)
ok('13b. Invitation/video/calendar caught by the local recorder only', ob.includes('appointment_invites.send_prospect_invitation') && ob.includes('video.ensure_meeting'), ob.join(', '))
await shot(page, 'e2e-05-booked')
await clickText(page, 'button.ss-link', 'Move to Demo & Closing'); await sleep(700)
await clickText(page, 'button', 'See it in Appointments'); await sleep(1500)
const evt = await page.$$eval('.ss-event', b => b.map(e => e.textContent))
ok('14. Demo shows on Appointments', evt.some(t => t.includes('Chen')), evt.join(' | '))
await shot(page, 'e2e-06-appointments')

// ── 15 Pipeline ──────────────────────────────────────────────────────────
await page.goto(APP + '/sales/pipeline', { waitUntil: 'networkidle2' }); await sleep(900)
const cols = await page.$$eval('.ss-boardcol', cs => cs.map(c => c.innerText))
ok('15a. Deal under Demo & Closing, exact stage shown', /Testfamily Chen[\s\S]*?Demo \/ Proposal/.test(cols[2]))
const addLead = await page.$$eval('button', bs => bs.filter(b => b.textContent.trim() === 'Add Lead').length)
ok('15b. Exactly one Add Lead control', addLead === 1, String(addLead))
ok('15c. Every card shows its real stage', await page.$$eval('.ss-dealcard', cs => cs.length > 0 && cs.every(c => !!c.querySelector('.ss-stagechip'))))
await page.reload({ waitUntil: 'networkidle2' }); await sleep(800)
ok('15d. Stage persists after refresh', /Testfamily Chen[\s\S]*?Demo \/ Proposal/.test((await page.$$eval('.ss-boardcol', cs => cs.map(c => c.innerText)))[2]))
await shot(page, 'e2e-07-pipeline')

// ── 16–17 Won → existing onboarding ─────────────────────────────────────
await page.goto(APP + '/sales/leads/' + chen.id, { waitUntil: 'networkidle2' }); await sleep(900)
await page.select('#ss-stage-' + chen.id, 'won'); await sleep(500)
ok('16a. Won asks for one confirming click', (await bodyText(page)).includes('Mark this deal Won?'))
await clickText(page, 'button', 'Yes, mark Won'); await sleep(1200)
d = (await api(repTok, '/sales/opportunities/' + chen.id)).body
ok('16b. Deal is Won; no customer created', d.stage === 'won' && d.status === 'won' && !d.customer_organization_id)
const wonTxt = await bodyText(page)
ok('17a. Hand-off shown; no owner-only provisioning offered to a rep', wonTxt.includes('Won — ready to hand off to onboarding') && !wonTxt.includes('Start onboarding (owner)'))
await shot(page, 'e2e-08-won-handoff')
await clickText(page, 'button', 'Open Sold / Onboarding'); await sleep(1500)
ok('17b. Opens the existing Sold / Onboarding area', page.url().endsWith('/sales/onboarding'))
const prov = await api(repTok, '/god/ops/opportunities/' + chen.id + '/provision', { method: 'POST', body: '{}' })
ok('17c. A rep cannot provision a customer (server)', [401, 403, 404].includes(prov.status), 'HTTP ' + prov.status)

// ── 18 Not Interested → Close as Lost ───────────────────────────────────
const diaz = await byName(repTok, 'Testfamily Diaz')
await page.goto(APP + '/sales/leads/' + diaz.id, { waitUntil: 'networkidle2' }); await sleep(900)
await clickText(page, '.ss-detail-actions button', 'Call')
await clickText(page, '.ss-outcome', 'Not Interested', true)
const niTxt = await bodyText(page)
const saveDisabled = await page.$$eval('.ss-modal .ss-modalfoot button', bs => bs[bs.length - 1].disabled)
ok('18a. Not Interested asks Close as Lost / Follow up later; Save waits for a choice', niTxt.includes('Close as Lost') && niTxt.includes('Follow up later') && saveDisabled)
ok('18b. No date or next action pre-filled', !(await page.$('#ss-due')))
await shot(page, 'e2e-09-not-interested-options')
await clickText(page, '.ss-decision', 'Close as Lost')
await clickText(page, '.ss-modal .ss-modalfoot button', 'Close as Lost'); await sleep(900)
d = (await api(repTok, '/sales/opportunities/' + diaz.id)).body
ok('18c. Closed as Lost; no future follow-up; history kept', d.stage === 'lost' && d.status === 'lost' && d.next_action === null && d.next_action_due_at === null &&
   d.timeline.some(e => e.summary.startsWith('Call: Not interested — closed as Lost')), `${d.stage} ${d.next_action} ${d.next_action_due_at}`)

// ── 19 Not Interested → Follow up later (date required) ─────────────────
const garcia = await byName(repTok, 'Testfamily Garcia')
await page.goto(APP + '/sales/leads/' + garcia.id, { waitUntil: 'networkidle2' }); await sleep(900)
await clickText(page, '.ss-detail-actions button', 'Call')
await clickText(page, '.ss-outcome', 'Not Interested', true)
await clickText(page, '.ss-decision', 'Follow up later')
ok('19a. Later shows empty, required action and date', (await page.$eval('#ss-next', e => e.value)) === '' && (await page.$eval('#ss-due', e => e.value)) === '')
await clickText(page, '.ss-modal .ss-modalfoot button', 'Save follow-up')
ok('19b. Saving without action/date is refused with a clear message', (await bodyText(page)).includes('Add the next action for the later follow-up.'))
d = (await api(repTok, '/sales/opportunities/' + garcia.id)).body
ok('19c. The refused attempt saved nothing', !d.timeline.some(e => e.event_type === 'call_outcome'))
await page.type('#ss-next', 'Check back after budget season')
const later = new Date(Date.now() + 60 * 86400000).toISOString().slice(0, 10)
await setDate(page, '#ss-due', later)
await clickText(page, '.ss-modal .ss-modalfoot button', 'Save follow-up'); await sleep(900)
d = (await api(repTok, '/sales/opportunities/' + garcia.id)).body
ok('19d. Later follow-up saved with the chosen date; real stage kept', d.stage === 'demo_proposal' && d.next_action === 'Check back after budget season' && String(d.next_action_due_at).startsWith(later), `${d.stage} ${d.next_action_due_at}`)

// ── Book a Meeting → leave the form → nothing fabricated ────────────────
const brooks = await byName(repTok, 'Testfamily Brooks')
await page.goto(APP + '/sales/leads/' + brooks.id, { waitUntil: 'networkidle2' }); await sleep(900)
await clickText(page, '.ss-detail-actions button', 'Call')
await clickText(page, '.ss-outcome', 'Book a Meeting', true)
await clickText(page, '.ss-modal .ss-modalfoot button', 'Save & open booking'); await sleep(1500)
ok('BM1. Book a Meeting opens the booking form for that contact', page.url().includes('/sales/book?opp=' + brooks.id))
await page.goto(APP + '/sales/follow-ups', { waitUntil: 'networkidle2' }); await sleep(900)
d = (await api(repTok, '/sales/opportunities/' + brooks.id)).body
ok('BM2. Leaving the form books nothing and claims nothing', d.appointments.length === 0 && d.timeline.some(e => e.summary.startsWith('Call: Agreed to a meeting (not booked yet)')) &&
   !d.timeline.some(e => e.event_type === 'appointment_booked'))

// ── 20 nothing prohibited, nothing duplicated; Completed ─────────────────
const cal = (await api(repTok, '/sales/calendar/view?date_from=' + new Date().toISOString().slice(0,10) + '&date_to=' + new Date(Date.now()+30*86400000).toISOString().slice(0,10) + '&scope=mine&include_external=false')).body
ok('20a. Exactly one appointment exists in total', (cal.appointments || []).length === 1, String((cal.appointments || []).length))
const lostNow = await byName(repTok, 'Testfamily Diaz')
ok('20b. Lost deal has no follow-up and is off Due Now', !lostNow.next_action_due_at && !(await rowsNames(page)).includes('Testfamily Diaz'))
p = await pills(page)
const worked = (await api(repTok, '/sales/my-day?scope=mine')).body.worked_today
ok('20c. Completed = every deal worked today on the server, no duplicates', p['Completed'] === worked.count && worked.count === 5 && new Set(worked.opportunity_ids).size === 5, JSON.stringify(p) + ' server ' + worked.count)
await clickText(page, 'button.ss-pill', 'Completed')
await shot(page, 'e2e-10-follow-ups-completed')

await clickText(page, 'button.ss-pill', 'Due Now')
let reached = false
for (let i = 0; i < 80 && !reached; i++) { await page.keyboard.press('Tab'); reached = await page.evaluate(() => (document.activeElement?.getAttribute('aria-label') || '').startsWith('Call ')) }
ok('K1. Keyboard: Tab reaches a Call button', reached)
await page.keyboard.press('Enter'); await sleep(600)
ok('K2. Keyboard: Enter opens "What happened?"', (await bodyText(page)).includes('What happened?'))
await page.keyboard.press('Escape'); await sleep(400)
ok('K3. Keyboard: Escape closes it', !(await bodyText(page)).includes('What happened?'))

// ── server-side permissions ─────────────────────────────────────────────
const mgrTok = await token('manager@example.test')
const zed = (await api(mgrTok, '/sales/opportunities')).body.stages.flatMap(s => s.opportunities).find(o => o.contact_name === 'Testfamily Zed')
ok('P1. Another rep\'s deal is not in this rep\'s list', !(await api(repTok, '/sales/opportunities')).body.stages.flatMap(s => s.opportunities).some(o => o.id === zed.id))
ok('P2. Rep cannot open another rep\'s deal', [403, 404].includes((await api(repTok, '/sales/opportunities/' + zed.id)).status))
ok('P3. Rep cannot change another rep\'s stage', [403, 404].includes((await api(repTok, '/sales/opportunities/' + zed.id, { method: 'PATCH', body: JSON.stringify({ stage: 'lost' }) })).status))
ok('P4. Manager-only data refused to a rep', (await api(repTok, '/sales/manager/overview')).status === 403)
const otherBrand = await api(repTok, '/sales/my-day?brand_sales_org_id=not-my-brand')
ok('P5. A rep cannot read another sales organization', [403, 404].includes(otherBrand.status), 'HTTP ' + otherBrand.status)

// ── manager ─────────────────────────────────────────────────────────────
await logout(page); await login(page, 'manager@example.test')
const mDay = await bodyText(page)
const mTeam = (await api(mgrTok, '/sales/my-day')).body
const mb0 = await badge(page)
const cardFollow = (await page.$$eval('.ss-stat', c => c.map(x => x.innerText))).find(t => t.includes('Follow Ups'))
const teamFollow = (await page.$$eval('.ss-teamstat', c => c.map(x => x.innerText))).find(t => t.includes('Follow ups due'))
ok('M1. Manager: "Your numbers" and a separate "Your team"', mDay.includes('Your numbers') && mDay.includes('Your team'))
ok('M2. Personal card = own list = badge; team row = team total', cardFollow.trim().startsWith(String(mb0)) && teamFollow.trim().startsWith(String(mTeam.metrics.follow_ups_due)), `card ${cardFollow.split('\n')[0]} badge ${mb0} team ${mTeam.metrics.follow_ups_due}`)
ok('M3. Team Command reachable', mDay.includes('Open Team Command') && (await page.$$eval('.sw-sidebar nav a', a => a.some(x => x.textContent.includes('Team Command')))))
await shot(page, 'e2e-11-manager-my-day')
await page.goto(APP + '/sales/follow-ups', { waitUntil: 'networkidle2' }); await sleep(900)
const mp = await pills(page); const mb = await badge(page)
ok('M4. Manager Follow Ups = own book; Due Now = badge', mp['Due Now'] === mb && (await bodyText(page)).includes('Testfamily Manager') && !(await bodyText(page)).includes('Testfamily Zed'), JSON.stringify(mp) + ' badge ' + mb)

// ── accessibility (axe-core) as the rep ─────────────────────────────────
await logout(page); await login(page, 'rep@example.test')
const axeSrc = fs.readFileSync(new URL('./node_modules/axe-core/axe.min.js', import.meta.url), 'utf8')
const lead = await byName(repTok, 'Testfamily Evans')
const runAxe = async () => { await page.evaluate(axeSrc); return page.evaluate(async () => (await window.axe.run(document, { resultTypes: ['violations'] })).violations
  .filter(v => ['serious', 'critical'].includes(v.impact)).map(v => v.id + '(' + v.nodes.length + '): ' + v.nodes.slice(0, 2).map(x => x.target.join(' ')).join(' ; '))) }
for (const [n, path] of [['My Day', '/sales'], ['Follow Ups', '/sales/follow-ups'], ['Lead Details', '/sales/leads/' + lead.id],
    ['Book a Demo', '/sales/book?opp=' + lead.id], ['Pipeline', '/sales/pipeline'], ['Appointments', '/sales/appointments'], ['Reports', '/sales/my-results']]) {
  await page.goto(APP + path, { waitUntil: 'networkidle2' }); await sleep(700)
  const res = await runAxe()
  ok('A. axe no serious/critical — ' + n, res.length === 0, res.join(' | '))
}
await page.goto(APP + '/sales/leads/' + lead.id, { waitUntil: 'networkidle2' }); await sleep(700)
await clickText(page, '.ss-detail-actions button', 'Call'); await clickText(page, '.ss-outcome', 'Not Interested', true)
const resM = await runAxe()
ok('A. axe no serious/critical — "What happened?" (Not Interested)', resM.length === 0, resM.join(' | '))
ok('A2. Keyboard focus is inside the open dialog', await page.evaluate(() => !!document.querySelector('[role=dialog]')?.contains(document.activeElement)))
const small = await page.$$eval('.ss-modal button, .ss-modal input', els => els.filter(e => { const r = e.getBoundingClientRect(); return r.height > 0 && r.height < 30 }).map(e => e.textContent.trim() || e.id))
ok('A3. Dialog controls are at least 30px tall', small.length === 0, small.join(', '))

// ── phone width: main workflow screens ──────────────────────────────────
await page.keyboard.press('Escape'); await sleep(300)
await page.setViewport({ width: 390, height: 844 })
for (const [n, path] of [['m-my-day', '/sales'], ['m-follow-ups', '/sales/follow-ups'], ['m-lead-details', '/sales/leads/' + lead.id],
    ['m-book-a-demo', '/sales/book?opp=' + lead.id], ['m-pipeline', '/sales/pipeline'], ['m-appointments', '/sales/appointments']]) {
  await page.goto(APP + path, { waitUntil: 'networkidle2' }); await sleep(700)
  const over = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)
  ok('R. No sideways scroll on phone — ' + n, over <= 0, over + 'px')
  await shot(page, 'e2e-' + n)
}
await page.goto(APP + '/sales/leads/' + lead.id, { waitUntil: 'networkidle2' }); await sleep(700)
await clickText(page, '.ss-detail-actions button', 'Call'); await clickText(page, '.ss-outcome', 'Not Interested', true)
await shot(page, 'e2e-m-what-happened')

// ════════════════ CLOSEOUT (2026-10-21): phone More, phone dialog, Do Not Contact ════════════════
const vshot = (pg, n) => pg.screenshot({ path: `${OUT}/${n}.png`, fullPage: false })
const inView = (pg, sel) => pg.$eval(sel, e => { const r = e.getBoundingClientRect()
  return r.top >= 0 && r.left >= 0 && r.bottom <= window.innerHeight + 0.5 && r.right <= window.innerWidth + 0.5 && r.height > 0 })
const overflowX = pg => pg.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)
const BASE_MORE = ['Book a Demo', 'All Deal Stages', 'My Availability', 'Sold / Onboarding', 'My Compensation']
const PATHS = { 'Book a Demo': '/sales/book', 'All Deal Stages': '/sales/pipeline/stages', 'My Availability': '/sales/availability',
  'Demo Suite': '/demo-suite', 'Sold / Onboarding': '/sales/onboarding', 'My Compensation': '/sales/my-compensation', 'Team Availability': '/sales/team' }

async function phoneMore(who, email, expected, absent) {
  await logout(page); await page.setViewport({ width: 390, height: 844, hasTouch: true, isMobile: true }); await login(page, email)
  await page.goto(APP + '/sales', { waitUntil: 'networkidle2' }); await sleep(700)
  const box = await page.$eval('.ss-mmorebtn', e => { const r = e.getBoundingClientRect(); return { w: r.width, h: r.height, vis: getComputedStyle(e).display !== 'none' } }).catch(() => null)
  ok(`MN-${who}. "More" button shown on a phone, >=44x44`, !!box && box.vis && box.w >= 44 && box.h >= 44, JSON.stringify(box))
  // keyboard: Enter opens, focus moves in, Escape closes and returns focus
  await page.focus('.ss-mmorebtn'); await page.keyboard.press('Enter'); await sleep(300)
  const kb = await page.evaluate(() => ({ open: !!document.getElementById('ss-mmore-panel'),
    inside: !!document.getElementById('ss-mmore-panel')?.contains(document.activeElement),
    exp: document.querySelector('.ss-mmorebtn').getAttribute('aria-expanded') }))
  const labels = await page.$$eval('#ss-mmore-panel a', a => a.map(x => x.textContent.replace('LATER', '').trim()))
  const small = await page.$$eval('#ss-mmore-panel a, #ss-mmore-panel button', a => a.filter(x => x.getBoundingClientRect().height < 44).map(x => x.textContent.trim()))
  ok(`MN-${who}. Keyboard: Enter opens, focus inside, aria-expanded=true`, kb.open && kb.inside && kb.exp === 'true', JSON.stringify(kb))
  ok(`MN-${who}. Menu lists exactly the allowed pages`, JSON.stringify(labels) === JSON.stringify(expected), labels.join(', '))
  ok(`MN-${who}. Not offered: ${absent.join(', ') || '(none)'}`, absent.every(a => !labels.includes(a)))
  ok(`MN-${who}. Menu items and Sign out are >=44px tall; Sign out present`, small.length === 0 && (await page.$('#ss-mmore-panel .ss-mmoreout')) !== null, small.join(', '))
  ok(`MN-${who}. No sideways scroll with the menu open`, (await overflowX(page)) <= 0)
  await vshot(page, 'e2e-m-more-' + who)
  await page.keyboard.press('Escape'); await sleep(200)
  const esc = await page.evaluate(() => ({ open: !!document.getElementById('ss-mmore-panel'), back: document.activeElement?.classList.contains('ss-mmorebtn') }))
  ok(`MN-${who}. Escape closes and returns focus to More`, !esc.open && esc.back, JSON.stringify(esc))
  // outside tap closes
  await page.tap('.ss-mmorebtn'); await sleep(200); await page.tap('.sw-body'); await sleep(200)
  ok(`MN-${who}. Tapping outside closes it`, !(await page.$('#ss-mmore-panel')))
  // touch: every destination opens
  for (const l of expected) {
    await page.goto(APP + '/sales', { waitUntil: 'networkidle2' }); await sleep(400)
    await page.tap('.ss-mmorebtn'); await sleep(250)
    const h = await page.evaluateHandle(l => [...document.querySelectorAll('#ss-mmore-panel a')].find(a => a.textContent.includes(l)), l)
    await h.asElement().tap(); await sleep(900)
    const path = await page.evaluate(() => location.pathname)
    const t = await bodyText(page)
    ok(`MN-${who}. Tap "${l}" opens ${PATHS[l]}`, path === PATHS[l] && !/Sales workspace unavailable|Forbidden|not allowed/i.test(t), path)
  }
}
await phoneMore('rep', 'rep@example.test', [...BASE_MORE, 'Team Availability'], ['Demo Suite'])
await phoneMore('rep-with-demo', 'rep2@example.test', ['Book a Demo', 'All Deal Stages', 'My Availability', 'Demo Suite', 'Sold / Onboarding', 'My Compensation', 'Team Availability'], [])
await phoneMore('manager', 'manager@example.test', BASE_MORE, ['Demo Suite', 'Team Availability'])
const teamBar = await page.$$eval('.sw-sidebar nav[aria-label="My team"] a', a => a.map(x => x.textContent.trim()))
ok('MN-manager. MY TEAM pages stay in the phone top bar', teamBar.some(x => x.includes('Team Command')) && teamBar.some(x => x.includes('Team Availability')), teamBar.join(', '))

// ── phone call-result dialog: the longest Not Interested form ───────────
await logout(page); await page.setViewport({ width: 390, height: 844, hasTouch: true, isMobile: true }); await login(page, 'rep@example.test')
const mk = async (body) => (await api(repTok, '/sales/opportunities', { method: 'POST', body: JSON.stringify(body) })).body
const mobileLead = await mk({ company_name: 'Mobile Dialog Co', contact_name: 'Testfamily Mobile', phone: '(214) 555-0166', email: 'mobile@example.test', stage: 'contacted' })
const LONG = ('Spoke with the owner for a while about their season, budget and current vendor contract. ').repeat(23).slice(0, 2000)
for (const [w, h, label] of [[390, 844, '390x844'], [390, 600, '390x600'], [375, 480, '375x480-keyboard']]) {
  await page.setViewport({ width: w, height: h, hasTouch: true, isMobile: true })
  await page.goto(APP + '/sales/leads/' + mobileLead.id, { waitUntil: 'networkidle2' }); await sleep(700)
  await clickText(page, '.ss-detail-actions button', 'Call'); await clickText(page, '.ss-outcome', 'Not Interested', true)
  await clickText(page, '.ss-decision', 'Follow up later')
  await page.type('#ss-call-note', LONG.slice(0, 300)); await page.$eval('#ss-call-note', (e, v) => { const s = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value').set; s.call(e, v); e.dispatchEvent(new Event('input', { bubbles: true })) }, LONG)
  await page.type('#ss-next', 'Check back after budget season')
  // try to save with no date: the error must be visible, nothing lost
  await page.click('.ss-modalfoot .ss-primaryact'); await sleep(500)
  const errVis = await page.$eval('.ss-modal [role=alert]', e => { const r = e.getBoundingClientRect(); const b = e.closest('.ss-modalbody').getBoundingClientRect(); return r.top >= b.top - 1 && r.bottom <= b.bottom + 1 }).catch(() => false)
  const kept = await page.$eval('#ss-call-note', e => e.value.length)
  ok(`MD-${label}. Missing date: error shown in view, note kept (${kept} chars)`, errVis && kept === LONG.length)
  await page.$eval('#ss-due', e => e.scrollIntoView({ block: 'center' }))
  await setDate(page, '#ss-due', (await page.$eval('#ss-due', e => e.min)))
  // scroll the body to the very top: header + actions must still be on screen
  await page.$eval('.ss-modalbody', e => { e.scrollTop = 0 }); await sleep(150)
  const headTop = await inView(page, '.ss-modalhead h2'); const saveTop = await inView(page, '.ss-modalfoot .ss-primaryact'); const cancelTop = await inView(page, '.ss-modalfoot .ss-white')
  await page.$eval('.ss-modalbody', e => { e.scrollTop = e.scrollHeight }); await sleep(150)
  const headBot = await inView(page, '.ss-modalhead h2'); const saveBot = await inView(page, '.ss-modalfoot .ss-primaryact')
  ok(`MD-${label}. Header + Save + Cancel on screen at top and bottom of the scroll`, headTop && saveTop && cancelTop && headBot && saveBot, JSON.stringify({ headTop, saveTop, cancelTop, headBot, saveBot }))
  const tgt = await page.$$eval('.ss-modalfoot .ss-btn, .ss-outcome, .ss-decision, .ss-modalhead button, #ss-next, #ss-due', els => els.filter(e => { const r = e.getBoundingClientRect(); return r.height > 0 && (r.height < 44 || (e.matches('button') && r.width < 44)) }).map(e => (e.textContent.trim() || e.id || e.getAttribute('aria-label')).slice(0, 20) + ' ' + Math.round(e.getBoundingClientRect().width) + 'x' + Math.round(e.getBoundingClientRect().height)))
  ok(`MD-${label}. Important targets >=44px`, tgt.length === 0, tgt.join(', '))
  ok(`MD-${label}. No sideways scroll`, (await overflowX(page)) <= 0)
  await page.$eval('.ss-modalbody', e => { e.scrollTop = e.scrollHeight / 2 }); await sleep(100)
  await vshot(page, 'e2e-m-dialog-' + label)
  if (label !== '375x480-keyboard') {
    await clickText(page, '.ss-modalfoot button', 'Cancel'); await sleep(300)
    ok(`MD-${label}. Cancel closes (not trapped)`, !(await page.$('[role=dialog]')))
  } else {
    await page.click('.ss-modalfoot .ss-primaryact'); await sleep(1200)
    const got = (await api(repTok, '/sales/opportunities/' + mobileLead.id)).body
    const n = (got.timeline || []).find(e => e.event_type === 'call_outcome')
    ok('MD. Saved from the smallest screen: dialog closed, full note + follow-up stored', !(await page.$('[role=dialog]')) && n && (n.detail || '').length === LONG.length && got.next_action === 'Check back after budget season' && !!got.next_action_due_at,
      `${(n?.detail || '').length} chars, next ${got.next_action}, due ${got.next_action_due_at}`)
  }
}

// ── Do Not Contact through the UI ───────────────────────────────────────
await page.setViewport({ width: 1440, height: 900 })
const outboxBefore = outboxLines().length
const today = new Date().toISOString().slice(0, 10) + 'T12:00:00'
const optout = await mk({ company_name: 'Optout Co', contact_name: 'Testfamily Optout', phone: '(214) 555-0177', email: 'optout@example.test', stage: 'contacted', next_action: 'Call back', next_action_due_at: today })
const optdup = await mk({ company_name: 'Optout Dup Co', contact_name: 'Testfamily Optdup', phone: '214.555.0177', email: 'other@example.test', stage: 'prospect', next_action: 'Intro call', next_action_due_at: today })
await page.goto(APP + '/sales/leads/' + optout.id, { waitUntil: 'networkidle2' }); await sleep(700)
ok('DNC-0. Rep sees no manager "Lift" control', !(await bodyText(page)).includes('Lift (manager correction)'))
await clickText(page, '.ss-detail-actions button', 'Call'); await clickText(page, '.ss-outcome', 'Not Interested', true)
const choices = await page.$$eval('.ss-decision strong', s => s.map(x => x.textContent))
ok('DNC-1. Not Interested offers Close as Lost / Follow up later / Asked not to be contacted', choices.join('|') === 'Close as Lost|Follow up later|Asked not to be contacted', choices.join('|'))
await clickText(page, '.ss-decision', 'Asked not to be contacted')
await page.type('#ss-call-note', 'Please stop calling me.')
const lbl = await page.$eval('.ss-modalfoot .ss-primaryact', b => b.textContent.trim())
await page.click('.ss-modalfoot .ss-primaryact'); await sleep(1500)
const od = (await api(repTok, '/sales/opportunities/' + optout.id)).body
const dd = (await api(repTok, '/sales/opportunities/' + optdup.id)).body
ok('DNC-2. "Record Do Not Contact" saves a server record (who + reason)', lbl.includes('Record Do Not Contact') && od.do_not_contact_detail?.active?.reason === 'Please stop calling me.' && !!od.do_not_contact_detail.active.recorded_by, JSON.stringify(od.do_not_contact_detail?.active || {}))
ok('DNC-3. Distinct from Lost: deal stays open, follow-up cleared', od.stage !== 'lost' && od.status === 'open' && !od.next_action && !od.next_action_due_at)
ok('DNC-4. Duplicate with the same phone in another format is covered, follow-up cleared', dd.do_not_contact === true && !dd.next_action_due_at)
const lt = await bodyText(page)
const actBtns = await page.$$eval('.ss-detail-actions button', b => b.map(x => x.textContent.trim()))
ok('DNC-5. Lead shows the banner; no Call / Text / Book Demo / Log Call Outcome', lt.includes('Do Not Contact') && lt.includes('Please stop calling me.') && actBtns.length === 0 && !lt.includes('Log Call Outcome'), actBtns.join(','))
await shot(page, 'e2e-dnc-lead-rep')
await page.goto(APP + '/sales/follow-ups', { waitUntil: 'networkidle2' }); await sleep(800)
const fuNames = await rowsNames(page)
ok('DNC-6. Neither record is in Due Now', !fuNames.includes('Testfamily Optout') && !fuNames.includes('Testfamily Optdup'))
const pat = await api(repTok, '/sales/opportunities/' + optdup.id, { method: 'PATCH', body: JSON.stringify({ next_action: 'Try again', next_action_due_at: today }) })
const nte = await api(repTok, '/sales/opportunities/' + optdup.id + '/notes', { method: 'POST', body: JSON.stringify({ event_type: 'call_outcome', summary: 'Call — Reached' }) })
ok('DNC-7. Server refuses a new follow-up and a call log on the duplicate (409)', pat.status === 409 && nte.status === 409, pat.status + '/' + nte.status)
await page.goto(APP + '/sales/book?opp=' + optdup.id, { waitUntil: 'networkidle2' }); await sleep(800)
const bk = await bodyText(page)
const offered = await page.$$eval('select option', o => o.map(x => x.textContent))
ok('DNC-8. Book a Demo refuses and never offers either person', bk.includes("Can't book") && !offered.some(t => /Optout|Optdup/.test(t)))
await page.goto(APP + '/sales/pipeline', { waitUntil: 'networkidle2' }); await sleep(800)
ok('DNC-9. Pipeline cards carry a "Do not contact" chip', (await page.$$eval('.ss-dnc', e => e.length)) >= 2)
ok('DNC-10. Nothing was sent to either person', outboxLines().slice(outboxBefore).every(l => !/optout@|other@|555-?0177|5550177/.test(l)))
// manager correction
await logout(page); await login(page, 'manager@example.test')
await page.goto(APP + '/sales/leads/' + optout.id, { waitUntil: 'networkidle2' }); await sleep(700)
await clickText(page, '.ss-dncbanner button', 'Lift (manager correction)')
await page.click('.ss-dncform .ss-blue'); await sleep(400)
ok('DNC-11. Lift needs a reason', (await bodyText(page)).includes('Give the reason for lifting it.'))
await page.type('#ss-dnc-reason', 'Recorded on the wrong person.')
await shot(page, 'e2e-dnc-manager-lift')
await page.click('.ss-dncform .ss-blue'); await sleep(1200)
const od2 = (await api(mgrTok, '/sales/opportunities/' + optout.id)).body
const dd2 = (await api(mgrTok, '/sales/opportunities/' + optdup.id)).body
const h0 = od2.do_not_contact_detail?.history?.[0] || {}
ok('DNC-12. Lifted with reason; history keeps who recorded and who lifted', !od2.do_not_contact_detail?.active && h0.lift_reason === 'Recorded on the wrong person.' && !!h0.lifted_by && !!h0.recorded_by)
ok('DNC-13. Lifting does not reschedule anything', !od2.next_action_due_at && !dd2.next_action_due_at)
ok('DNC-14. Lead shows Call again and the history', (await page.$$eval('.ss-detail-actions button', b => b.some(x => x.textContent.includes('Call')))) && (await bodyText(page)).includes('Do Not Contact history'))
await page.setViewport({ width: 390, height: 844, hasTouch: true, isMobile: true }); await sleep(300)
await shot(page, 'e2e-m-dnc-lifted-manager')

console.log('blocked hosts:', [...blocked].join(', ') || 'none')
console.log('console errors:', errors.length ? errors.join('\n  ') : 'none')
await browser.close()
