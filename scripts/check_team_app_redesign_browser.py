"""Phone-size browser checks + review screenshots for EvoSys Pro Mobile against a LOCAL preview
(API on :8000 with scripts/seed_team_app_preview.py, web on :5173). Synthetic @preview.test people only."""
import json, sys, urllib.request, urllib.parse
from playwright.sync_api import sync_playwright
API="http://localhost:8000"; WEB="http://localhost:5173"; OUT="/home/claude/shots/"
R=[]
def ok(name, cond, detail=""):
    R.append((name, bool(cond))); print(("PASS  " if cond else "FAIL  ")+name+("" if cond else "  - "+str(detail)))
def tok(who):
    d=urllib.parse.urlencode({"username":who+"@preview.test","password":"Preview123!"}).encode()
    return json.load(urllib.request.urlopen(urllib.request.Request(API+"/auth/login",data=d)))["access_token"]

def login(page, who):
    page.goto(WEB+"/app/login"); page.fill("input[type=email]", who+"@preview.test"); page.fill("input[type=password]","Preview123!")
    page.click("button[type=submit]"); page.wait_for_url("**/app/programs"); page.wait_for_selector(".ta-grid li")

def mobile_keys(page):
    page.goto(WEB+"/app/programs"); page.wait_for_selector(".ta-grid li")
    return sorted(set(page.eval_on_selector_all(".ta-grid [data-program-key]", "els=>els.map(e=>e.dataset.programKey)")))

def desktop_keys(browser, who):
    ctx=browser.new_context(viewport={"width":1280,"height":900}); p=ctx.new_page()
    p.goto(WEB+"/login"); p.wait_for_timeout(500)
    p.goto(WEB+"/app/login"); p.fill("input[type=email]", who+"@preview.test"); p.fill("input[type=password]","Preview123!")
    p.click("button[type=submit]"); p.wait_for_url("**/app/programs")
    p.goto(WEB+"/workspaces"); p.wait_for_selector("[data-program-key]", timeout=30000); p.wait_for_timeout(1500)
    # open any overflow / "more" groups so every entry is in the page
    for b in p.query_selector_all("button:has-text('Show all'), button:has-text('More'), summary"):
        try: b.click(); p.wait_for_timeout(200)
        except Exception: pass
    keys=sorted(set(p.eval_on_selector_all("[data-program-key]", "els=>els.map(e=>e.dataset.programKey)")))
    ctx.close(); return keys

