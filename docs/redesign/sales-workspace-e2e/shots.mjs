// Lane F screenshots against the LOCAL app only. Any request to a host other
// than 127.0.0.1/localhost is aborted and reported.
import chromium from '@sparticuz/chromium'
import pp from 'puppeteer-core'
import fs from 'node:fs'

const OUT = process.argv[2]
const APP = 'http://127.0.0.1:4173'
const API = 'http://127.0.0.1:8000'
fs.mkdirSync(OUT, { recursive: true })

const tok = fs.readFileSync(process.argv[3], 'utf8').trim()
const opps = await (await fetch(API + '/sales/opportunities', { headers: { Authorization: 'Bearer ' + tok } })).json()
const all = opps.stages.flatMap(s => s.opportunities)
const lead = all.find(o => o.contact_name === 'Testfamily Alvarez')

const browser = await pp.launch({ executablePath: await chromium.executablePath(), args: chromium.args, headless: true })
const blocked = new Set(); const errors = []
const page = await browser.newPage()
await page.setRequestInterception(true)
page.on('request', r => {
  const h = new URL(r.url()).hostname
  if (['127.0.0.1', 'localhost'].includes(h) || r.url().startsWith('data:')) r.continue()
  else { blocked.add(h); r.abort() }
})
page.on('console', m => { if (m.type() === 'error' && !/Failed to load resource/.test(m.text())) errors.push(m.text()) })
page.on('pageerror', e => errors.push('PAGEERROR ' + e.message))

await page.setViewport({ width: 1440, height: 900 })
await page.goto(APP + '/login', { waitUntil: 'networkidle2' })
await page.type('input[type=email], input[name=email], input[autocomplete=username]', 'rep@example.test')
await page.type('input[type=password]', 'LocalTest!1')
await Promise.all([page.waitForNavigation({ waitUntil: 'networkidle2' }).catch(() => {}), page.keyboard.press('Enter')])
await new Promise(r => setTimeout(r, 1500))
console.log('after login:', page.url())

const screens = [
  ['my_day', '/sales'],
  ['follow_ups', '/sales/follow-ups'],
  ['lead_details', '/sales/leads/' + lead.id],
  ['book_a_demo', '/sales/book?opp=' + lead.id],
  ['pipeline', '/sales/pipeline'],
  ['appointments', '/sales/appointments'],
  ['reports', '/sales/my-results'],
]
const sizes = [['desktop', 1440, 900], ['tablet', 820, 1180], ['mobile', 390, 844]]
const report = []
for (const [sz, w, h] of sizes) {
  await page.setViewport({ width: w, height: h })
  for (const [name, path] of screens) {
    if (sz !== 'desktop' && name === 'reports') continue
    await page.goto(APP + path, { waitUntil: 'networkidle2' })
    await new Promise(r => setTimeout(r, 700))
    const over = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)
    await page.screenshot({ path: `${OUT}/${name}-${sz}.png`, fullPage: true })
    report.push(`${sz.padEnd(7)} ${name.padEnd(13)} overflow=${over}px`)
  }
}
console.log(report.join('\n'))
console.log('blocked hosts:', [...blocked].join(', ') || 'none')
console.log('console errors:', errors.length ? errors.join('\n  ') : 'none')
await browser.close()
