# Provider selection package: contact/skip trace, sold comps/property data, imagery

Prepared on 2026-09-26 for Mike. **This is a decision package, not a recommendation to buy.** Nothing has been purchased, signed up for or activated. Every fact below comes from the vendor's own page and is linked. Anything I could not confirm is marked **unverified**.

## What EvoSys needs from a provider, and where the software plugs it in

| Need | EvoSys capability | Where it plugs in (already built) |
|---|---|---|
| Owner → phone(s), email(s), line type, confidence, provider record id | PHONE, EMAIL, LINE_TYPE, OWNER_IDENTITY | `evosense/providers.py` adapter → `enrichment.execute` → contact points as **candidates** (identity/confidence rules) → Contact Confidence → contactability |
| Line type / active / reassigned at dial time | PHONE_VALIDATION, LINE_TYPE | `validate_contacts` (paid, same budget) |
| Closed sales with price, date, sqft, beds, baths, lat/lng, sale type | SOLD_COMPS | comp evidence object → comp eligibility rules → ARV engine |
| Deed, mortgage, lien, foreclosure, tax | DEED, MORTGAGE, LIEN, FORECLOSURE, TAX | EvoSense observations → signals (Property Truth) |
| AVM | VALUATION | stored and shown **as an AVM**, never as ARV |
| Street / aerial imagery | IMAGERY | property page (display only) |

Every provider call already goes through these controls:

* the per-tenant provider config and the platform block
* the policy tier (free only / standard / aggressive / manual approval)
* the budget reserve → charge / refund
* the freshness cache
* the cost ledger, attributed to the tenant and the property
* a **kill switch**

**Hard technical constraints for any contract:**

1. **Permissible purpose.** The vendor must allow our use: owner outreach for a real-estate purchase, not FCRA uses. GLBA and DPPA certifications must be pushed down to each tenant.
2. **Multi-tenant redistribution.** EvoSys serves several customer workspaces. Several vendors forbid disclosing data to third parties without a reseller addendum.
3. **Caching and retention limits.** Our freshness cache must honour them.
4. **Phone data is never consent.** EvoSys already enforces this.

## A. Contact / skip trace

