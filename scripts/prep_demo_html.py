"""
Make a demo HTML file safe to publish through the demo-site route.

    python scripts/prep_demo_html.py --in <file.html> --out <file.html>
                                     [--label "DESIGN CONCEPT"]
                                     [--note "..."] [--safe-contacts]

WHY THIS EXISTS. `demos/README.md` states two rules every demo must keep, and
both of them are easy to forget and invisible until a prospect is looking at
the page:

1. HANDLE YOUR OWN IN-PAGE LINKS. A published demo renders inside
   `<iframe srcdoc sandbox="allow-scripts">`. In that frame a bare
   `href="#section"` does NOT scroll - it resolves against the HOST page's URL
   and reloads the whole app inside the frame. The prospect clicks your nav and
   the demo vanishes. This injects an interceptor that scrolls the page itself.

2. SAY WHAT IT IS. A concept must announce that it is one. This injects a bar
   at the top of the page carrying that sentence.

It never edits the input file. It writes a new one, and every injection is
idempotent - running it twice changes nothing the second time - so a demo can
be re-prepped after an edit without accumulating banners.

`--safe-contacts` additionally rewrites telephone numbers and email addresses
to the reserved ranges the README specifies (555-01xx, example.*), so a demo
number can never reach a real person. It PRINTS every substitution, because
replacing a prospect's own real number with a placeholder is a change you have
to see before you send the link.
"""
from __future__ import annotations

import argparse
import re
import sys

MARK_ANCHORS = "data-demo-anchor-fix"
MARK_BAR = "data-demo-concept-bar"

# Reserved for fiction. NANP 555-0100..555-0199 is set aside for exactly this,
# and `example.com` is reserved by RFC 2606.
SAFE_PHONE_PREFIX = "555-01"
SAFE_EMAIL_DOMAIN = "example.com"

ANCHOR_FIX = """
<script %s>
/* Published demos render inside <iframe srcdoc sandbox>, where a bare
   href="#id" resolves against the HOST document's URL and navigates the frame
   away from the demo. Intercept and scroll ourselves. */
(function () {
  document.addEventListener('click', function (e) {
    var a = e.target && e.target.closest && e.target.closest('a[href^="#"]');
    if (!a) return;
    var id = a.getAttribute('href').slice(1);
    e.preventDefault();
    if (!id) { window.scrollTo({ top: 0, behavior: 'smooth' }); return; }
    var el = document.getElementById(id)
          || document.querySelector('[name="' + (window.CSS && CSS.escape
                                                 ? CSS.escape(id) : id) + '"]');
    if (el && el.scrollIntoView) el.scrollIntoView({ behavior: 'smooth' });
  }, false);
})();
</script>
""".strip() % MARK_ANCHORS

BAR = """
<div %s style="position:relative;z-index:9999;background:#0b1f38;color:#e8f1fb;
  font:600 12px/1.5 ui-sans-serif,system-ui,-apple-system,'Segoe UI',sans-serif;
  letter-spacing:.02em;padding:10px 16px;text-align:center;
  border-bottom:1px solid rgba(255,255,255,.14)">
  <b style="letter-spacing:.12em">%s</b>
  <span style="opacity:.82">&nbsp;·&nbsp;%s</span>
</div>
""".strip()

DEFAULT_NOTE = ("Every name, figure and contact detail on this page is "
                "illustrative. Nothing here is connected to a live system.")


def inject_anchor_fix(html: str) -> tuple[str, bool]:
    if MARK_ANCHORS in html:
        return html, False
    if "</body>" in html:
        return html.replace("</body>", ANCHOR_FIX + "\n</body>", 1), True
    return html + "\n" + ANCHOR_FIX, True


def inject_bar(html: str, label: str, note: str) -> tuple[str, bool]:
    if MARK_BAR in html:
        return html, False
    bar = BAR % (MARK_BAR, label, note)
    m = re.search(r"<body[^>]*>", html, re.I)
    if m:
        return html[:m.end()] + "\n" + bar + html[m.end():], True
    return bar + "\n" + html, True


