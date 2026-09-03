#!/usr/bin/env python3
"""
smoke_test_brands.py - Multi-brand bootstrap smoke gate.

PURPOSE:
  Any change that touches shared frontend infrastructure (App.jsx, api/client.js,
  auth/bootstrap, ProtectedRoute, HomeRedirect, brand bootstrap, theme providers,
  deps.py, shared Layout/context) must prove every supported brand still loads
  before the push is accepted.

WHAT IS CHECKED:
  For each domain:
    1. HTML response (the app shell) - 200 OK
    2. Referenced JS bundle - 200 OK (the exact failure mode of commit 18369e5)
    3. Referenced CSS bundle - 200 OK
    4. Correct brand name appears in the HTML
    5. Brand-specific support email is present

USAGE:
  python scripts/smoke_test_brands.py
  python scripts/smoke_test_brands.py --timeout 10

RETURNS:
  0  all brands pass all checks
  1  one or more checks failed
"""

import re
import sys
import urllib.request
import urllib.error
from dataclasses import dataclass, field

BRANDS = [
    {
        "name": "EvoSys Pro",
        "domain": "https://app.evosyspro.live",
        "brand_text": "EvoSys Pro",
        "support_email": "support@evosyspro.live",
    },
    {
        "name": "BookaBoost",
        "domain": "https://app.bookaboost.live",
        "brand_text": "BookaBoost",
        "support_email": "support@bookaboost.live",
    },
]

ASSET_PATTERN = re.compile(
    r'(?:src|href)=["\'](?P<path>/assets/[^"\'?#]+)["\']',
    re.IGNORECASE,
)


@dataclass
class CheckResult:
    brand: str
    check: str
    passed: bool
    detail: str = ""


def fetch(url: str, timeout: int = 15) -> tuple:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "SmokeTester/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        return e.code, ""
    except Exception as e:
        return 0, str(e)


def head_status(url: str, timeout: int = 15) -> int:
    try:
        req = urllib.request.Request(url, method="HEAD",
                                     headers={"User-Agent": "SmokeTester/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status
    except urllib.error.HTTPError as e:
        return e.code
    except Exception:
        return 0


def smoke_brand(brand: dict, timeout: int = 15) -> list:
    results = []
    domain = brand["domain"]
    name = brand["name"]

    status, html = fetch(domain, timeout)
    html_ok = status == 200
    results.append(CheckResult(name, "HTML (app shell)", html_ok,
                               f"GET {domain} -> {status}"))

    if not html_ok:
        results.append(CheckResult(name, "JS bundle", False, "HTML check failed - skipping"))
        results.append(CheckResult(name, "CSS bundle", False, "HTML check failed - skipping"))
        results.append(CheckResult(name, "brand identity in HTML", False,
                                   "HTML check failed - skipping"))
        return results

    asset_paths = list(dict.fromkeys(m.group("path") for m in ASSET_PATTERN.finditer(html)))
    js_assets  = [p for p in asset_paths if p.endswith(".js")]
    css_assets = [p for p in asset_paths if p.endswith(".css")]

    for asset in js_assets:
        url = domain + asset
        code = head_status(url, timeout)
        ok = (code == 200)
        results.append(CheckResult(name, f"JS bundle ({asset})", ok,
                                   f"HEAD {url} -> {code}"))

    for asset in css_assets:
        url = domain + asset
        code = head_status(url, timeout)
        ok = (code == 200)
        results.append(CheckResult(name, f"CSS bundle ({asset})", ok,
                                   f"HEAD {url} -> {code}"))

    if not js_assets:
        results.append(CheckResult(name, "JS bundle", False,
                                   "No /assets/*.js reference found in HTML - build may be stale"))
    if not css_assets:
        results.append(CheckResult(name, "CSS bundle", False,
                                   "No /assets/*.css reference found in HTML - build may be stale"))

    brand_in_html = brand["brand_text"] in html
    results.append(CheckResult(name, "brand name in HTML", brand_in_html,
                               f"'{brand['brand_text']}' {'found' if brand_in_html else 'NOT FOUND'} in HTML"))

    email_in_html = brand["support_email"] in html
    results.append(CheckResult(name, "support email in HTML", email_in_html,
                               f"'{brand['support_email']}' {'found' if email_in_html else 'NOT FOUND'} in HTML"))

    return results


def main() -> int:
    timeout = 15
    for i, arg in enumerate(sys.argv[1:]):
        if arg == "--timeout" and i + 2 < len(sys.argv):
            try:
                timeout = int(sys.argv[i + 2])
            except ValueError:
                pass

    all_results = []
    for brand in BRANDS:
        print(f"\n-- {brand['name']} ({brand['domain']}) --")
        results = smoke_brand(brand, timeout)
        for r in results:
            icon = "OK" if r.passed else "FAIL"
            print(f"  {icon}  {r.check}")
            if not r.passed or "--verbose" in sys.argv:
                print(f"       {r.detail}")
        all_results.extend(results)

    failures = [r for r in all_results if not r.passed]

    print("\n" + "=" * 60)
    if failures:
        print(f"SMOKE GATE FAILED - {len(failures)} check(s) failed")
        print("=" * 60)
        print("\nFAILURES:")
        for f in failures:
            print(f"  [{f.brand}] {f.check}")
            print(f"    {f.detail}")
        print("\nDO NOT PUSH until all brands pass.")
        return 1

    passed = len(all_results)
    print(f"SMOKE GATE PASSED - {passed}/{passed} checks passed across all brands")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
