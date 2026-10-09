"""The public EvoSys Wholesale seller page (public-site/sell/index.php) and the
legal pages it points to.

Two layers:

* Static checks that always run: the opt-in box ships unchecked, the heading
  and every disclosure element are on the page, the navigation reaches
  EvoSysPro Home, and every link target exists (including the #anchors).
* A rendered check that runs when a PHP binary is available: the real page is
  served by `php -S`, pointed at a local stand-in for the platform intake, and
  submitted without the box, with the box, and with the field omitted. The
  stand-in records the exact payload the page would send the platform.

No request leaves the machine; nothing is sent to the platform or to anyone.
"""
from __future__ import annotations

import html
import json
import re
import shutil
import socket
import subprocess
import threading
import time
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

SITE = Path(__file__).resolve().parents[1] / "public-site"
SELL = SITE / "sell" / "index.php"


def _read(rel: str) -> str:
    return (SITE / rel).read_text(encoding="utf-8").replace("\r\n", "\n")


def _text(markup: str) -> str:
    markup = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", markup, flags=re.S)
    markup = re.sub(r"</?(a|strong|b|em|span|i)\b[^>]*>", "", markup)
    markup = re.sub(r"<[^>]+>", " ", markup)
    return re.sub(r"\s+", " ", html.unescape(markup)).strip()


def _disclosure_constant(src: str) -> str:
    m = re.search(r"const SELL_DISCLOSURE = '([^']+)';", src)
    assert m, "SELL_DISCLOSURE constant not found"
    return m.group(1)


def _checkbox_tag(page: str) -> str:
    m = re.search(r'<input[^>]*name="sms_consent"[^>]*>', page)
    assert m, "sms_consent checkbox not found"
    return m.group(0)


def _ids(markup: str) -> set:
    return set(re.findall(r'\bid="([^"]+)"', markup))


# --------------------------------------------------------------------- static

def test_checkbox_is_unchecked_by_default_in_source():
    src = _read("sell/index.php")
    tags = re.findall(r'<input[^>]*name="sms_consent"[^>]*>', src)
    assert len(tags) == 1, "exactly one SMS consent control - no second consent path"
    tag = tags[0]
    assert 'type="checkbox"' in tag and 'value="yes"' in tag
    # The only way the box renders checked is echoing the person's own tick back
    # after a validation error on the same submission.
    assert "checked" not in re.sub(r"<\?=.*?\?>", "", tag)
    assert "(($_POST['sms_consent'] ?? '') === 'yes') ? ' checked' : ''" in tag
    assert " required" not in tag


def test_consent_heading_and_subheading():
    src = _read("sell/index.php")
    block = src[src.index('<fieldset class="consent">'):src.index("</fieldset>", src.index('<fieldset class="consent">'))]
    legend = re.search(r"<legend>(.*?)</legend>", block, flags=re.S).group(1)
    assert _text(legend).upper().endswith("OPTIONAL SMS OPT-IN")
    assert "Want text updates about your property inquiry?" in block


def test_disclosure_label_is_the_registered_text_verbatim():
    src = _read("sell/index.php")
    label = re.search(r'<label for="f-sms_consent" id="sms-disclosure">(.*?)</label>', src, flags=re.S).group(1)
    assert _text(label) == _disclosure_constant(src)


def test_disclosure_covers_every_required_element():
    src = _read("sell/index.php")
    d = _disclosure_constant(src)
    for needle in ("EVO Integrated Solutions LLC", "EvoSys Wholesale", "property inquiry",
                   "Message frequency varies", "Message and data rates may apply",
                   "Reply STOP", "HELP", "Consent is not a condition", "Privacy Policy", "Terms"):
        assert needle in d, needle
    label = re.search(r'<label for="f-sms_consent" id="sms-disclosure">(.*?)</label>', src, flags=re.S).group(1)
    assert 'href="/privacy.html#sms"' in label and 'href="/terms.html#sms-wholesale"' in label


def test_submit_is_not_consent_and_consent_is_independent_of_terms():
    src = _read("sell/index.php")
    assert "Submitting this form does not sign you up for text messages" in src
    # No Terms-acceptance checkbox that could bundle consent.
    assert len(re.findall(r'type="checkbox"', src)) == 1
    # The payload carries consent only from the box.
    assert "$sms = isset($_POST['sms_consent']) && $_POST['sms_consent'] === 'yes';" in src
    assert "'sms_consent' => $sms," in src
    assert "'disclosure_text' => $sms ? SELL_DISCLOSURE : null," in src