# A phone number as a human writes one. Deliberately conservative: it wants a
# separator or brackets, so it will not eat a price, a ZIP+4 or a date.
#
# The trailing guard rejects a following DIGIT, and a decimal point that has a
# digit after it - but not a full stop. An earlier version excluded any "."
# and therefore silently skipped every phone number that ended a sentence,
# which is where a phone number usually is.
PHONE_RE = re.compile(
    r"(?<![\d.])(\+?1[\s.\-]?)?\(?([2-9]\d{2})\)?[\s.\-]([2-9]\d{2})[\s.\-](\d{4})"
    r"(?!\d)(?!\.\d)")
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
TEL_HREF_RE = re.compile(r'href\s*=\s*"tel:([^"]+)"', re.I)


def safe_contacts(html: str) -> tuple[str, list]:
    """Rewrite phone numbers and emails into the reserved ranges.

    Each distinct original maps to ONE replacement, so a number that appears in
    the header and the footer stays the same number on the page - a demo whose
    contact details disagree with themselves reads as broken, not as fiction.
    """
    changes: list = []
    phone_map: dict = {}
    email_map: dict = {}
    counter = {"p": 0}

    def next_phone(area: str) -> str:
        counter["p"] += 1
        return "(%s) %s%02d" % (area, SAFE_PHONE_PREFIX, counter["p"] % 100)

    def sub_phone(m):
        original = m.group(0)
        if original not in phone_map:
            phone_map[original] = next_phone(m.group(2))
            changes.append(("phone", original, phone_map[original]))
        return phone_map[original]

    def sub_email(m):
        original = m.group(0)
        low = original.lower()
        if low.endswith(("example.com", "example.org", "example.net")):
            return original
        if original not in email_map:
            local = re.sub(r"[^a-z0-9.]+", "", original.split("@")[0].lower()) or "hello"
            email_map[original] = "%s@%s" % (local, SAFE_EMAIL_DOMAIN)
            changes.append(("email", original, email_map[original]))
        return email_map[original]

    html = EMAIL_RE.sub(sub_email, html)
    html = PHONE_RE.sub(sub_phone, html)

    # tel: hrefs carry the digits with no separators, so the pattern above does
    # not see them. Rewrite them from whatever the visible number became.
    def sub_tel(m):
        digits = re.sub(r"\D", "", m.group(1))
        for orig, new in phone_map.items():
            if re.sub(r"\D", "", orig)[-10:] == digits[-10:]:
                return 'href="tel:%s"' % re.sub(r"\D", "", new)
        return m.group(0)

    html = TEL_HREF_RE.sub(sub_tel, html)
    return html, changes


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="src", required=True)
    ap.add_argument("--out", dest="dst", required=True)
    ap.add_argument("--label", default="DESIGN CONCEPT")
    ap.add_argument("--note", default=DEFAULT_NOTE)
    ap.add_argument("--safe-contacts", action="store_true")
    a = ap.parse_args()

    with open(a.src, "r", encoding="utf-8", errors="replace") as fh:
        html = fh.read()
    before = len(html)

    html, did_anchor = inject_anchor_fix(html)
    html, did_bar = inject_bar(html, a.label, a.note)
    changes = []
    if a.safe_contacts:
        html, changes = safe_contacts(html)

    with open(a.dst, "w", encoding="utf-8", newline="") as fh:
        fh.write(html)

    print("in :", a.src)
    print("out:", a.dst)
    print("anchor interceptor:", "injected" if did_anchor else "already present")
    print("concept bar       :", "injected" if did_bar else "already present")
    if a.safe_contacts:
        if not changes:
            print("contacts          : nothing to rewrite")
        for kind, old, new in changes:
            print("  %-6s %-34s -> %s" % (kind, old, new))
    kb = len(html.encode("utf-8")) / 1024.0
    print("size: %.0fKB (was %.0fKB)  limit 2048KB" % (kb, before / 1024.0))
    if kb > 2048:
        print("TOO LARGE - the publish route refuses anything over 2MB.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
