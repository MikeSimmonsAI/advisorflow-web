import sys, json
from playwright.sync_api import sync_playwright
html=open('/home/claude/advisorflow-web/frontend/public/demo/blue-ridge/index.html').read()
with sync_playwright() as p:
    b=p.chromium.launch(executable_path='/usr/bin/google-chrome',args=['--no-sandbox'])
    pg=b.new_page(viewport={'width':1366,'height':860}); errs=[]
    pg.on('pageerror',lambda e:errs.append(str(e)))
    pg.on('console',lambda m:errs.append(m.text) if m.type=='error' else None)
    pg.set_content('<html><body style="margin:0"><iframe id=f sandbox="allow-scripts" style="border:0;width:100vw;height:100vh"></iframe></body></html>')
    pg.evaluate("h=>{document.getElementById('f').srcdoc=h}", html); pg.wait_for_timeout(1200)
    f=pg.frames[1]
    out={}
    for sid in ['customers','campaigns','appointments','ai','inventory','reports','how','roadmap','dashboard']:
        f.click(f'.nav button[data-go="{sid}"]'); pg.wait_for_timeout(120)
        out[sid]=f.locator(f'#v-{sid}.active').count()==1
    f.click('.nav button[data-go="customers"]'); f.click('#custTable tbody tr.click >> nth=1'); pg.wait_for_timeout(300)
    out['drawer']=f.locator('#drawer.show').count()==1
    f.click('#drawer .x'); pg.wait_for_timeout(300); out['drawerClose']=f.locator('#drawer.show').count()==0
    f.click('.nav button[data-go="campaigns"]'); f.click('#sendBtn'); f.wait_for_selector('text=SIMULATION COMPLETE',timeout=8000); out['sim']=True
    f.click('.topbar button:has-text("TOUR")'); pg.wait_for_timeout(200)
    for i in range(5): f.click('#tourNext'); pg.wait_for_timeout(150)
    out['tour6']='STEP 6 OF 6' in f.inner_text('#tour')
    f.click('#tour button:has-text("Exit")')
    f.click('.nav button[data-go="how"]'); f.click('button:has-text("Print / Save PDF")'); pg.wait_for_timeout(200)
    out['printMsg']='direct link' in f.inner_text('#toast')
    f.click('button:has-text("Reset Demo")'); pg.wait_for_timeout(200); out['reset']=f.evaluate('S.runs.length')==3
    pg.screenshot(path='/home/claude/qa/framed.png')
    out['errors']=errs
    print(json.dumps(out)); b.close()
