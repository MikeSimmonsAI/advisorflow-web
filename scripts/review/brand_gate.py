"""PERMANENT BRAND-RESOLUTION ACCEPTANCE GATE - reusable for any module / tenant.

A page that functions correctly but renders the wrong tenant/platform brand is a
FAILED acceptance test. Import this from any real-browser acceptance run
(Playwright sync API) and call it on every screen:

    from brand_gate import app_brand, public_brand
    report, failures = app_brand(page, EXPECT)          # signed-in app screens
    report, failures = public_brand(page, EXPECT, kind, token)   # public rooms

EXPECT describes the tenant under test, e.g. the canonical EvoSys review:

    EXPECT = {
        "org_id": "acc04387-...", "org_name": "EvoSense Review (TEST)",
        "platform_slug": "evosyspro", "theme": "evosyspro",
        "platform_name": "EvoSys Pro", "product": "EvoSys Wholesale",
        "engine": "EvoSense", "api": "http://localhost:8000",
        "forbidden": ["BookaBoost", "Restland", "cemetery", "funeral"],
        "public_phone": "469-553-7417", "public_email": None,
    }

What it proves on an app screen: the organization the server says is signed in,
the platform it resolves to, that the SHELL's brand came from that workspace
(data-brand-source == 'workspace', or 'host' on a real brand domain) and not a
hostname default, the html theme, the tab title, the sidebar brand and product
line, and that no forbidden tenant/brand name is visible. On a public room it
proves the operator identity and that the public contact came from the
configured Wholesale public-contact setting (contact_source), with no fallback.
Every report says which organization, which platform, and whether a fallback
occurred.
"""

APP_JS = """async (api) => {
  const root = document.querySelector('.layout')
  const d = root ? root.dataset : {}
  let org = null
  try {
    const tok = localStorage.getItem('af_token')
    const r = await fetch(api + '/branding/org', { headers: { Authorization: 'Bearer ' + tok } })
    org = r.ok ? await r.json() : { error: r.status }
  } catch (e) { org = { error: String(e) } }
  const brandEl = document.querySelector('.sidebar-brand')
  const logo = brandEl && brandEl.querySelector('img')
  const product = document.querySelector('.wsx-product__name')
  const engine = document.querySelector('.wsx-product__engine')
  return {
    title: document.title,
    html_theme: document.documentElement.getAttribute('data-theme'),
    shell_theme: d.brandTheme || null, shell_source: d.brandSource || null,
    shell_platform: d.brandPlatform || null, shell_org: d.orgId || null, shell_product: d.product || null,
    sidebar_brand: brandEl ? brandEl.innerText.trim() : null, logo_alt: logo ? logo.alt : null,
    product_line: product ? product.innerText.trim() : null, engine_line: engine ? engine.innerText.trim() : null,
    website_link: (document.querySelector('.back-to-website-btn') || {}).href || null,
    org_payload: org, text: document.body.innerText + '\\n' + document.title,
  } }"""

PUBLIC_JS = """async ([api, kind, token]) => {
  let room = null
  try { const r = await fetch(api + '/wholesale-rooms/' + kind + '/' + token); room = r.ok ? await r.json() : { error: r.status } }
  catch (e) { room = { error: String(e) } }
  const mast = document.querySelector('.wr__mast')
  const mc = document.querySelector('.wr__mast-contact')
  const visible = !!(mc && mc.getBoundingClientRect().width > 0 && getComputedStyle(mc).display !== 'none')
  return { title: document.title, masthead: mast ? mast.innerText.trim() : null, contact_visible: visible,
           brand: room && room.brand, text: document.body.innerText + '\\n' + document.title } }"""


def _forbidden(text, expect):
    low = (text or "").lower()
    return [w for w in expect.get("forbidden", []) if w.lower() in low]


