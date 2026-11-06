import json
from playwright.sync_api import sync_playwright
html=open('/home/claude/advisorflow-web/frontend/public/demo/blue-ridge/index.html').read()
with sync_playwright() as p:
    b=p.chromium.launch(executable_path='/usr/bin/google-chrome',args=['--no-sandbox'])
    out={}
    for label,signed,staff in [('staff_signed',True,True),('public',False,False),('public_staffparam',False,True)]:
        c=b.new_context(viewport={'width':390,'height':844}); pg=c.new_page()
        pg.route('**/public/demo/**', lambda r: r.fulfill(status=200, content_type='application/json', body=json.dumps({'title':'Blue Ridge Gold & Jewelry — Business Command Center (Interactive Demo)','html':html})))
        pg.route('**advisorflow-backend.onrender.com/**', lambda r: r.fulfill(status=404, body='{}') if '/public/demo/' not in r.request.url else r.fallback())
        if signed: pg.add_init_script("localStorage.setItem('af_token','x')")
        pg.goto('http://127.0.0.1:8765/demo/blue-ridge-gold'+('?staff=1' if staff else '')); pg.wait_for_timeout(2500)
        out[label]={'bar':pg.locator('nav[aria-label=Preview] a').count(), 'href':pg.get_attribute('nav[aria-label=Preview] a','href') if pg.locator('nav[aria-label=Preview] a').count() else None, 'iframe':pg.locator('iframe').count()}
        pg.screenshot(path=f'/home/claude/qa/staffbar_{label}.png')
        c.close()
    print(json.dumps(out)); b.close()
