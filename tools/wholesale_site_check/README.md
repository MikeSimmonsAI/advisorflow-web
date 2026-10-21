# Wholesale site form check (desktop + mobile)

Local only. Uses test data and sends nothing. It drives the real `buyers.html` form → `intake.php` → `bridge.php` against a stub of the platform route. The stub records the JSON that would reach `/site-intake/wholesale/<key>/buyer-inquiry`. That route is covered by `tests/test_wholesale_buyer_intake.py`.

```bash
P=$(mktemp -d); mkdir -p $P/private $P/shots
python3 tools/wholesale_site_check/stub_platform.py $P/captured.jsonl &
(cd public-site/wholesale && WHOLESALE_INTAKE_DIR=$P/private \
  WHOLESALE_BUYER_INTAKE_URL=http://127.0.0.1:8765/site-intake/wholesale/testkey/buyer-inquiry \
  php -S 127.0.0.1:8088) &
npm i --prefix $P playwright@1.56.1 && cp tools/wholesale_site_check/check_forms.mjs $P/
(cd $P && PRIV=$P/private OUT=$P/shots node check_forms.mjs)   # set executablePath for your Chromium
```

What it checks, on a 1366px desktop viewport and an iPhone 13 viewport:
- no sideways scrolling on the buyers or sell pages;
- an empty form posts nothing (the browser's own validation stops it);
- the mobile menu opens and shows its links;
- a completed form shows "Reference: BUY-…";
- the JSON sent to the platform matches the `BuyerInquiryPayload` fields;
- the seller links point at the existing `evosyspro.live/sell/` intake.

Platform outage: stop the stub and submit again. The registration is still accepted and its file records `"evosys": {"sent": false}`. Then `php public-site/wholesale/resend.php --send` hands it over once the platform is back.
