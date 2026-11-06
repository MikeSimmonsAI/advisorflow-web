import json, sys
from playwright.sync_api import sync_playwright
seed = json.load(open('/home/claude/qa/local_seed.json')); BASE = sys.argv[1]
with sync_playwright() as p:
    b = p.chromium.launch(executable_path='/usr/bin/google-chrome', args=['--no-sandbox'])
    for w, mob in ((1440, False), (390, True)):
        c = b.new_context(viewport={'width': w, 'height': 900}, is_mobile=mob)
        c.add_init_script("localStorage.setItem('af_token', %s); localStorage.setItem('af_user', %s);" % (json.dumps(seed['platform']['token']), json.dumps(json.dumps(seed['platform']['user']))))
        pg = c.new_page(); pg.goto(BASE + '/program/jewelry/settings'); pg.wait_for_selector('#jw-hours-tue')
        before = {k: pg.input_value('#jw-hours-' + k) for k in ('mon','tue','wed','thu','fri','sat','sun')}
        new_tue = '9:30am–5:30pm' if before['tue'] != '9:30am–5:30pm' else '10am–6pm'
        pg.click('label[for=jw-hours-tue]')  # the day's label focuses its own input
        focused = pg.evaluate('document.activeElement.id')
        pg.fill('#jw-hours-tue', new_tue)
        box = pg.locator('label.jw-check:has-text("Hours confirmed by owner") input')
        was = box.is_checked(); box.set_checked(not was)
        pg.click('button:has-text("Save business information")'); pg.wait_for_selector('text=Business information saved.')
        pg.reload(); pg.wait_for_selector('#jw-hours-tue'); pg.wait_for_timeout(400)
        after = {k: pg.input_value('#jw-hours-' + k) for k in before}
        ok = after['tue'] == new_tue and all(after[k] == before[k] for k in before if k != 'tue') and box.is_checked() == (not was)
        print(w, 'label focuses', focused, '| tue', before['tue'], '->', after['tue'], '| others kept', all(after[k] == before[k] for k in before if k != 'tue'), '| confirmed', was, '->', box.is_checked(), '| PASS' if ok else '| FAIL')
        c.close()
    b.close()
