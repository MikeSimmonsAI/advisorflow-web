// "Before" captures: the CURRENT production sales screens (unmodified main),
// served locally on :4174 against the same local synthetic backend.
import pp from 'puppeteer-core'
import fs from 'node:fs'
// Browser: SW_CHROME=<path to a headless-shell build> (recommended - see README),
// otherwise @sparticuz/chromium.
async function launchOpts() {
  if (process.env.SW_CHROME) return { executablePath: process.env.SW_CHROME, args: ['--no-sandbox', '--disable-dev-shm-usage'], headless: true }
  const chromium = (await import('@sparticuz/chromium')).default
  return { executablePath: await chromium.executablePath(), args: chromium.args, headless: true }
}
const OUT = process.argv[2]; const APP = process.env.SW_APP_BEFORE || 'http://localhost:5174'; const API = 'http://127.0.0.1:8000'
fs.mkdirSync(OUT, { recursive: true })
const tok = fs.readFileSync(process.argv[3], 'utf8').trim()
const all = (await (await fetch(API + '/sales/opportunities', { headers: { Authorization: 'Bearer ' + tok } })).json()).stages.flatMap(s => s.opportunities)
const lead = all.find(o => o.contact_name === 'Testfamily Alvarez')
const b = await pp.launch(await launchOpts())
const page = await b.newPage(); await page.setViewport({ width: 1440, height: 900 })
await page.setRequestInterception(true)
page.on('request', r => { const u = r.url(); const h = u.startsWith('http') ? new URL(u).hostname : ''; (!u.startsWith('http') || ['127.0.0.1', 'localhost'].includes(h)) ? r.continue() : r.abort() })
await page.goto(APP + '/login', { waitUntil: 'networkidle2' })
await page.type('input[type=email], input[name=email], input[autocomplete=username]', 'rep@example.test')
await page.type('input[type=password]', 'LocalTest!1')
await Promise.all([page.waitForNavigation({ waitUntil: 'networkidle2' }).catch(() => {}), page.keyboard.press('Enter')])
await new Promise(r => setTimeout(r, 1500))
for (const [n, p] of [['before-my_day', '/sales'], ['before-prospects', '/sales/prospects'], ['before-opportunity', '/sales/opportunities/' + lead.id],
                      ['before-pipeline', '/sales/pipeline'], ['before-calendar', '/sales/calendar']]) {
  await page.goto(APP + p, { waitUntil: 'networkidle2' }); await new Promise(r => setTimeout(r, 900))
  await page.screenshot({ path: `${OUT}/${n}.png`, fullPage: true }); console.log('captured', n, page.url())
}
await b.close()