def app_brand(page, expect):
    """Brand context of a signed-in app screen, and the list of failures."""
    info = page.evaluate(APP_JS, expect["api"])
    text = info.pop("text")
    org = info.pop("org_payload") or {}
    plat = org.get("platform") or {}
    source = info.get("shell_source")
    report = {
        "organization": org.get("brand_name") or expect.get("org_name"),
        "organization_id": org.get("organization_id"),
        "resolved_platform": plat.get("slug"),
        "resolved_platform_name": plat.get("display_name"),
        "product": (plat.get("products") or {}).get("wholesale"),
        "shell_theme": info.get("shell_theme"), "shell_source": source,
        "html_theme": info.get("html_theme"), "title": info.get("title"),
        "sidebar_brand": info.get("sidebar_brand"), "logo_alt": info.get("logo_alt"),
        "product_line": info.get("product_line"), "engine_line": info.get("engine_line"),
        "website_link": info.get("website_link"),
        "fallback_occurred": source not in ("workspace", "host"),
    }
    f = []
    if report["organization_id"] != expect["org_id"]:
        f.append("signed-in organization %r is not %r" % (report["organization_id"], expect["org_id"]))
    if report["resolved_platform"] != expect["platform_slug"]:
        f.append("platform resolved to %r, expected %r" % (report["resolved_platform"], expect["platform_slug"]))
    if report["fallback_occurred"]:
        f.append("FALLBACK: shell brand source is %r (hostname default), not the workspace" % source)
    if info.get("shell_theme") != expect["theme"]:
        f.append("shell theme %r, expected %r" % (info.get("shell_theme"), expect["theme"]))
    if info.get("html_theme") != expect["theme"]:
        f.append("document theme %r, expected %r" % (info.get("html_theme"), expect["theme"]))
    if info.get("shell_org") and info["shell_org"] != expect["org_id"]:
        f.append("shell org %r != %r" % (info["shell_org"], expect["org_id"]))
    if expect.get("product"):
        if report["product"] != expect["product"]:
            f.append("brand product name %r, expected %r" % (report["product"], expect["product"]))
        if info.get("shell_product") and info["shell_product"] != expect["product"]:
            f.append("shell product %r, expected %r" % (info["shell_product"], expect["product"]))
        if info.get("title") != expect["product"]:
            f.append("tab title %r, expected %r" % (info.get("title"), expect["product"]))
    if expect.get("engine") and info.get("engine_line") and expect["engine"].lower() not in info["engine_line"].lower():
        f.append("engine line %r does not name %r" % (info["engine_line"], expect["engine"]))
    if expect.get("platform_name"):
        shown = " ".join(filter(None, [info.get("sidebar_brand"), info.get("logo_alt")]))
        if expect["platform_name"] not in shown and not expect.get("org_brands_sidebar"):
            f.append("sidebar shows %r, expected the platform %r" % (shown, expect["platform_name"]))
    link = info.get("website_link") or ""
    if link and any(w.lower().replace(" ", "") in link.lower() for w in expect.get("forbidden", [])):
        f.append("website link points at another brand: %s" % link)
    bad = _forbidden(text, expect)
    if bad:
        f.append("forbidden brand/tenant text visible: %s" % ", ".join(bad))
    return report, f


def public_brand(page, expect, kind, token):
    """Brand + public-contact context of a public room (seller / buyer)."""
    info = page.evaluate(PUBLIC_JS, [expect["api"], kind, token])
    text = info.pop("text")
    brand = info.get("brand") or {}
    report = {"organization": brand.get("name"), "title": info.get("title"),
              "public_phone": brand.get("support_phone"), "public_email": brand.get("support_email"),
              "contact_source": brand.get("contact_source"), "masthead": info.get("masthead"),
              "fallback_occurred": bool((brand.get("support_phone") or brand.get("support_email"))
                                        and brand.get("contact_source") != "wholesale_settings")}
    f = []
    if report["fallback_occurred"]:
        f.append("public contact did not come from the Wholesale public-contact setting")
    if expect.get("org_name") and brand.get("name") != expect["org_name"]:
        f.append("room names %r, expected the operator %r" % (brand.get("name"), expect["org_name"]))
    if "public_phone" in expect and brand.get("support_phone") != expect["public_phone"]:
        f.append("public phone %r, expected %r" % (brand.get("support_phone"), expect["public_phone"]))
    if "public_email" in expect and brand.get("support_email") != expect["public_email"]:
        f.append("public email %r, expected %r" % (brand.get("support_email"), expect["public_email"]))
    # The masthead contact is hidden at phone/tablet widths by the approved
    # responsive design (rooms.css); where it IS shown it must be the configured one.
    report["contact_visible"] = info.get("contact_visible")
    if info.get("contact_visible") and expect.get("public_phone") \
            and expect["public_phone"] not in (info.get("masthead") or ""):
        f.append("configured public phone is not shown in the masthead")
    bad = _forbidden(text, expect)
    if bad:
        f.append("forbidden brand/tenant text visible: %s" % ", ".join(bad))
    return report, f
