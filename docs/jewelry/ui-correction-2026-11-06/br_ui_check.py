"""Blue Ridge UI correction: screenshots + layout measurements.
usage: python3 br_ui_check.py OUTDIR [base_url]
Prints one JSON line per viewport with the checks, and saves the 5 required captures."""
import json, os, sys
from playwright.sync_api import sync_playwright

seed = json.load(open('/home/claude/qa/local_seed.json'))
OUT = sys.argv[1]; BASE = sys.argv[2] if len(sys.argv) > 2 else 'http://localhost:5173'
os.makedirs(OUT, exist_ok=True)

MEASURE = r"""() => {
  const r = e => e && e.getBoundingClientRect()
  const vis = e => { if (!e) return false; const s = getComputedStyle(e), b = r(e); return s.display !== 'none' && s.visibility !== 'hidden' && b.width > 0 && b.height > 0 }
  const hit = (a, b) => a.left < b.right - 0.5 && b.left < a.right - 0.5 && a.top < b.bottom - 0.5 && b.top < a.bottom - 0.5
  const doc = document.documentElement
  // Business hours: every label/input box must not intersect any other one.
  const parts = [...document.querySelectorAll('[data-hours-row] > *, .jw-hours label > *')].map(e => ({ t: e.textContent.trim() || e.value || e.placeholder, b: r(e) }))
  const hoursRows = document.querySelectorAll('[data-hours-row], .jw-hours label').length
  let overlaps = []
  for (let i = 0; i < parts.length; i++) for (let j = i + 1; j < parts.length; j++) if (hit(parts[i].b, parts[j].b)) overlaps.push(parts[i].t + ' x ' + parts[j].t)
  // inputs whose placeholder is wider than the box (clipped)
  const clipped = [...document.querySelectorAll('.jw input.jw-input')].filter(i => vis(i) && i.scrollWidth > i.clientWidth + 1).map(i => i.placeholder || i.value)
  // any element sticking out of the viewport horizontally
  const wide = [...document.querySelectorAll('.jw-content *')].filter(e => vis(e) && r(e).right > doc.clientWidth + 1).slice(0, 5).map(e => e.className || e.tagName)
  const menu = document.querySelector('.jw-top__menu'), back = document.querySelector('.jw-top [data-testid=back-to-program-access]')
  const title = document.querySelector('.jw-top__title'), me = document.querySelector('.jw-me'), top = document.querySelector('.jw-top')
  const side = document.querySelector('.jw-side')
  return {
    vw: doc.clientWidth, scrollW: doc.scrollWidth, overflow: doc.scrollWidth > doc.clientWidth,
    hoursRows, hoursOverlaps: overlaps, clipped, wide,
    hamburger: vis(menu), sidebarVisible: vis(side) && r(side).right > 10,
    statusChip: !!document.querySelector('.jw-chip-status'), headerText: top && top.innerText.replace(/\s+/g, ' '),
    backCount: document.querySelectorAll('[data-testid=back-to-program-access]').length,
    backHref: back && back.getAttribute('href'), backVisible: vis(back),
    backLeft: back && Math.round(r(back).left), backRightSide: back && r(back).left > doc.clientWidth / 2,
    backOverlapsTitle: !!(back && title && vis(title) && hit(r(back), r(title))), backOverlapsMe: !!(back && me && vis(me) && hit(r(back), r(me))),
    headerH: top && Math.round(r(top).height),
  }
}"""

def ctx(b, w, h, mob=False, dpr=1):
    c = b.new_context(viewport={'width': w, 'height': h}, is_mobile=mob, has_touch=mob, device_scale_factor=dpr)
    c.add_init_script("localStorage.setItem('af_token', %s); localStorage.setItem('af_user', %s);" % (
        json.dumps(seed['platform']['token']), json.dumps(json.dumps(seed['platform']['user']))))
    return c

