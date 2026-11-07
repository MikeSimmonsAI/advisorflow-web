import json, sys, os
from playwright.sync_api import sync_playwright
seed = json.load(open('/home/claude/qa/local_seed.json'))
OUT = sys.argv[1]; os.makedirs(OUT, exist_ok=True)
with sync_playwright() as p:
    b = p.chromium.launch(executable_path='/usr/bin/google-chrome', args=['--no-sandbox'])
    for name, vp, mob in (('desktop', {'width': 1440, 'height': 900}, False), ('tablet', {'width': 820, 'height': 1180}, False), ('mobile', {'width': 390, 'height': 844}, True)):
        c = b.new_context(viewport=vp, is_mobile=mob, device_scale_factor=2 if mob else 1)
        c.add_init_script("localStorage.setItem('af_token', %s); localStorage.setItem('af_user', %s);" % (json.dumps(seed['platform']['token']), json.dumps(json.dumps(seed['platform']['user']))))
        pg = c.new_page(); errs = []
        pg.on('pageerror', lambda e: errs.append(str(e)))
        pg.goto('http://localhost:5173/workspaces'); pg.wait_for_selector('.ev-wcard', timeout=20000); pg.wait_for_timeout(800)
        n = pg.locator('.ev-wcard').count()
        variants = pg.eval_on_selector_all('.ev-wcard', 'els => els.map(e => e.dataset.variant + ":" + e.querySelector(".ev-wcard-name").innerText)')
        sw = pg.evaluate('document.documentElement.scrollWidth')
        pg.locator('#pa-work-title').scroll_into_view_if_needed(); pg.wait_for_timeout(300)
        pg.screenshot(path=os.path.join(OUT, 'program-access-%s.png' % name), full_page=False)
        pg.screenshot(path=os.path.join(OUT, 'program-access-%s-full.png' % name), full_page=True)
        print(name, n, sw, variants, errs[:2])
        if name == 'desktop':
            pg.click('.ev-wcard[data-variant=gold] .ev-wcard-open'); pg.wait_for_timeout(2500)
            print('open gold ->', pg.url)
        c.close()
    b.close()
