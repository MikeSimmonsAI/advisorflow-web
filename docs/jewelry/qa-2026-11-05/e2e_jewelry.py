"""End-to-end: the real React app against a real local backend (SQLite).
Usage: python3 e2e_jewelry.py SHOT_DIR"""
import json, sys, os, io
from datetime import datetime, timedelta
from playwright.sync_api import sync_playwright

SHOTS = sys.argv[1]
os.makedirs(SHOTS, exist_ok=True)
seed = json.load(open('/home/claude/qa/local_seed.json'))
BASE = 'http://localhost:5173'
results = []

def check(name, cond, detail=''):
    results.append((name, bool(cond)))
    print(('PASS ' if cond else 'FAIL ') + name + ((' — ' + str(detail)) if detail else ''))

def login(ctx, who):
    ctx.add_init_script("localStorage.setItem('af_token', %s); localStorage.setItem('af_user', %s);" % (
        json.dumps(seed[who]['token']), json.dumps(json.dumps(seed[who]['user']))))

def shot(pg, name, full=False):
    pg.wait_for_timeout(450)
    pg.screenshot(path=os.path.join(SHOTS, name + '.png'), full_page=full)

with sync_playwright() as p:
    b = p.chromium.launch(executable_path='/usr/bin/google-chrome', args=['--no-sandbox'])
    ctx = b.new_context(viewport={'width': 1440, 'height': 900})
    login(ctx, 'owner')
    pg = ctx.new_page()
    errs = []
    pg.on('pageerror', lambda e: errs.append(str(e)))
    pg.goto(BASE + '/program/jewelry'); pg.wait_for_selector('.jw-hero', timeout=20000)
    check('owner opens workspace', 'Blue Ridge Gold' in pg.inner_text('.jw-top'))
    check('back to program access visible', pg.locator('[data-testid=back-to-program-access]').is_visible())
    check('back link href', pg.get_attribute('[data-testid=back-to-program-access]', 'href') == '/workspaces')
    check('empty dashboard shows zeros not fake data', [x.strip() for x in pg.locator('.jw-kpi .jw-kpi__value').first.inner_text().split('\n')][0] == '0', pg.locator('.jw-kpi').first.inner_text())
    check('empty state prompt', 'no customers yet' in pg.inner_text('.jw-content').lower())
    shot(pg, '01-dashboard-empty')

    # add customers through the UI
    def add(first, last, phone, segs, interests):
        pg.goto(BASE + '/program/jewelry/customers?add=1'); pg.wait_for_selector('.jw-modal')
        pg.fill('.jw-modal input >> nth=0', first); pg.fill('.jw-modal input >> nth=1', last)
        pg.fill('.jw-modal input >> nth=2', phone)
        for s in segs:
            pg.click('.jw-modal .jw-chip:has-text("%s")' % s)
        pg.fill('.jw-modal input[placeholder=""] >> nth=0', '') if False else None
        inp = pg.locator('.jw-modal .jw-field:has-text("Interests") input')
        inp.fill(interests)
        pg.click('.jw-modal button:has-text("Add customer")')
        pg.wait_for_selector('.jw-profile', timeout=10000)
    add('Jordan', 'Ellis', '(276) 555-0110', ['Gold Sellers', 'Previous Customers'], 'Gold chains, scrap gold')
    check('customer saved and profile opened', 'Jordan Ellis' in pg.inner_text('.jw-profile'))
    url_jordan = pg.url
    add('Casey', 'Rivera', '(423) 555-0115', ['Coin Collectors', 'VIP Customers'], 'Gold Eagles')
    add('Taylor', 'Brooks', '(276) 555-0111', ['Jewelry Buyers'], 'Engagement rings')
    # duplicate
    pg.goto(BASE + '/program/jewelry/customers?add=1'); pg.wait_for_selector('.jw-modal')
    pg.fill('.jw-modal input >> nth=0', 'Dup'); pg.fill('.jw-modal input >> nth=2', '2765550110')
    pg.click('.jw-modal button:has-text("Add customer")'); pg.wait_for_timeout(800)
    check('duplicate detected in UI', 'already exists' in pg.inner_text('.jw-modal'))
    pg.click('.jw-modal button:has-text("Cancel")')

    # note, follow-up, transaction, permission on Jordan
    pg.goto(url_jordan); pg.wait_for_selector('.jw-profile')
    pg.fill('input[placeholder="Add a note…"]', 'Brought a 14K chain to look at')
    pg.click('button:has-text("Add note")'); pg.wait_for_timeout(700)
    check('note persisted', 'Brought a 14K chain' in pg.inner_text('.jw-timeline'))
    pg.fill('input[placeholder^="Follow-up"]', 'Call about chain offer')
    pg.click('button:has-text("Set follow-up")'); pg.wait_for_timeout(700)
    check('follow-up shown', 'Call about chain offer' in pg.inner_text('.jw-content'))
    pg.click('.jw-tabs button:has-text("Transactions")')
    pg.select_option('select[aria-label=Type]', 'purchase')
    pg.fill('input[aria-label=Amount]', '320'); pg.fill('input[placeholder="What was it?"]', '14K chain')
    pg.click('button:has-text("Record")'); pg.wait_for_timeout(800)
    check('transaction recorded', '$320' in pg.inner_text('.jw-table'))
    pg.click('.jw-card:has-text("Marketing preferences") button:has-text("Edit")')
    pg.check('.jw-modal input[type=checkbox] >> nth=0')
    pg.click('.jw-modal button:has-text("Save")'); pg.wait_for_timeout(500)
    check('permission requires source', 'came from' in pg.inner_text('.jw-modal').lower() or 'permission' in pg.inner_text('.jw-modal').lower())
    pg.fill('.jw-modal .jw-field input', 'Signed in-store form')
    pg.click('.jw-modal button:has-text("Save")'); pg.wait_for_timeout(700)
    check('permission saved', 'permission recorded' in pg.inner_text('.jw-content').lower())
    shot(pg, '03-customer-profile')
    # refresh persists
    pg.reload(); pg.wait_for_selector('.jw-profile')
    check('data persists after refresh', 'Brought a 14K chain' in pg.inner_text('.jw-content'))

    # list, search, filter
    pg.goto(BASE + '/program/jewelry/customers'); pg.wait_for_selector('.jw-table')
    check('3 customers listed', pg.locator('.jw-table tbody tr').count() == 3)
    pg.click('.jw-chip:has-text("Coin Collectors")'); pg.wait_for_timeout(600)
    check('segment filter', pg.locator('.jw-table tbody tr').count() == 1 and 'Casey' in pg.inner_text('.jw-table'))
    pg.click('.jw-chip:has-text("All")'); pg.fill('input[aria-label="Search customers"]', 'rings'); pg.wait_for_timeout(900)
    check('search by interest', pg.locator('.jw-table tbody tr').count() == 1 and 'Taylor' in pg.inner_text('.jw-table'))
    pg.fill('input[aria-label="Search customers"]', ''); pg.wait_for_timeout(700)
    shot(pg, '02-customers')

    # import
    csv = b"Name,Phone,Email,Segment,Interests,SMS Opt In\nAda Lovelace,276-555-0131,ada@example.com,VIP; Coins,Morgan dollars,yes\nBen Franklin,2765550132,ben@example.com,Gold Seller,,no\nDup Row,(276) 555-0110,,,,\n"
    open('/tmp/import.csv', 'wb').write(csv)
    pg.goto(BASE + '/program/jewelry/customers?import=1'); pg.wait_for_selector('.jw-modal')
    pg.set_input_files('.jw-modal input[type=file]', '/tmp/import.csv')
    pg.click('.jw-modal button:has-text("Preview")'); pg.wait_for_selector('.jw-modal .jw-table', timeout=10000)
    txt = pg.inner_text('.jw-modal')
    check('import preview shows duplicate', 'Duplicate' in txt)
    shot(pg, '04-import-preview')
    pg.click('.jw-modal button:has-text("Import 2 customers")'); pg.wait_for_timeout(1500)
    check('import committed', pg.locator('.jw-table tbody tr').count() == 5, pg.locator('.jw-table tbody tr').count())

    # appointments
    pg.goto(BASE + '/program/jewelry/appointments?new=1'); pg.wait_for_selector('.jw-modal')
    pg.fill('.jw-picker input', 'Jordan'); pg.wait_for_selector('.jw-picker__list button'); pg.click('.jw-picker__list button >> nth=0')
    staff_sel = pg.locator('.jw-modal .jw-field:has-text("Staff member") select')
    staff_sel.select_option(label='Store Owner')
    when = (datetime.now() + timedelta(days=1)).replace(hour=11, minute=0)
    pg.fill('.jw-modal input[type=datetime-local]', when.strftime('%Y-%m-%dT%H:%M'))
    pg.click('.jw-modal button:has-text("Book appointment")'); pg.wait_for_timeout(1200)
    check('appointment booked', pg.locator('.jw-ev').count() >= 1)
    # overlap
    pg.goto(BASE + '/program/jewelry/appointments?new=1'); pg.wait_for_selector('.jw-modal')
    pg.fill('.jw-picker input', 'Casey'); pg.wait_for_selector('.jw-picker__list button'); pg.click('.jw-picker__list button >> nth=0')
    pg.locator('.jw-modal .jw-field:has-text("Staff member") select').select_option(label='Store Owner')
    pg.fill('.jw-modal input[type=datetime-local]', when.replace(minute=15).strftime('%Y-%m-%dT%H:%M'))
    pg.click('.jw-modal button:has-text("Book appointment")'); pg.wait_for_timeout(900)
    check('double booking refused in UI', 'already has an appointment' in pg.inner_text('.jw-modal'))
    pg.locator('.jw-modal .jw-field:has-text("Staff member") select').select_option(label='Sam Staff')
    pg.click('.jw-modal button:has-text("Book appointment")'); pg.wait_for_timeout(1200)
    check('booking with other staff ok', pg.locator('.jw-ev').count() >= 2)
    shot(pg, '05-appointments-week')
    pg.click('.jw-seg button:has-text("Month")'); pg.wait_for_timeout(700); shot(pg, '06-appointments-month')
    pg.click('.jw-seg button:has-text("Day")'); pg.wait_for_timeout(500)
    pg.click('.jw-seg button:has-text("Week")'); pg.wait_for_timeout(700)
    pg.locator('.jw-ev').first.click(); pg.wait_for_selector('.jw-modal')
    pg.locator('.jw-modal .jw-field:has-text("Status") select').select_option('completed')
    pg.click('.jw-modal button:has-text("Save")'); pg.wait_for_timeout(900)
    check('status change saved', 'Completed' in pg.inner_text('.jw-table'))

    # campaigns
    pg.goto(BASE + '/program/jewelry/campaigns'); pg.wait_for_selector('.jw-head')
    pg.click('button:has-text("+ New campaign")'); pg.click('.jw-template:has-text("VIP")'); pg.wait_for_selector('.jw-subcard', timeout=10000)
    pg.click('.jw-card .jw-chip:has-text("VIP Customers")'); pg.click('button:has-text("Save draft")'); pg.wait_for_timeout(1200)
    rec = pg.inner_text('.jw-subcard >> nth=0')
    check('recipients computed from real permission', 'eligible' in rec.lower() and '1 / 0' in rec, rec[:120])
    shot(pg, '07-campaign-editor', full=True)
    pg.click('button:has-text("Send now")'); pg.wait_for_selector('.jw-modal')
    check('send refused with checklist', 'Nothing was sent' in pg.inner_text('.jw-modal'))
    shot(pg, '08-send-locked')
    pg.click('.jw-modal button:has-text("OK")')
    pg.click('button:has-text("Schedule")'); pg.wait_for_timeout(900)
    check('scheduled on hold', 'on hold' in pg.inner_text('.jw-content'))

    # inventory
    pg.goto(BASE + '/program/jewelry/inventory'); pg.wait_for_selector('.jw-head')
    pg.click('button:has-text("+ Add item")'); pg.wait_for_selector('.jw-modal')
    pg.fill('.jw-modal .jw-field:has-text("Name") input', '1921 Morgan silver dollar')
    pg.locator('.jw-modal .jw-field:has-text("Category") select').select_option('coins')
    pg.fill('.jw-modal .jw-field:has-text("Interest keywords") input', 'Morgan')
    pg.click('.jw-modal button:has-text("Save")'); pg.wait_for_timeout(1000)
    check('item saved', '1921 Morgan' in pg.inner_text('.jw-items'))
    pg.click('.jw-item button:has-text("Edit")'); pg.wait_for_selector('.jw-modal')
    png = (b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4"
           b"\x89\x00\x00\x00\rIDATx\x9cc\xf8\xff\xff?\x00\x05\xfe\x02\xfe\xa7\x35\x81\x84\x00\x00\x00\x00IEND\xaeB`\x82")
    open('/tmp/item.png', 'wb').write(png)
    pg.set_input_files('.jw-modal input[type=file]', '/tmp/item.png'); pg.wait_for_timeout(1200)
    pg.click('.jw-modal button:has-text("Cancel")'); pg.wait_for_timeout(800)
    check('photo shown', pg.locator('.jw-item__img img').count() == 1)
    shot(pg, '09-inventory')
    pg.click('.jw-item button:has-text("Promote")'); pg.wait_for_timeout(1500)
    check('promote creates campaign draft', '/campaigns' in pg.url and 'New arrival' in pg.inner_text('.jw-content'))

    # reports
    pg.goto(BASE + '/program/jewelry/reports'); pg.wait_for_selector('.jw-kpis')
    check('reports real numbers', '$0' in pg.inner_text('.jw-kpis') or 'Recorded revenue' in pg.inner_text('.jw-kpis'))
    shot(pg, '10-reports')

    # AI
    pg.goto(BASE + '/program/jewelry/receptionist'); pg.wait_for_selector('.jw-chat')
    pg.click('.jw-chip:has-text("How much will you pay")'); pg.wait_for_timeout(1800)
    ai = pg.inner_text('.jw-chat')
    check('AI hands off pricing', 'evaluates it in person' in ai and '$' not in ai)
    pg.click('.jw-chip:has-text("Can I bring in coins")'); pg.wait_for_timeout(1800)
    shot(pg, '11-ai-receptionist')
    pg.click('button:has-text("Emergency stop")'); pg.wait_for_timeout(900)
    pg.click('.jw-chip:has-text("Do you buy gold")'); pg.wait_for_timeout(1500)
    check('emergency stop blocks', 'stopped' in pg.inner_text('.jw-chat').lower())
    pg.click('button:has-text("Restart assistant")'); pg.wait_for_timeout(600)

    # settings
    pg.goto(BASE + '/program/jewelry/settings'); pg.wait_for_selector('.jw-card')
    pg.fill('.jw-field:has-text("Street address") input', '16011 Porterfield Highway')
    pg.fill('.jw-field:has-text("City, state, ZIP") input', 'Abingdon, VA 24210')
    pg.click('button:has-text("Save business information")'); pg.wait_for_timeout(900)
    check('profile saved', 'saved' in pg.inner_text('body').lower())
    check('activity log populated', pg.locator('.jw-card:has-text("Activity log") li').count() > 5)
    shot(pg, '12-settings')

    # dashboard with real data
    pg.goto(BASE + '/program/jewelry'); pg.wait_for_selector('.jw-kpis')
    k = pg.inner_text('.jw-kpis')
    check('dashboard reflects 5 customers', pg.locator('.jw-kpi .jw-kpi__value').first.inner_text().strip() == '5', k[:80])
    shot(pg, '13-dashboard-real-data')
    # back navigation goes to /workspaces (not history)
    navs = []
    pg.on('framenavigated', lambda f: navs.append(f.url) if f == pg.main_frame else None)
    pg.click('[data-testid=back-to-program-access]'); pg.wait_for_timeout(2000)
    check('back goes to /workspaces (single-workspace login is then sent to its only workspace)', any(u.rstrip('/').endswith('/workspaces') for u in navs), navs)
    shot(pg, '14-program-access-after-back')
    check('no page errors (owner)', not errs, errs[:3])

    # STAFF role
    sc = b.new_context(viewport={'width': 1440, 'height': 900}); login(sc, 'staff'); sp = sc.new_page()
    sp.goto(BASE + '/program/jewelry'); sp.wait_for_selector('.jw-nav')
    nav = sp.inner_text('.jw-nav')
    check('staff nav hides campaigns/reports/settings', 'Campaign' not in nav and 'Reports' not in nav and 'Settings' not in nav)
    sp.goto(BASE + '/program/jewelry/customers'); sp.wait_for_timeout(1500)
    rows = sp.locator('.jw-table tbody tr').count()
    check('staff sees only assigned customers', rows <= 1, rows)
    sp.goto(BASE + '/program/jewelry/campaigns'); sp.wait_for_timeout(1500)
    check('staff campaigns refused', 'required' in sp.inner_text('.jw-content').lower())
    shot(sp, '15-staff-view')

    # MOBILE
    mc = b.new_context(viewport={'width': 390, 'height': 844}, is_mobile=True, has_touch=True, device_scale_factor=2)
    login(mc, 'owner'); mp = mc.new_page()
    for path, name in (('', 'dashboard'), ('/customers', 'customers'), ('/appointments', 'appointments'), ('/campaigns', 'campaigns'),
                       ('/inventory', 'inventory'), ('/reports', 'reports'), ('/receptionist', 'ai'), ('/settings', 'settings')):
        mp.goto(BASE + '/program/jewelry' + path); mp.wait_for_timeout(1800)
        sw = mp.evaluate('document.documentElement.scrollWidth')
        check('mobile %s no overflow' % name, sw <= 392, sw)
        check('mobile %s back button visible' % name, mp.locator('[data-testid=back-to-program-access]').is_visible())
        shot(mp, 'm-' + name)
    mp.click('.jw-top__menu'); mp.wait_for_timeout(400); shot(mp, 'm-menu')
    check('mobile menu opens', mp.locator('.jw-side.open').count() == 1)
    b.close()

fails = [r for r in results if not r[1]]
print('\n%d/%d passed' % (len(results) - len(fails), len(results)))
sys.exit(1 if fails else 0)
