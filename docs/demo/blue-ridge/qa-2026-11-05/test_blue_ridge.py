"""Browser QA for the Blue Ridge demo. Usage: python3 test_blue_ridge.py BASE_URL SHOT_DIR"""
import sys, re, json, os
from playwright.sync_api import sync_playwright

BASE = sys.argv[1].rstrip('/')
SHOTS = sys.argv[2] if len(sys.argv) > 2 else None
URL = BASE + '/demo/blue-ridge/'
results = []

def check(name, cond, detail=''):
    results.append((name, bool(cond), detail))
    print(('PASS ' if cond else 'FAIL ') + name + ((' — ' + str(detail)) if detail else ''))

def shot(page, name):
    if SHOTS:
        os.makedirs(SHOTS, exist_ok=True)
        page.wait_for_timeout(350)
        page.screenshot(path=os.path.join(SHOTS, name + '.png'), full_page=False)

with sync_playwright() as p:
    b = p.chromium.launch(executable_path='/usr/bin/google-chrome', args=['--no-sandbox'])
    ctx = b.new_context(viewport={'width': 1440, 'height': 900})  # fresh, no cookies/session
    page = ctx.new_page()
    errors, requests = [], []
    page.on('pageerror', lambda e: errors.append(str(e)))
    page.on('console', lambda m: errors.append('console:' + m.text) if m.type == 'error' and 'fonts.g' not in m.text else None)
    page.on('request', lambda r: requests.append(r.url))
    resp = page.goto(URL, wait_until='domcontentloaded')
    page.wait_for_timeout(600)
    check('page loads 200', resp.status == 200, resp.status)
    check('title', 'Blue Ridge Gold' in page.title(), page.title())
    check('demo ribbon', 'INTERACTIVE DEMO — SAMPLE DATA' in page.inner_text('.ribbon'))
    check('powered by EvoSys', 'EvoSys' in page.inner_text('.powered'))
    check('no login form', page.locator('input[type=password]').count() == 0)
    check('url stayed on demo (no login redirect)', page.url.startswith(URL), page.url)
    shot(page, '01-dashboard')

    # dashboard period
    v30 = page.inner_text('#kpiGrid')
    page.click('#periodCtl button:has-text("12 months")'); page.wait_for_timeout(200)
    v12 = page.inner_text('#kpiGrid')
    check('period switch changes KPIs', v30 != v12)
    page.click('#periodCtl button:has-text("7 days")'); page.wait_for_timeout(200)
    check('period 7d chart renders bars', page.locator('#salesChart rect').count() == 14)

    # every nav tab
    for sid in ['dashboard','customers','campaigns','appointments','ai','inventory','reports','how','roadmap']:
        page.click(f'.nav button[data-go="{sid}"]'); page.wait_for_timeout(150)
        ok = page.locator(f'#v-{sid}.active').count() == 1 and page.url.endswith('#' + sid)
        check(f'nav → {sid}', ok, page.url)
        check(f'banner visible on {sid}', page.locator('.ribbon').is_visible())
        shot(page, f'02-{sid}')

    # customers
    page.click('.nav button[data-go="customers"]')
    page.fill('#custSearch', 'Morgan'); page.wait_for_timeout(150)
    n = page.locator('#custTable tbody tr.click').count()
    check('search "Morgan" filters', n == 2, n)  # Alex Morgan, Morgan Hayes
    page.fill('#custSearch', ''); page.dispatch_event('#custSearch', 'input')
    page.click('#segChips button:has-text("Coin collectors")'); page.wait_for_timeout(150)
    names = page.locator('#custTable tbody tr.click').all_inner_texts()
    check('segment filter coins', len(names) == 6, len(names))
    page.click('#segChips button:has-text("Inactive customers")'); page.wait_for_timeout(100)
    check('segment filter inactive', page.locator('#custTable tbody tr.click').count() == 5)
    page.click('#segChips button:has-text("All customers")')
    page.click('#custTable tbody tr.click >> nth=0'); page.wait_for_timeout(350)
    check('profile drawer opens', page.locator('#drawer.show').count() == 1)
    check('deep link hash for profile', re.search(r'#customers/c\d+', page.url) is not None, page.url)
    check('transaction history shown', page.locator('#drawer .timeline li').count() >= 1)
    page.click('#drawer .tabs button:has-text("Communication")'); page.wait_for_timeout(100)
    check('communication history shown', 'Sent' in page.inner_text('#drawer') or 'Compliance' in page.inner_text('#drawer'))
    check('marketing preferences shown', 'Marketing preferences' in page.inner_text('#drawer'))
    page.select_option('#fuStatus', 'Contacted'); page.wait_for_timeout(150)
    check('follow-up status change applied', 'Contacted' in page.inner_text('#custTable tbody tr.click >> nth=0'))
    shot(page, '03-customer-profile')
    page.click('#drawer .x'); page.wait_for_timeout(300)
    check('drawer closes', page.locator('#drawer.show').count() == 0)
    page.click('button:has-text("+ Add customer")'); page.wait_for_timeout(150)
    page.fill('#nName', 'Sam Example'); page.fill('#nInt', 'Gold chains')
    page.click('button:has-text("Add to sample vault")'); page.wait_for_timeout(400)
    check('added customer opens profile', 'Sam Example' in page.inner_text('#drawer'))
    page.keyboard.press('Escape'); page.wait_for_timeout(300)
    check('added customer in list', 'Sam Example' in page.inner_text('#custTable'))
    check('total customers incremented', '2,488' in page.inner_text('#segChips'))

    # campaigns
    page.click('.nav button[data-go="campaigns"]')
    check('5 campaigns listed', page.locator('#campList .camp').count() == 5)
    for nm in ['We Buy Gold Weekend','Previous Customer Reactivation','Jewelry Holiday Specials','Coin Collector Alerts','VIP Customer Offers']:
        check('campaign present: ' + nm, nm in page.inner_text('#campList'))
    page.click('#campList .camp:has-text("Previous Customer Reactivation")'); page.wait_for_timeout(150)
    aud1 = page.inner_text('#campEditor .aud .elig b')
    page.click('#campEditor .chips button:has-text("VIP customers")'); page.wait_for_timeout(150)
    aud2 = page.inner_text('#campEditor .aud .elig b')
    check('segment choice changes audience', aud1 != aud2, f'{aud1} -> {aud2}')
    page.fill('#cSms', 'Hi {first}, test copy for the demo.'); page.wait_for_timeout(100)
    check('SMS preview reflects edit', 'test copy for the demo' in page.inner_text('#campPreview') and 'Reply STOP' in page.inner_text('#campPreview'))
    page.click('#campEditor .seg-ctl button:has-text("Email")'); page.wait_for_timeout(100)
    check('email preview', 'Subject:' in page.inner_text('#campPreview'))
    check('send button labeled simulation', 'Simulate' in page.inner_text('#sendBtn'))
    runs_before = page.evaluate('S.runs.length'); appts_before = page.evaluate('S.appts.length')
    page.click('#sendBtn'); page.wait_for_selector('text=SIMULATION COMPLETE', timeout=8000)
    check('simulation results shown', 'APPOINTMENTS' in page.inner_text('#campResult').upper())
    check('responses shown', page.locator('#campResult .reply').count() >= 3)
    check('run added to reports', page.evaluate('S.runs.length') == runs_before + 1)
    check('appointments added from sim', page.evaluate('S.appts.length') > appts_before)
    shot(page, '04-campaign-simulated')
    page.click('#campList .camp:has-text("VIP Customer Offers")'); page.wait_for_timeout(150)
    page.click('button:has-text("Schedule — Simulation")'); page.wait_for_timeout(150)
    check('schedule campaign', 'Scheduled' in page.inner_text('#campList .camp:has-text("VIP Customer Offers")'))

    # appointments
    page.click('.nav button[data-go="appointments"]')
    page.select_option('#apType', 'coins'); page.select_option('#apCust', 'c03')
    d = page.evaluate("iso(addDays(3))"); page.fill('#apDate', d); page.select_option('#apTime', '16:30')
    before = page.evaluate('S.appts.length')
    page.click('button:has-text("Book appointment — Simulation")'); page.wait_for_timeout(200)
    check('appointment booked', page.evaluate('S.appts.length') == before + 1)
    check('booked appt in list', 'Alex Morgan' in page.inner_text('#apptTable') and '4:30 PM' in page.inner_text('#apptTable'))
    page.click('button:has-text("Book appointment — Simulation")'); page.wait_for_timeout(200)
    check('double-booking blocked', page.evaluate('S.appts.length') == before + 1)
    sel = page.locator('#apptTable select').first
    sel.select_option('Confirmed'); page.wait_for_timeout(150)
    check('status change works', page.evaluate("S.appts.some(a=>a.status==='Confirmed')"))
    check('week calendar has 7 days', page.locator('#weekGrid .day').count() == 7)
    shot(page, '05-appointments')

    # AI
    page.click('.nav button[data-go="ai"]')
    for q in ['Do you buy gold jewelry?', 'Can I bring in coins?', 'How do I schedule an evaluation?', 'Are you open seven days a week?', 'How much will you pay for my ring?']:
        page.click(f'#aiChips button:has-text("{q}")'); page.wait_for_timeout(950)
    chat = page.inner_text('#chat')
    check('AI answers', chat.count('BLUE RIDGE ASSISTANT') >= 6)
    check('AI no price quote', not re.search(r'\$\d', chat))
    check('AI captures interests', 'Gold seller' in page.inner_text('#captureCard') and 'Coin collector' in page.inner_text('#captureCard'))
    check('AI recommends human follow-up', 'flagged for staff' in page.inner_text('#captureCard'))
    check('AI marked simulation', 'SIMULATION' in page.inner_text('#v-ai').upper())
    shot(page, '06-ai')
    page.click('button:has-text("Offer appointment")'); page.wait_for_timeout(900)
    check('AI → appointment handoff', page.url.endswith('#appointments'))

    # inventory
    page.click('.nav button[data-go="inventory"]')
    check('inventory categories', page.locator('#catGrid .cat').count() == 6)
    page.click('#itemGrid .toggle >> nth=3'); page.wait_for_timeout(100)
    check('feature toggle', page.evaluate("S.featured.i4") is True)
    page.click('#itemGrid button:has-text("Notify") >> nth=2'); page.wait_for_timeout(200)
    check('inventory targeting opens campaign', page.url.endswith('#campaigns') and 'Morgan silver dollar' in page.input_value('#cSms'))

    # reports consistency
    page.click('.nav button[data-go="reports"]')
    tot = page.evaluate("""(()=>{const R=S.runs;const s=k=>R.reduce((a,r)=>a+r[k],0);return {rev:s('revenue'),cost:s('cost'),reach:s('reached'),rep:s('replies'),ap:s('appts'),vi:s('visits'),sa:s('sales'),ok:R.every(r=>r.replies<=r.reached&&r.appts<=r.replies&&r.sales<=r.visits&&Math.abs(r.roi-(r.revenue-r.cost)/r.cost)<1e-9)}})()""")
    tiles = page.inner_text('#reportTiles')
    roi = (tot['rev'] - tot['cost']) / tot['cost']
    check('report ROI tile matches totals', f'{roi:.1f}×' in tiles, f'{roi:.1f}')
    check('report revenue tile matches', '${:,}'.format(tot['rev']) in tiles)
    check('per-run math consistent', tot['ok'])
    check('report labeled illustrative', 'ILLUSTRATIVE' in page.inner_text('#v-reports').upper())
    for lab in ['Campaign reach','Replies','Appointments','Store visits','Reactivated','Recorded sales','Attributed revenue','Promotional cost','Estimated ROI']:
        check('report tile: ' + lab, lab in tiles)
    shot(page, '07-reports')

    # tour
    page.click('.topbar button:has-text("TOUR")'); page.wait_for_timeout(300)
    check('tour opens step 1', 'STEP 1 OF 6' in page.inner_text('#tour') and page.url.endswith('#dashboard'))
    expected = ['customers', 'campaigns', 'ai', 'appointments', 'reports']
    for i, e in enumerate(expected):
        page.click('#tourNext'); page.wait_for_timeout(250)
        check(f'tour step {i+2} → {e}', page.url.endswith('#' + e) and f'STEP {i+2} OF 6' in page.inner_text('#tour'))
    shot(page, '08-tour-step6')
    page.click('#tourBack'); page.wait_for_timeout(200)
    check('tour back', page.url.endswith('#appointments'))
    page.click('#tour button:has-text("Exit")'); page.wait_for_timeout(200)
    check('tour exit', not page.locator('#tour').is_visible())
    page.click('.topbar button:has-text("TOUR")'); page.wait_for_timeout(200)
    page.click('#tour button:has-text("Skip tour")'); page.wait_for_timeout(200)
    check('tour skip', not page.locator('#tour').is_visible() and page.url.endswith('#dashboard'))
    page.click('.topbar button:has-text("TOUR")'); page.wait_for_timeout(200)
    for _ in range(6): page.click('#tourNext'); page.wait_for_timeout(150)
    check('tour finish closes', not page.locator('#tour').is_visible())

    # reset
    page.click('button:has-text("Reset Demo")'); page.wait_for_timeout(300)
    st = page.evaluate("({c:S.customers.length,r:S.runs.length,a:S.appts.length,added:S.added,camp:S.campaigns.find(c=>c.id==='vip').status})")
    check('reset restores sample data', st == {'c': 20, 'r': 3, 'a': 9, 'added': 0, 'camp': 'Draft'}, st)
    check('no localStorage/sessionStorage used', page.evaluate('localStorage.length + sessionStorage.length') == 0)
    check('no cookies set', len(ctx.cookies()) == 0)

    # deep link + refresh
    page.goto(URL + '#customers/c05'); page.wait_for_timeout(500)
    check('deep link opens Casey Rivera profile', 'Casey Rivera' in page.inner_text('#drawer') and page.locator('#drawer.show').count() == 1)
    page.reload(); page.wait_for_timeout(500)
    check('refresh keeps deep link', 'Casey Rivera' in page.inner_text('#drawer'))
    page.goto(URL + '#reports'); page.reload(); page.wait_for_timeout(400)
    check('refresh on #reports', page.locator('#v-reports.active').count() == 1)

    other = [u for u in requests if not u.startswith(BASE) and 'fonts.g' not in u]
    own = [u for u in requests if u.startswith(BASE) and '/demo/blue-ridge' not in u]
    check('no third-party/API requests', not other, other[:5])
    check('no other same-origin requests (no API calls)', not own, own[:5])
    check('no JS errors', not errors, errors[:5])

    # mobile
    m = b.new_context(viewport={'width': 390, 'height': 844}, is_mobile=True, has_touch=True, device_scale_factor=2)
    mp = m.new_page(); merr = []
    mp.on('pageerror', lambda e: merr.append(str(e)))
    mp.goto(URL); mp.wait_for_timeout(600)
    sw = mp.evaluate('document.documentElement.scrollWidth')
    check('mobile no horizontal overflow', sw <= 392, sw)
    shot(mp, '10-mobile-dashboard')
    mp.click('.menu-btn'); mp.wait_for_timeout(300)
    check('mobile menu opens', mp.locator('#sidebar.open').count() == 1)
    shot(mp, '11-mobile-menu')
    mp.click('.nav button[data-go="campaigns"]'); mp.wait_for_timeout(300)
    check('mobile nav works + menu closes', mp.url.endswith('#campaigns') and mp.locator('#sidebar.open').count() == 0)
    for sid in ['customers','campaigns','appointments','ai','inventory','reports','how','roadmap']:
        mp.goto(URL + '#' + sid); mp.wait_for_timeout(250)
        sw = mp.evaluate('document.documentElement.scrollWidth')
        check(f'mobile {sid} no page overflow', sw <= 392, sw)
        shot(mp, f'12-mobile-{sid}')
    mp.goto(URL + '#campaigns'); mp.wait_for_timeout(200)
    mp.click('#sendBtn'); mp.wait_for_selector('text=SIMULATION COMPLETE', timeout=8000)
    check('mobile simulate send', True)
    mp.click('.topbar button:has-text("Tour")'); mp.wait_for_timeout(400)
    check('mobile tour visible', mp.locator('#tour').is_visible())
    shot(mp, '13-mobile-tour')
    check('mobile no JS errors', not merr, merr[:3])
    b.close()

fails = [r for r in results if not r[1]]
print(f'\n{len(results) - len(fails)}/{len(results)} passed')
sys.exit(1 if fails else 0)