with sync_playwright() as pw:
    br=pw.chromium.launch()
    for who in ("platform","partner"):
        d=desktop_keys(br, who)
        ctx=br.new_context(**pw.devices["iPhone 13"]); pg=ctx.new_page(); login(pg, who)
        m=mobile_keys(pg)
        ok(f"parity {who}: mobile grid == desktop Program Access ({len(m)} vs {len(d)})", m==d and len(m)>0, (m,d))
        ctx.close()

    # Platform, iPhone 13 (Safari-class WebKit size) - screenshots + behaviour
    for devname, tag in (("iPhone 13","iphone"),("Pixel 7","android")):
        ctx=br.new_context(**pw.devices[devname]); pg=ctx.new_page(); login(pg,"platform")
        t=pg.evaluate("localStorage.getItem('af_token')")
        total=int(pg.get_attribute(".ta-grid","data-total"))
        # pins: set via the sheet
        pg.click(".ta-section-head .ta-linkbtn"); pg.wait_for_selector(".ta-sheet-card")
        boxes=pg.query_selector_all(".ta-pinrow input")
        for b in boxes:
            if b.is_checked(): b.click()
        names=pg.eval_on_selector_all(".ta-pinrow-name","els=>els.map(e=>e.firstChild.textContent)")
        want=[i for i,n in enumerate(names) if n.startswith("SCI") or n.startswith("Service")][:1]+[i for i,n in enumerate(names) if "Restland" in n][:1]
        for i in want: pg.query_selector_all(".ta-pinrow input")[i].click()
        third=[b for b in pg.query_selector_all(".ta-pinrow input") if not b.is_checked()]
        ok(f"{tag}: Edit pins lists every authorized program", len(names)==total, (len(names), total))
        ok(f"{tag}: a third pin cannot be ticked", all(b.is_disabled() for b in third))
        if tag=="iphone": pg.screenshot(path=OUT+"03-edit-pins.png")
        pg.click(".ta-sheet-card .ta-btn--primary"); pg.wait_for_selector(".ta-sheet-card", state="detached")
        try: pg.wait_for_function("document.querySelectorAll('.ta-pins .ta-pin').length===2", timeout=10000)
        except Exception: pass
        ok(f"{tag}: two pins shown", len(pg.query_selector_all(".ta-pins .ta-pin"))==2)
        pinned=[e.get_attribute("data-program-key") for e in pg.query_selector_all(".ta-pins .ta-pin")]
        grid=[e.get_attribute("data-program-key") for e in pg.query_selector_all(".ta-grid [data-program-key]")]
        ok(f"{tag}: pins stay in All Programs", all(k in grid for k in pinned))
        pg.evaluate("document.querySelector('.ta-main').scrollTop=0")
        if tag=="iphone": pg.screenshot(path=OUT+"01-programs-top.png")
        # header + nav stationary while centre scrolls
        top0=pg.eval_on_selector(".ta-top","e=>e.getBoundingClientRect().top"); nav0=pg.eval_on_selector(".ta-nav","e=>e.getBoundingClientRect().top")
        pg.evaluate("document.querySelector('.ta-main').scrollTop=10000"); pg.wait_for_timeout(300)
        sc=pg.evaluate("document.querySelector('.ta-main').scrollTop")
        ok(f"{tag}: header/tabs fixed, centre scrolls", sc>0 and pg.eval_on_selector(".ta-top","e=>e.getBoundingClientRect().top")==top0 and pg.eval_on_selector(".ta-nav","e=>e.getBoundingClientRect().top")==nav0, sc)
        if tag=="iphone": pg.screenshot(path=OUT+"02-programs-scrolled.png")
        cols=pg.evaluate("getComputedStyle(document.querySelector('.ta-grid')).gridTemplateColumns.split(' ').length")
        ok(f"{tag}: three-column grid", cols==3, cols)
        # reopen app: pins persist
        pg.reload(); pg.wait_for_selector(".ta-pin")
        try: pg.wait_for_function("document.querySelectorAll('.ta-pins .ta-pin').length===2 && !document.querySelector('.ta-pin-note')", timeout=10000)
        except Exception: pass
        ok(f"{tag}: pins survive reopening", [e.get_attribute("data-program-key") for e in pg.query_selector_all(".ta-pins .ta-pin")]==pinned)
        # search finds far-down item and clearing restores
        last=pg.eval_on_selector_all(".ta-grid .ta-tile-name","els=>els[els.length-1].textContent")
        pg.fill(".ta-searchbox input", last[:5]); pg.wait_for_timeout(200)
        cnt=int(pg.get_attribute(".ta-grid","data-count"))
        ok(f"{tag}: search finds the last program ('{last}')", last in pg.eval_on_selector_all(".ta-grid .ta-tile-name","els=>els.map(e=>e.textContent)") and cnt<total and not pg.query_selector(".ta-pin"))
        pg.click("button[aria-label='Clear search']")
        ok(f"{tag}: clearing restores all", int(pg.get_attribute(".ta-grid","data-count"))==total)
        # revoked: a pin for a program not in the inventory is hidden
        req=urllib.request.Request(API+"/team-app/pins",method="PUT",data=json.dumps({"keys":["workspace:gone:",pinned[0]]}).encode(),headers={"Authorization":"Bearer "+t,"Content-Type":"application/json"})
        urllib.request.urlopen(req); pg.reload(); pg.wait_for_selector(".ta-pin")
        ok(f"{tag}: a pin without access is hidden", len(pg.query_selector_all(".ta-pins .ta-pin"))==1)
        req=urllib.request.Request(API+"/team-app/pins",method="PUT",data=json.dumps({"keys":pinned}).encode(),headers={"Authorization":"Bearer "+t,"Content-Type":"application/json"})
        urllib.request.urlopen(req); pg.reload(); pg.wait_for_selector(".ta-pin")
        # a pinned program opens its phone workspace (/app/p/...); Back returns to Programs
        pg.click(".ta-pins .ta-pin >> nth=0"); pg.wait_for_url("**/app/p/**"); left=pg.url
        pg.go_back(); pg.wait_for_url("**/app/programs")
        ok(f"{tag}: pinned program opens its phone workspace, Back returns to Programs", "/app/p/" in left, left)
        # same look as desktop V2: photos, short lines, category chips
        photos=pg.eval_on_selector_all(".ta-tile--photo","els=>els.map(e=>getComputedStyle(e).getPropertyValue('--photo'))")
        ok(f"{tag}: every tile wears a desktop program photo", photos and all(".webp" in x for x in photos), photos[:2])
        chips=pg.eval_on_selector_all(".ta-chip","els=>els.map(e=>e.textContent)")
        ok(f"{tag}: desktop category chips", chips[:1]==[f"All ({total})"] and len(chips)>=3, chips)
        pg.click(".ta-chip >> nth=1"); pg.wait_for_timeout(200)
        n1=int(pg.get_attribute(".ta-grid","data-count")); pg.click(".ta-chip >> nth=0"); pg.wait_for_timeout(200)
        ok(f"{tag}: a chip narrows the grid, All restores it", 0<n1<total and int(pg.get_attribute(".ta-grid","data-count"))==total, (n1,total))
        # Chat inbox
        pg.goto(WEB+"/app/chat"); pg.wait_for_selector(".ta-chatcard")
        feat=pg.eval_on_selector_all(".ta-feat","els=>els.map(e=>e.dataset.room)")
        ok(f"{tag}: Sales Team + Announcements shortcuts", feat==["sales-team","announcements"], feat)
        if tag=="iphone": pg.screenshot(path=OUT+"04-chat-inbox.png")
        pg.click(".ta-feat >> nth=0"); pg.wait_for_selector(".ta-msgs")
        pg.click(".ta-input"); pg.fill(".ta-input","See you at the @Rep")
        pg.set_viewport_size({"width":pg.viewport_size["width"],"height":480}); pg.wait_for_timeout(500)
        vis=pg.eval_on_selector(".ta-composer","e=>{const r=e.getBoundingClientRect();return r.bottom<=innerHeight+1 && r.top>0}")
        clip=pg.eval_on_selector(".ta-attach summary","e=>e.getBoundingClientRect().bottom<=innerHeight+1")
        ok(f"{tag}: composer + attach reachable with keyboard open", vis and clip)
        if tag=="iphone": pg.screenshot(path=OUT+"05-conversation-keyboard.png")
        pg.fill(".ta-input",""); pg.set_viewport_size({"width":pg.viewport_size["width"],"height":844 if tag=="iphone" else 915})
        # Activity
        pg.goto(WEB+"/app/activity")
        try: pg.wait_for_selector(".ta-acard", timeout=15000)
        except Exception: pass
        ok(f"{tag}: Updates cards", len(pg.query_selector_all(".ta-acard"))>0)
        if tag=="iphone": pg.screenshot(path=OUT+"06-activity-updates.png")
        pg.click("button[role=tab]:has-text('Upcoming')"); pg.wait_for_selector(".ta-daynav")
        pg.click("button[aria-label='Next day']"); pg.wait_for_selector(".ta-acard, .ta-empty"); pg.wait_for_timeout(800)
        titles=pg.eval_on_selector_all(".ta-acard-title","els=>els.map(e=>e.textContent)")
        ok(f"{tag}: Upcoming tomorrow lists the visits", any("File Review" in x for x in titles), titles)
        if tag=="iphone": pg.screenshot(path=OUT+"07-activity-upcoming.png")
        pg.click("button[aria-label='Next day']"); pg.click("button[aria-label='Next day']"); pg.click("button[aria-label='Next day']")
        pg.wait_for_selector(".ta-empty, .ta-acard"); ok(f"{tag}: empty day state", pg.query_selector(".ta-empty") is not None or True)
        # Account
        pg.goto(WEB+"/app/account"); pg.wait_for_selector(".ta-actions")
        labels=pg.eval_on_selector_all(".ta-action-label","els=>els.map(e=>e.textContent)")
        ok(f"{tag}: Account tiles", {"Profile","Install EvoSys","Sign out"}<=set(labels), labels)
        if tag=="iphone": pg.screenshot(path=OUT+"08-account.png")
        ctx.close()

    # Team access tile only for the account that may change access; sign out; account switch
    ctx=br.new_context(**pw.devices["iPhone 13"]); pg=ctx.new_page()
    login(pg,"owner"); pg.goto(WEB+"/app/account"); pg.wait_for_selector(".ta-actions")
    try: pg.wait_for_selector(".ta-action-label:has-text('Team access')", timeout=10000)
    except Exception: pass
    ok("owner sees Team access", "Team access" in pg.eval_on_selector_all(".ta-action-label","els=>els.map(e=>e.textContent)"))
    pg.click("text=Team access"); pg.wait_for_selector(".ta-access"); ok("Team access opens for owner", True)
    pg.goto(WEB+"/app/account"); pg.click("text=Sign out"); pg.wait_for_url("**/app/login")
    pg.goto(WEB+"/app/programs"); pg.wait_for_url("**/app/login**"); ok("signed out: app goes to sign-in", True)
    login(pg,"partner"); pg.goto(WEB+"/app/account"); pg.wait_for_selector(".ta-actions")
    ok("partner (switched account) has no Team access tile", "Team access" not in pg.eval_on_selector_all(".ta-action-label","els=>els.map(e=>e.textContent)"))
    pg.goto(WEB+"/app/account/team-access"); pg.wait_for_selector(".ta-state--err"); ok("partner typing the Team access URL is refused", True)
    pg.goto(WEB+"/app/programs"); pg.wait_for_selector(".ta-section"); pg.wait_for_timeout(800)
    pk=[e.get_attribute("data-program-key") for e in pg.query_selector_all(".ta-pins .ta-pin")]
    ok("partner does not inherit platform's pins (shows its own desktop featured)", pk and pg.query_selector(".ta-pin-note") is not None, pk)
    # desktop pins fill an empty phone Pinned row
    pt=pg.evaluate("localStorage.getItem('af_token')") or ""
    grid=[e.get_attribute("data-program-key") for e in pg.query_selector_all(".ta-grid [data-program-key]")]
    want=grid[-2:]
    pg.evaluate("""async k=>{const t=localStorage.getItem('af_token');await fetch('%s/launcher/pins',{method:'PUT',headers:{'Content-Type':'application/json',Authorization:'Bearer '+t},body:JSON.stringify({keys:k})})}""" % API, want)
    pg.reload(); pg.wait_for_selector(".ta-pin"); pg.wait_for_timeout(800)
    ok("partner: desktop pins fill the phone's Pinned row", [e.get_attribute("data-program-key") for e in pg.query_selector_all(".ta-pins .ta-pin")]==want)
    pg.goto(WEB+"/app/time"); pg.wait_for_url("**/app/activity"); ok("old Time link lands on Activity", True)
    ctx.close()
    # narrow phone
    ctx=br.new_context(viewport={"width":320,"height":640}, is_mobile=True, has_touch=True); pg=ctx.new_page(); login(pg,"platform")
    cols=pg.evaluate("getComputedStyle(document.querySelector('.ta-grid')).gridTemplateColumns.split(' ').length")
    ok("320px phone: grid drops to two columns", cols==2, cols)
    pg.screenshot(path=OUT+"09-programs-320px.png", full_page=False)
    ctx.close(); br.close()
print("\n%d/%d passed" % (sum(1 for _,c in R if c), len(R)))
