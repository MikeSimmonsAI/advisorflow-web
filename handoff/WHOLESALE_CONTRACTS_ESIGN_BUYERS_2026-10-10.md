# Wholesale: ready-made contracts, DocuSeal e-signing, cash-buyer finder (2026-10-10)

Branch `wholesale/contracts-esign-buyers`. Same day as PRs #28 (tax-sale source),
#29 (Linebarger certificate fix) and #30 (Key numbers: vacant lot / absentee owner).

## What shipped

### 1. Ready-made contracts (`app/services/wholesale_contract_docs.py`)
Three Texas documents filled from the deal:

| kind | who signs (in order) | blocks sending until |
|---|---|---|
| `purchase_agreement` | Seller, then Buyer (your company "and/or assigns") | seller name, your company, address, county, price, earnest money, title company, closing date, option days, both emails |
| `assignment_agreement` | Assignee (end buyer), then Assignor (you) | your company, end buyer, seller, address, county, contract date, price, assignment fee, end-buyer deposit, title company, closing date, both emails |
| `assignee_disclosure` | Assignee, then Assignor | your company, end buyer, address, county, both emails (Occupations Code 1101.0045 notice) |

* Starter templates, labelled as such on every screen (`STARTER_NOTICE`): have a Texas
  real estate attorney approve the set once. The documents themselves carry no banner.
* Values: the deal first (price, earnest money, option fee, title company, dates,
  assigned buyer, seller from the Lead or owner of record, your company = organization
  name, signer = the signed-in user), then anything typed on the screen (`OVERRIDABLE`).
* Option days default to inspection deadline minus effective date when both exist.
* Preview/print copy shows blanks highlighted; the copy sent for signature carries
  DocuSeal `<signature-field>` / `<date-field>` tags per role.

Routes (`wholesale_contracts_router.py`):
* `GET  /wholesale/contract-kits` - kinds, labels, notice, e-signing state
* `POST /wholesale/deals/{id}/contracts/{kind}/preview` - read-only
* `POST /wholesale/deals/{id}/contracts/{kind}/send` - 409 while blanks/emails missing;
  `sent:false` + printable HTML when no provider; otherwise creates the
  WholesaleDocument (approved -> sent), sets deal.contract_status / assignment_status = sent
* `POST /wholesale/documents/{id}/signature-refresh` - asks the provider (works without a webhook)

UI: Deal -> Contracts ("Papering this deal") -> **Ready-made contracts** panel
(`wsContracts.jsx` `ReadyContracts`); Documents list gets **Check signing status**.

### 2. DocuSeal (`wholesale_esign.DocuSealProvider`)
* Env: `WHOLESALE_DOCUSEAL_API_KEY` (required), `WHOLESALE_DOCUSEAL_API_URL` (default
  https://api.docuseal.com; EU: https://api.docuseal.eu), `WHOLESALE_DOCUSEAL_WEBHOOK_SECRET` (optional).
* `active_provider()`: while the org setting is the default "manual", a connected
  electronic provider is used - adding the key IS the switch.
* Sends `POST /submissions/html` with `order: preserved`, `send_email: true`.
* Webhook `POST /esign/docuseal/webhook` (`app/routers/esign_webhook_router.py`, no session):
  finds OUR document by submission id, then re-reads the submission from DocuSeal with
  our key; only that record moves the document (signed / declined / viewed). Signed
  stores DocuSeal's combined PDF URL in `file_url` and the audit-trail URL in notes, and
  sets deal.contract_signed_at / assignment_signed_at.
* Pricing (docuseal.com/pricing, read 2026-10-10): API $0.20 per completed document,
  needs one Pro seat ($20/mo); sandbox/test mode free and unlimited.

### 3. Cash-buyer finder (`app/services/wholesale_buyer_finder.py`)
* Reads the DCAD export (ACCOUNT_APPRL_YEAR for type: A house, B multifamily, C1 lot;
  ACCOUNT_INFO for owner, mailing, situs, deed date) or the TAD file (State_Use_Code).
* Groups by normalized owner name; keeps owners with 3-150 residential parcels; counts
  deeds to them in the last 24 months; drops government, churches, nonprofits, HOAs,
  banks/servicers, housing authorities, utilities, confidential owners.
* Three streaming passes, per-owner state hashed until the qualifying owners are known.
* Result JSON in the EvoSense source cache (`buyer_finder_<county>.json`); the Render disk
  is not durable, so after a deploy click **Scan again**.
* Routes (`wholesale_buyers_router.py`): `GET /wholesale/buyers/finder?county=`,
  `POST /wholesale/buyers/finder/run`, `POST /wholesale/buyers/finder/import`.
* Imported investors: source `county_roll:<county>`, notes with mailing address, last deed
  and examples, buy box "From county records" (county, top ZIPs, property types).
  **No phone or email is invented** - they cannot be sent deals until one is added
  (Tracerfy lookup or a letter to the mailing address).
* UI: Buyers -> **Find cash buyers** drawer.

## Owner steps (Mike)
1. Merge the PR.
2. DocuSeal: sign up, Pro seat, API key -> Render `WHOLESALE_DOCUSEAL_API_KEY` -> **Save and Deploy**.
   Webhook in DocuSeal settings: `https://advisorflow-backend.onrender.com/esign/docuseal/webhook`
   (optional secret header `X-Docuseal-Secret` = `WHOLESALE_DOCUSEAL_WEBHOOK_SECRET`).
   Test with DocuSeal's test-mode key first.
3. Email deals to buyers: Render `OUTBOUND_EMAIL_WHOLESALE_BUYER_DISPOSITION=true` (Save and Deploy).
   Leave buyer SMS off until buyers have opted in to texts.
4. Phone lookups: Tracerfy $20 credit -> `TRACERFY_API_TOKEN`.
5. Buyers -> Find cash buyers -> Dallas -> Scan the county file -> pick -> Add.
6. Have a Texas real estate attorney approve the three starter documents.

## Tests
`tests/test_wholesale_ready_contracts.py` (8), `tests/test_wholesale_buyer_finder.py` (4),
cross-tenant attack list extended with the contract and refresh routes,
`test_wholesale_contracts.py` capability assertion updated (an electronic provider may be
listed; none is connected without its key).