def test_navigation_reaches_evosyspro_home_and_page_sections():
    src = _read("sell/index.php")
    header = src[src.index('<header class="top dark"'):src.index("</header>")]
    assert header.count('href="/"') == 2, "EvoSysPro Home on desktop nav and in the mobile menu"
    assert "EvoSysPro Home" in header
    for anchor in ("#how", "#situations", "#seller-form"):
        assert f'href="{anchor}"' in header
    ids = _ids(src)
    for anchor in ("how", "situations", "seller-form", "f-sms_consent"):
        assert anchor in ids
    assert '<details class="mnav">' in header, "mobile menu"
    footer = src[src.index('<footer class="foot dark"'):src.index("</footer>")]
    assert 'href="/"' in footer and "EvoSysPro Home" in footer
    assert 'href="/privacy.html#sms"' in footer and 'href="/terms.html#sms-wholesale"' in footer


def test_every_link_from_the_seller_page_resolves():
    src = _read("sell/index.php")
    targets = {"privacy.html": _ids(_read("privacy.html")),
               "terms.html": _ids(_read("terms.html")),
               "sms-terms.html": _ids(_read("sms-terms.html"))}
    for href in set(re.findall(r'href="(/[a-z-]+\.html)(?:#([^"]*))?"', src)):
        page, frag = href
        assert page.lstrip("/") in targets, page
        if frag:
            assert frag in targets[page.lstrip("/")], f"{page}#{frag}"


def test_public_phone_is_configuration_not_code():
    src = _read("sell/index.php")
    assert "evosys_cfg('WHOLESALE_PUBLIC_PHONE', '')" in src
    assert "469" not in re.sub(r"<\?php.*?\?>", "", src, flags=re.S).split("<body>")[1]


def test_homepage_has_one_wholesale_path_and_keeps_the_sms_optin():
    page = _read("index.html")
    band = page[page.index('id="wholesale"'):page.index('id="trust"')]
    assert "EvoSys Wholesale" in band and "powered by EvoSysPro" in band
    assert 'href="/sell">Sell a Property' in band
    header = page[page.index('<nav class="nav">'):page.index('</nav>')]
    assert re.search(r'href="/?sms-optin/">SMS Opt-In', header), "the existing SMS Opt-In stays in the header"
    assert len(re.findall(r'href="/?sms-optin/"', page)) >= 3


def test_existing_sms_optin_page_is_unchanged_and_points_sellers_to_sell():
    # The Universal SMS Consent Center moved the wording into the program
    # registry; the general program's wording and source URL are unchanged.
    src = _read("sms-optin/index.php")
    reg = _read("private/sms-programs.php")
    assert ("'disclosure' => 'By checking this box, I agree to receive SMS/text messages from EvoSys Pro "
            "including appointment confirmations, reminders, and related service follow-ups.") in reg
    assert "'version' => '2026-09'" in reg
    assert "$sourceUrl = 'https://evosyspro.live/sms-optin/'" in src
    assert 'href="/sell"' in src and "separate program" in src


def test_privacy_covers_seller_data_and_mobile_non_sharing():
    t = _read("privacy.html")
    assert 'id="sms"' in t and 'id="sms-wholesale"' in t
    assert "seller inquiry to EvoSys Wholesale" in t
    assert "Text messaging originator opt-in data and consent will not be shared with any third parties." in t
    assert "Mobile information will not be shared with third parties or affiliates for marketing or promotional purposes." in t
    assert "A phone number obtained from public records" in t


def test_terms_and_sms_terms_cover_the_wholesale_program():
    for rel, anchor in (("terms.html", "sms-wholesale"), ("sms-terms.html", "wholesale")):
        t = _read(rel)
        sec = t[t.index(f'id="{anchor}"'):]
        sec = sec[:sec.index("Changes &")]
        for needle in ("EVO Integrated Solutions LLC", "evosyspro.live/sell", "unchecked",
                       "not a condition", "Message frequency varies", "Message and data rates may apply",
                       "STOP", "HELP", "469-553-7417", "support@evosyspro.live",
                       "will not be shared with any third parties",
                       "third parties or affiliates for marketing or promotional purposes"):
            assert needle in sec, f"{rel}: {needle}"
        assert "privacy.html" in sec


