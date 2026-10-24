import { chromium, devices } from 'playwright';
import fs from 'fs';
const priv = process.env.PRIV, base = 'http://127.0.0.1:8088';
const clearRate = () => { for (const f of fs.readdirSync(priv)) if (f.startsWith('rate-')) fs.unlinkSync(priv + '/' + f); };
const results = [];
const browser = await chromium.launch({ executablePath: '/opt/pw-browsers/chromium-1194/chrome-linux/chrome' });
for (const [name, opts] of [['desktop', { viewport: { width: 1366, height: 900 } }], ['mobile', devices['iPhone 13']]]) {
  const ctx = await browser.newContext(opts); const page = await ctx.newPage();
  const errors = []; page.on('pageerror', e => errors.push(String(e)));
  let posts = 0; page.on('request', r => { if (r.url().endsWith('intake.php') && r.method() === 'POST') posts++; });
  await page.goto(base + '/buyers.html');
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
  // empty submit: browser validation, nothing posted
  await page.click('#buyer-form button[type=submit]'); await page.waitForTimeout(300);
  const postsAfterEmpty = posts;
  let navOk = true;
  if (name === 'mobile') {
    const toggle = page.locator('#site-nav button').first();
    navOk = (await toggle.count()) > 0 && await toggle.isVisible();
    if (navOk) { await toggle.click(); await page.waitForTimeout(300); navOk = await page.locator('#site-nav a[href="sell.html"]').first().isVisible(); await toggle.click(); }
  }
  clearRate();
  await page.fill('#full_name', 'Test Buyer ' + name); await page.fill('#company', 'Fiction Capital');
  await page.fill('#email', `buyer-${name}@example.com`); await page.fill('#phone', '(817) 555-01' + (name === 'desktop' ? '20' : '21'));
  await page.fill('#markets', 'Tarrant County, Fort Worth'); await page.selectOption('#strategy', 'Fix & Flip');
  await page.selectOption('#budget', '$100,000–$250,000'); await page.fill('#notes', 'SFR, 3/2, light rehab. SYNTHETIC TEST.');
  await page.check('input[name=contact_permission]');
  await page.click('#buyer-form button[type=submit]');
  const status = page.locator('#buyer-form [role=status]');
  await status.filter({ hasText: /Reference|problem|wait|Please/ }).waitFor({ timeout: 15000 });
  const msg = await status.textContent(); const cls = await status.getAttribute('class');
  await page.screenshot({ path: `${process.env.OUT}/buyers-${name}.png`, fullPage: false });
  // sell page: seller CTA goes to the existing seller intake
  await page.goto(base + '/sell.html');
  const sellLinks = await page.$$eval('a', as => as.map(a => a.href).filter(h => h.includes('evosyspro.live/sell')).length);
  const sellOverflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
  results.push({ name, overflow, sellOverflow, postsAfterEmpty, navOk, msg, cls, sellLinks, errors });
  await ctx.close();
}
await browser.close();
console.log(JSON.stringify(results, null, 1));
