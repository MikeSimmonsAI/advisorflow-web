# Customer public websites

One folder per customer site, each holding the page exactly as it is served.

```
public-sites/<slug>/index.html      the page at  /site/<slug>
```

These are NOT `demos/`. A demo is a mockup shown to a prospect during a sale,
tokenised and rendered in a sandboxed iframe so nobody mistakes it for a live
system. These are live customer websites: the form on them files a real lead
into that customer's own workspace.

## Publishing

```
python scripts/publish_customer_site.py --org-id <organization id> \
    --slug <slug> --file public-sites/<slug>/index.html
```

Dry run by default. `--apply` writes. Republishing keeps the address — the URL
is on the customer's stationery and a publish that moved it would break every
link they have handed out.

## What a page may and may not do

* Its form POSTs to `/site/<slug>/inquiry`, same origin, JSON. Nothing else on
  the page talks to the platform.
* It carries a honeypot field named `website_url` and a `form_started_at`
  timestamp. Both are read by the server; neither is ever stored.
* If it asks for messaging consent, the wording lives on the `customer_sites`
  row, not in the markup — the row is what gets written to the lead as
  evidence, and a client cannot change it.
* **It states no price, rate, plan or availability that the platform cannot
  prove.** A number on a live customer website is a representation that
  customer has to stand behind. Where a real provider feed is not connected
  yet, the page asks for the enquiry instead of inventing the answer.
* Nothing on these pages sends email, SMS or voice. An enquiry appears in the
  workspace, which is where the person working it is already looking.