with sync_playwright() as p:
    b = p.chromium.launch(executable_path='/usr/bin/google-chrome', args=['--no-sandbox'])
    sizes = [('1920', 1920, 1080, False, 1), ('1440', 1440, 900, False, 1), ('1366', 1366, 768, False, 1), ('1024', 1024, 768, False, 1),
             ('768', 768, 1024, True, 2), ('390', 390, 844, True, 3), ('375', 375, 812, True, 3),
             # 125% browser zoom = the same window showing 1/1.25 as many CSS pixels at 1.25x density
             ('1440@125%', 1152, 720, False, 1.25), ('1366@125%', 1093, 614, False, 1.25), ('390@125%', 312, 675, True, 3.75)]
    for name, w, h, mob, dpr in sizes:
        c = ctx(b, w, h, mob, dpr); pg = c.new_page(); errs = []
        pg.on('pageerror', lambda e: errs.append(str(e)))
        pg.goto(BASE + '/program/jewelry/settings'); pg.wait_for_selector('text=Business information', timeout=30000); pg.wait_for_timeout(700)
        m = pg.evaluate(MEASURE); m['view'] = name; m['errors'] = errs[:2]
        print(json.dumps(m))
        tag = name.replace('@', '_at_').replace('%', 'pct')
        if name in ('1440', '390', '1024'):
            pg.screenshot(path=f'{OUT}/settings-full-{tag}.jpg', full_page=True, quality=80, type='jpeg')
            hrs = pg.locator('[data-hours], .jw-hours').first
            hrs.scroll_into_view_if_needed(); hrs.screenshot(path=f'{OUT}/business-hours-{tag}.jpg', quality=90, type='jpeg')
            pg.evaluate('scrollTo(0,0)'); pg.wait_for_timeout(200)
            pg.locator('.jw-top').screenshot(path=f'{OUT}/header-{tag}.jpg', quality=90, type='jpeg')
        if name == '390':
            menu = pg.locator('.jw-top__menu')
            if menu.is_visible():
                menu.click(); pg.wait_for_timeout(400)
                pg.screenshot(path=f'{OUT}/mobile-nav-open-390.jpg', quality=85, type='jpeg')
                focused = pg.evaluate('document.activeElement && (document.activeElement.getAttribute("aria-label") || document.activeElement.textContent.trim())')
                res = {'nav_open_focus': focused}
                def step(k, fn):
                    try: res[k] = fn()
                    except Exception as e: res[k] = 'FAIL: ' + str(e).splitlines()[0][:80]
                    if pg.locator('.jw-side.open').count():
                        try: pg.locator('.jw-scrim').click(position={'x': 370, 'y': 400}, timeout=2000); pg.wait_for_timeout(300)
                        except Exception: pass
                def opened():
                    menu.click(timeout=3000); pg.wait_for_timeout(350)
                def esc():
                    pg.keyboard.press('Escape'); pg.wait_for_timeout(350); return not pg.locator('.jw-side.open').count()
                step('esc_closes', esc)
                res['focus_back_on_menu'] = pg.evaluate('!!document.activeElement && document.activeElement.classList.contains("jw-top__menu")')
                def backdrop():
                    opened(); pg.mouse.click(370, 400); pg.wait_for_timeout(350); return not pg.locator('.jw-side.open').count()
                step('backdrop_closes', backdrop)
                def closebtn():
                    opened(); pg.locator('.jw-side [aria-label="Close navigation"]').click(timeout=2000); pg.wait_for_timeout(350); return not pg.locator('.jw-side.open').count()
                step('close_button_closes', closebtn)
                def same():
                    opened(); pg.locator('.jw-nav a', has_text='Settings & Team').click(timeout=3000); pg.wait_for_timeout(400); return not pg.locator('.jw-side.open').count()
                step('closes_after_nav_same_page', same)
                def other():
                    opened(); pg.locator('.jw-nav a', has_text='Customer Vault').click(timeout=3000); pg.wait_for_timeout(800); return [not pg.locator('.jw-side.open').count(), pg.url]
                step('closes_after_nav', other)
                print(json.dumps({'mobile_nav': res}))
        c.close()
    # Program Access card
    for name, w, h, mob, dpr in (('1440', 1440, 900, False, 1), ('390', 390, 844, True, 3)):
        c = ctx(b, w, h, mob, dpr); pg = c.new_page()
        pg.goto(BASE + '/workspaces'); pg.wait_for_selector('.ev-wcard', timeout=30000); pg.wait_for_timeout(900)
        card = pg.locator('.ev-wcard', has_text='Blue Ridge').first
        card.scroll_into_view_if_needed(); pg.wait_for_timeout(300)
        card.screenshot(path=f'{OUT}/program-access-card-{name}.jpg', quality=90, type='jpeg')
        info = card.evaluate('e => ({variant: e.dataset.variant, text: e.innerText.replace(/\\s+/g, " ")})')
        print(json.dumps({'card': name, **info}))
        lst = pg.locator('button[aria-label*="List" i], button:has-text("List")').first
        if lst.count():
            lst.click(); pg.wait_for_timeout(500)
            card = pg.locator('.ev-wcard', has_text='Blue Ridge').first
            if card.count(): card.scroll_into_view_if_needed(); card.screenshot(path=f'{OUT}/program-access-card-list-{name}.jpg', quality=90, type='jpeg')
        c.close()
    b.close()
