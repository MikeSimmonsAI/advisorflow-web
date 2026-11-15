import sys, json, urllib.request
from playwright.sync_api import sync_playwright
BASE=sys.argv[1] if len(sys.argv)>1 else "http://localhost:5173"
OUT=sys.argv[2] if len(sys.argv)>2 else "/home/claude/volt-qa/site"
res=[]
def ok(n,c): res.append((n,bool(c))); print(("PASS " if c else "FAIL ")+n)
with sync_playwright() as p:
    b=p.chromium.launch()
    for name,vp in [("desktop",{"width":1280,"height":900}),("mobile",{"width":390,"height":844})]:
        pg=b.new_page(viewport=vp); posts=[]
        pg.on("request", lambda r: posts.append(r.url) if r.method=="POST" and "/leads" in r.url else None)
        pg.goto(BASE+"/volt/check-coverage"); pg.wait_for_selector("[data-funnel-intro]",timeout=20000)
        t=pg.inner_text("main")
        ok(f"{name}: headline shown","You May Qualify for $0 Monthly Health Insurance" in t)
        ok(f"{name}: no shutdown page","This form is not open yet" not in t)
        ok(f"{name}: closed notice shown",pg.locator("[data-intake-closed]").count()==1)
        ok(f"{name}: benefits shown",pg.locator("[data-funnel-intro] li").count()>=6)
        d=pg.inner_text("[data-funnel-disclaimers]").lower()
        ok(f"{name}: disclaimers shown","not a government" in d and "not guaranteed" in d)
        ok(f"{name}: no sideways scroll",pg.evaluate("document.documentElement.scrollWidth<=window.innerWidth+1"))
        pg.screenshot(path=f"{OUT}/{name}-landing.png",full_page=True)
        pg.click("[data-funnel-start]")
        ok(f"{name}: question 1",pg.locator("text=Question 1 of 8").count()==1)
        pg.fill("input[autocomplete=postal-code]","12"); pg.click("[data-funnel-next]")
        ok(f"{name}: bad ZIP refused","5-digit ZIP" in pg.inner_text("#lead-err"))
        pg.fill("input[autocomplete=postal-code]","75201"); pg.click("[data-funnel-next]")
        pg.select_option("select","TX"); pg.click("[data-funnel-next]")
        pg.click("text=← Back"); ok(f"{name}: back works",pg.locator("text=Question 2 of 8").count()==1)
        pg.click("[data-funnel-next]")
        for lbl in ["26 to 44","No, I need coverage","2","$20,000 – $35,000"]:
            pg.locator("label",has_text=lbl).first.click(); pg.click("[data-funnel-next]")
        pg.locator("label",has_text="Phone call").click(); pg.locator("label",has_text="Morning").click(); pg.click("[data-funnel-next]")
        ok(f"{name}: reached last step",pg.locator("text=Question 8 of 8").count()==1)
        ok(f"{name}: final note",pg.locator("[data-closed-final]").count()==1)
        ok(f"{name}: inputs off",pg.locator("input[autocomplete=given-name]").is_disabled())
        btn=pg.locator("[data-funnel-next]")
        ok(f"{name}: send off",btn.is_disabled() and "Requests open soon" in btn.inner_text())
        ok(f"{name}: consent wording shown","licensed agent" in pg.inner_text("form"))
        ok(f"{name}: no success message","Thank you" not in pg.inner_text("main"))
        ok(f"{name}: no submit sent",not posts)
        pg.screenshot(path=f"{OUT}/{name}-last-step.png",full_page=True)
    b.close()
print(json.dumps({"passed":sum(c for _,c in res),"total":len(res)}))