| Vendor | Access | Pricing (as published) | Relevant terms | Notes |
|---|---|---|---|---|
| **BatchData** | Real-time API, bulk; property skip trace ≤100 properties/request ([docs](https://developer.batchdata.com/docs/batchdata/batchdata-v1/operations/create-a-property-skip-trace)) | Monthly plans: $2,000/mo for 100k records up to $20,000 for 3M ([pricing](https://batchdata.io/pricing)); a PAYG per-record price is **unverified** | **No disclosure to third parties without a Reseller Addendum**; refresh caches every 30 days; not consumer reports / no FCRA use; data "does not … evidence consent" ([ToS](https://batchdata.io/terms-of-service)) | Mobile/landline flag, confidence, DNC and litigator scrub ([page](https://batchdata.io/skip-tracing)) |
| **Tracerfy** | Bearer API: sync lookup (500/min) and async batch with webhook ([docs](https://www.tracerfy.com/skip-tracing-api-documentation/)) | Pay per hit, **misses free**: normal $0.02/hit; the sync API costs 5 credits/hit; optional plans from $700/mo ([pricing](https://www.tracerfy.com/pricing)) | Customer responsible for TCPA; no FCRA/GLBA/resale statement found ([FAQ](https://www.tracerfy.com/faqs)) | Phone `type`, `dnc`, `carrier`, `rank`; litigator flag; **no sandbox**, so tests use real credits |
| **EnformionGO** (formerly Endato) | API key headers; Contact Enrichment needs 2 of name/phone/address/email ([docs](https://enformiongo.readme.io/)) | From $0.25/match; Pro "as low as $0.01"; **100 free matches/month**; no minimums ([pricing](https://go.enformion.com/pricing/)) | Not a CRA; GLBA data requires credentialing ([GLBA](https://go.enformion.com/news/what-is-glba/)) | Response field names **unverified** (page returned 403) |
| **PropStream / REISkip** | No public developer API found ([help](https://www.propstream.com/help)) | n/a | n/a | Treat as UI/export only unless sales says otherwise (**unverified**) |
| **Melissa Personator** | [docs](https://docs.melissa.com/cloud-api/personator-consumer/personator-consumer-reference-guide.html) | [pricing](https://www.melissa.com/pricing/developer) | **unverified** (pages blocked) | |

**Phone validation / line type (independent of the skip vendor):**

* **Trestle IQ.** Phone Validation $0.015 per query (line type, disconnected, activity score); Real Contact $0.03; Reverse Phone $0.07. Free trial of 25 queries per product. Rate limit 10 QPS self-serve. ([pricing](https://trestleiq.com/pricing/), [trial](https://trestleiq.com/knowledge-base/understanding-trestles-free-api-trial-and-the-api-access-request-process-for-specific-products/))
* **Twilio Lookup v2.** Line Type Intelligence $0.008/request; Line Status $0.007; Reassigned Number $0.02. Returns `type`: mobile / fixedVoip / nonFixedVoip / landline … ([pricing](https://www.twilio.com/en-us/user-authentication-identity/pricing/lookup), [docs](https://www.twilio.com/docs/lookup/v2-api/line-type-intelligence))

## B. Sold comps / property data

**What "comps" means matters.** It can be closed sales from recorded deeds, closed sales from the MLS, list prices, or AVM comparables. Only closed sales support an ARV.

**Texas is a non-disclosure state.** Sale prices are not public record, so deed-based feeds have no TX prices. Brokers do report prices to the MLS ([Texas REALTORS](https://www.texasrealestate.com/members/posts/what-non-disclosure-doesnt-mean-for-reporting-prices-to-the-mls/)). For DFW, a sold price comes from one of:

* **MLS-licensed data**
* **a provider's MLS holdings**
* **a modeled price.** EvoSys must label this **estimated**, never "closed sale".

| Vendor | What its "comps" are | Pricing | Terms | TX |
|---|---|---|---|---|
| **ATTOM** | `/sale`, `/saleshistory` (10 yr), `/attomavm` with confidence, mortgage/deed/foreclosure ([docs](https://api.developer.attomdata.com/docs)) | Contact sales; **30-day free key with sandbox** ([intro](https://www.attomdata.com/news/company-news/delivery-solutions/an-introduction-to-attoms-property-data-api/)) | Redistribution/caching **unverified** | Lists TX as non-disclosure; fills gaps with MLS where available, AVM, mortgage and deed data ([ATTOM](https://www.attomdata.com/news/most-recent/do-you-know-what-non-disclosure-states-are/)) |
| **RentCast** | **Listings, not closed sales**: value-endpoint comps are list prices ([schema](https://developers.rentcast.io/reference/property-valuation)); records have `lastSalePrice` | Free 50 req/mo; $74 / $199 / $449 plans ([api](https://www.rentcast.io/api)) | Store as needed; **resale permitted** ([terms](https://www.rentcast.io/terms-api)) | "Sale prices may not be available in non-disclosure states" ([schema](https://developers.rentcast.io/reference/property-data-schema)) |
| **Realie** | **Sold transactions** (`transferPrice`, `transferDate`) by radius and time with sqft/bed/bath filters ([docs](https://docs.realie.ai/api-reference/premium/premium-comparables-search)) | Tokens: free 25; $50 / $150 / $350 plans ([pricing](https://www.realie.ai/pricing)) | **unverified** | Public-source based, so likely **no TX prices** (**unverified**) |
| **HouseCanary** | AVM, forecasts, transaction histories | API from $79/mo; $0.40–$5.00 per call ([pricing](https://www.housecanary.com/pricing)) | **unverified** | **unverified** |
| **BatchData property** | Property, mortgage/liens, pre-foreclosure, AVM ([pricing](https://batchdata.io/pricing)) | $1,000–$10,000/mo | Reseller-addendum rule applies | MLS where available plus mortgage-derived estimates ([blog](https://batchdata.io/blog/non-disclosure-states)) |
| **Regrid** | Parcels / owner / zoning (not comps) | $0.10–$0.15 per record overage; 30-day sandbox ([api](https://regrid.com/api), [plans](https://support.regrid.com/changelog/self-serve-api-plans)) | | |
| **Cotality Trestle (MLS feeds)** | The licensed route to MLS sold data; each MLS must approve, often with a broker ([FAQ](https://trestle-documentation.corelogic.com/faq.html)) | $100–250/mo per MLS plus MLS fees | IDX feeds have **no sold** listings | NTREIS / MetroTex eligibility for a wholesaler SaaS **unverified** |

## C. Imagery (optional)

* **Google Street View Static.** 10k free per month, then $7 per 1,000 ([pricing](https://developers.google.com/maps/billing-and-pricing/pricing)). **No caching** except `pano_id`, and no bulk download ([policies](https://developers.google.com/maps/documentation/streetview/policies), [terms](https://cloud.google.com/maps-platform/terms)). Store `pano_id` only and render live.
* **Mapbox.** 50k free static images per month, then $1 per 1,000 ([pricing](https://www.mapbox.com/pricing)).
* **Esri.** Tiles 2M free per month ([pricing](https://location.arcgis.com/pricing/)).
* **Nearmap.** Subscription; price **unverified**.

## Compliance notes that shape the software (not legal advice)

* **Fifth Circuit, *Bradford v. Sovereign Pest Control* (Feb 2026).** The TCPA requires prior express consent, not "written" consent. Consent is still required ([Holland & Knight](https://www.hklaw.com/en/insights/publications/2026/03/tcpa-reset-fifth-circuit-rejects-prior-express-written-consent-rule)).
* **Texas SB 140 (from Sept 1, 2025).** Texas telephone-solicitation law now covers text messages. Solicitors must register with the Texas Secretary of State ([Morgan Lewis](https://www.morganlewis.com/pubs/2025/09/texas-telephone-solicitation-law-now-covers-text-messages)). **This is a decision for Mike / counsel before any cold texting.** EvoSys keeps cold SMS off.

## Test strategy (no purchases at scale)

1. **Build an authorized sample.** Take 50–100 DFW parcels the business has a lawful reason to research, including a few with known outcomes (EVO's own closed deals: real contract price and the seller's real phone).
2. **Property and comps.** Use ATTOM's 30-day key, RentCast's free tier, Realie's free tokens and Regrid's sandbox. Measure fill rates for price, date, sqft, beds, baths and lat/lng on TX parcels. Tag every comp as deed / MLS / listing / modeled.
3. **Skip trace.** Use EnformionGO (100 free matches per month) and Tracerfy (pay per hit; about 100 traces is roughly $2). Ask BatchData for trial credits. Run everything through the EvoSys **provider evaluation harness** (`evosense/provider_eval.py`), which reports match rate, phone, mobile and email coverage, cost per usable contact and more, and labels results REAL vs SYNTHETIC.
4. **Independent line-type check.** Run returned numbers through Trestle IQ (25 free) and Twilio Lookup ($0.008 each) to measure each skip vendor's line-type accuracy **without dialing anyone**.
5. **Before signing, get these in writing:** multi-tenant redistribution rights, cache retention, and how TX sale prices are sourced.

**What each category unlocks in EvoSys:**

* **A** unlocks: owner contact → candidate contacts → confidence → contactability. The contact still needs consent before SMS.
* **B** unlocks: evidence-backed ARV, and so a gated MAO.
* **C** is cosmetic and diligence support.