def test_footer_links_to_wholesale_on_every_static_page():
    for page in SITE.glob("*.html"):
        assert '<a href="/sell">EvoSys Wholesale</a>' in page.read_text(encoding="utf-8"), page.name
    assert '<a href="/sell">EvoSys Wholesale</a>' in _read("private/site.php")


# ------------------------------------------------------------------- rendered

PHP = shutil.which("php")


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class _Intake(BaseHTTPRequestHandler):
    received: list = []

    def do_POST(self):  # noqa: N802
        body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        payload = json.loads(body or b"{}")
        _Intake.received.append(payload)
        out = json.dumps({"success": True, "reference": "TEST-REF",
                          "sms_consent_recorded": bool(payload.get("sms_consent"))}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    def log_message(self, *a):
        pass


@pytest.fixture(scope="module")
def served(tmp_path_factory):
    if not PHP:
        pytest.skip("php is not installed here; the static checks above still ran")
    intake_port, web_port = _free_port(), _free_port()
    intake = HTTPServer(("127.0.0.1", intake_port), _Intake)
    threading.Thread(target=intake.serve_forever, daemon=True).start()
    env = {"WHOLESALE_SELLER_INTAKE_URL": f"http://127.0.0.1:{intake_port}/intake",
           "WHOLESALE_PUBLIC_PHONE": "469-553-7417", "PATH": "/usr/bin:/bin"}
    proc = subprocess.Popen([PHP, "-S", f"127.0.0.1:{web_port}", "-t", str(SITE)], env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    base = f"http://127.0.0.1:{web_port}"
    for _ in range(50):
        try:
            urllib.request.urlopen(base + "/sell/", timeout=1)
            break
        except Exception:
            time.sleep(0.1)
    yield base
    proc.terminate()
    intake.shutdown()


FORM = {"street_address": "100 Test Fixture Ln", "city": "Testville", "state": "TX", "zip_code": "75001",
        "full_name": "ZZTEST Page Check", "phone": "(214) 555-0142", "email": "",
        "preferred_contact_method": "phone", "form_started_at": "1"}


def _post(base: str, data: dict) -> str:
    req = urllib.request.Request(base + "/sell/", data=urllib.parse.urlencode(data).encode(), method="POST")
    return urllib.request.urlopen(req, timeout=10).read().decode("utf-8")


def test_rendered_page_box_unchecked_and_heading_visible(served):
    page = urllib.request.urlopen(served + "/sell/", timeout=10).read().decode("utf-8")
    assert "checked" not in _checkbox_tag(page)
    text = _text(page)
    assert "Optional SMS Opt-In" in text
    assert "Want text updates about your property inquiry?" in text
    assert "469-553-7417" in text
    assert "EvoSysPro Home" in text


def test_rendered_submit_without_consent_sends_inquiry_and_no_consent(served):
    _Intake.received.clear()
    page = _post(served, FORM)
    assert "we received your property inquiry" in page
    assert "You did not sign up for text messages" in page
    (p,) = _Intake.received
    assert p["sms_consent"] is False and p["disclosure_text"] is None
    assert p["source_url"] == "https://evosyspro.live/sell"


def test_rendered_field_omitted_or_wrong_value_grants_nothing(served):
    for extra in ({}, {"sms_consent": ""}, {"sms_consent": "on"}, {"sms_consent": "true"}):
        _Intake.received.clear()
        _post(served, {**FORM, **extra})
        (p,) = _Intake.received
        assert p["sms_consent"] is False and p["disclosure_text"] is None, extra


def test_rendered_submit_with_consent_sends_the_verbatim_disclosure(served):
    _Intake.received.clear()
    page = _post(served, {**FORM, "sms_consent": "yes"})
    assert "You asked to receive text messages" in page
    (p,) = _Intake.received
    assert p["sms_consent"] is True
    assert p["disclosure_text"] == _disclosure_constant(_read("sell/index.php"))
    assert p["disclosure_version"] and p["form_version"] == "sell-v1"


def test_rendered_text_preference_without_box_is_refused_not_upgraded(served):
    _Intake.received.clear()
    page = _post(served, {**FORM, "preferred_contact_method": "sms"})
    assert "check the SMS consent box" in page
    assert _Intake.received == [], "nothing is sent, and nothing is inferred"
    assert "checked" not in _checkbox_tag(page)
