import json, sys, os
from playwright.sync_api import sync_playwright
seed = json.load(open('/home/claude/qa/local_seed.json')); BASE, OUT = sys.argv[1], sys.argv[2]; os.makedirs(OUT, exist_ok=True)
with sync_playwright() as p:
    b = p.chromium.launch(executable_path='/usr/bin/google-chrome', args=['--no-sandbox'])
    for name, w, h, mob, dpr in (('1440', 1440, 900, False, 2), ('390', 390, 844, True, 3), ('1440_at_125pct', 1152, 720, False, 2.5)):
        c = b.new_context(viewport={'width': w, 'height': h}, is_mobile=mob, device_scale_factor=dpr)
        c.add_init_script("localStorage.setItem('af_token', %s); localStorage.setItem('af_user', %s);" % (json.dumps(seed['platform']['token']), json.dumps(json.dumps(seed['platform']['user']))))
        pg = c.new_page(); pg.goto(BASE + '/workspaces'); pg.wait_for_selector('.ev-wcard', timeout=30000); pg.wait_for_timeout(900)
        card = pg.locator('.ev-wcard', has_text='Blue Ridge').first; card.scroll_into_view_if_needed(); pg.wait_for_timeout(300)
        card.screenshot(path=f'{OUT}/blue-ridge-card-grid-{name}.jpg', quality=92, type='jpeg')
        info = card.evaluate('e => ({variant: e.dataset.variant, text: e.innerText.replace(/\\s+/g, " ")})')
        sw = pg.evaluate('[document.documentElement.scrollWidth, document.documentElement.clientWidth]')
        others = pg.eval_on_selector_all('.ev-wcard', 'els => els.map(e => e.dataset.variant)')
        btns = pg.eval_on_selector_all('button', 'els => els.map(e => (e.getAttribute("aria-label")||"") + "|" + e.innerText.trim()).filter(t => /list|grid|view/i.test(t))')
        print(name, info, 'scroll', sw, 'variants', others, 'viewbtns', btns[:4])
        lb = pg.locator('button', has_text='List').first
        if lb.count():
            lb.click(); pg.wait_for_timeout(600); card = pg.locator('.ev-wcard', has_text='Blue Ridge').first; card.scroll_into_view_if_needed()
            card.screenshot(path=f'{OUT}/blue-ridge-card-list-{name}.jpg', quality=92, type='jpeg')
            print(' list layout:', card.evaluate('e => e.className'))
        c.close()
    b.close()
