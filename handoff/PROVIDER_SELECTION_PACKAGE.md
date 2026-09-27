# Provider selection package: contact/skip trace and sold comps/property data

**Version 3**, 2026-09-27. Replaces version 2; pricing updated (section 4) and the harness/adapters are now built (section 11).

**This is a decision package, not a purchase.** Nothing has been bought, signed up for, configured or activated. No vendor was contacted. No real owner was contacted.

**How each fact is marked:**

* **CONFIRMED:** the vendor's own page says it, and it is linked.
* **SALES:** needs written confirmation from the vendor's sales team.
* **UNKNOWN:** not found, or the page would not load.

Research was done through a web fetcher. Some vendor pages were blocked or returned only headers; those are listed at the end. Quote any contract term only after re-reading the live page.

---

## 0. Where EvoSys stands today (production)

| Capability (registry) | State today | What stays true until Mike approves a provider |
|---|---|---|
| PHONE / EMAIL / LINE_TYPE | **MANUAL ONLY** | Contacts come only from manual entry or CSV. A phone is never consent. |
| PHONE_VALIDATION | NOT CONFIGURED | No line-type checks are run. |
| SOLD_COMPS | **NOT CONFIGURED** (manual comps only) | ARV = INSUFFICIENT COMPARABLE SALES unless a person enters eligible sold comps with a source. MAO stays gated. |
| VALUATION (AVM) | MANUAL ONLY | Never used as ARV. |
| OWNER_IDENTITY / PROPERTY_FACTS / TAX / CODE_VIOLATION / GEOCODING | OPERATIONAL | Real public sources: DCAD export, Tarrant tax roll, Fort Worth and Dallas 311 code data, Census geocoder. |
| Tarrant Appraisal District | BLOCKED | The source refuses our servers. |

**The Command Center right now:**

* **35 real DFW properties** are *awaiting contact data* (contactability = ENRICHMENT NEEDED).
* PHONE and SOLD_COMPS are flagged "provider-blocked".

---

## 1. What EvoSys needs from a provider

### A. Contact / skip trace: to unlock the 35 waiting properties

| Requirement | Why EvoSys needs it | How the framework already uses it |
|---|---|---|
| Owner/person matching from owner name + property/mailing address | The owner of record comes from DCAD. The vendor must find **that** person, not a resident or relative. | `EnrichmentInput` (name, property address, mailing address) → adapter `enrich()` |
| Phones, each with a **line type** (mobile/landline/VoIP) | Mobile vs landline decides the channel. Landline blocks SMS. | `EnrichmentPhone.phone_type` → contact point `line_type` |
| Emails | Second channel. Discovered-owner email stays **off** until Mike enables it. | `EnrichmentEmail` |
| Validation (connected/disconnected) | Avoids paying to reach dead numbers. | `validate_phone()` (PHONE_VALIDATION), separate budget line |
| Confidence / match info (score, rank, match evidence) | Candidate → trusted needs evidence. | `match_evidence`, `provider_confidence` → Contact Confidence → `trust_state` |
| Freshness (first/last seen) | Old numbers are wrong-party risk. | `source_last_seen` |
| Provider record/reference id | Provenance and disputes ("where did this number come from?"). | `provider_reference` |
| DNC / litigator flags | A block signal only, never permission. | `dnc_flag` → eligibility block |
| REST API, server-to-server | EvoSys calls per property from the backend. | Adapter class in `evosense/providers.py` |
| Batch + async/webhook | Nice to have for 35–1,000 properties. Sync is enough for the pilot. | Hunt runs per property today. Batch is an adapter optimisation. |
| Rate limits | Fit inside budgets and the scheduler. | Budget reserve → charge/refund per call |
| Pricing per hit, with **misses free** | Economics per usable contact. | Cost ledger, per tenant, per property |
| Right to **store** results per tenant and show them in a **multi-tenant white-label** product | Without it EvoSys cannot keep the contact on the property. | Contact points are stored per tenant with provenance |
| Clear permissible-purpose terms (no FCRA use; GLBA/DPPA handling) | Legal footing for owner outreach research. | Contract level |

### B. Sold comps / property data: to unlock legitimate ARV

| Requirement | Why | How the framework already uses it |
|---|---|---|
| **Actual CLOSED sale price** and **closing date** | The only valid ARV input. | `WholesaleComp.sale_price`, `sale_date` → `comp_rules` |
| Clear distinction: **MLS closed / deed-recorded / estimated / list / AVM** | Texas is non-disclosure. A modeled price must never pose as a sale. | `provider_key`, `verification_state`, `provenance`. An estimated price would be refused by the eligibility rules. |
| Property facts: sqft, beds, baths, year built, lot, lat/lng, type | Similarity rules and $/sqft. | Comp rules (distance, size ±25%, beds/baths ±1, year ±20) |
| Sale type / arm's-length flags (REO, foreclosure, family, quitclaim) | Non-arm's-length sales are excluded. | `sale_type` → `SALE_TYPE` exclusion |
| Comp search: radius, date window, similarity filters | One call per subject property. | Adapter `search(COMPS, subject)` |
| **Texas/DFW closed-price coverage** | Pilot market. | The critical test metric. |
| Freshness (days from closing to availability) | Comps must be recent (default 12 months). | `retrieved_at`, `TOO_OLD` rule |
| Licensing: store comps per deal, show them to tenants under their own brand | The deal page shows each comp and why it counts. | Comps are stored per deal |
| Provider reference id per sale (MLS # / document #) | Evidence a second person can check. | `source_reference` (required for manual comps) |

---

## 2. Contact / skip-trace candidates (side by side)

| | BatchData | Tracerfy | RealEstateAPI (REAPI) | DataSkip | EnformionGO | Trestle IQ | Twilio Lookup v2 |
|---|---|---|---|---|---|---|---|
| **What it is** | Skip trace + property data | Skip trace | Skip trace + property + comps | Skip trace | People-data API | Phone intel / reverse address | **Line type only** |
| **Inputs** | Property address; ≤100 properties/request. V1 returns 1 contact, V3 up to 3. **CONFIRMED** ([doc](https://developer.batchdata.com/docs/batchdata/batchdata-v1/operations/create-a-property-skip-trace)) | Address (+ optional name); APN batch. **CONFIRMED** ([doc](https://www.tracerfy.com/skip-tracing-api-documentation/)) | Name, phone, email, mailing and/or property address; `exact_match`. **CONFIRMED** ([doc](https://developer.realestateapi.com/reference/skiptrace-api)) | Property address. **CONFIRMED** ([page](https://dataskip.io/skip-tracing-api)) | Any 2 of name/phone/address/email. **CONFIRMED** ([doc](https://enformiongo.readme.io/reference/contact-enrichment)) | Phone, or address (reverse address). **CONFIRMED** ([docs](https://docs.trestleiq.com/guides/overview)) | Phone number. **CONFIRMED** |
| **Phones + line type** | Mobile/landline flag, carrier. **CONFIRMED** ([page](https://batchdata.io/skip-tracing)) | type, carrier, rank. **CONFIRMED** | `phoneType`, `isConnected`, `lastSeen`. **CONFIRMED** ([schema](https://developer.realestateapi.com/reference/skiptrace-api.md)) | Up to 10 mobiles and 9 landlines. **CONFIRMED** | Top 5 phones: type, isConnected, first/last reported. **CONFIRMED** | Line type, carrier, activity score. **CONFIRMED** | mobile / landline / fixedVoip / nonFixedVoip… **CONFIRMED** ([doc](https://www.twilio.com/docs/lookup/v2-api/line-type-intelligence)) |
| **DNC / litigator** | DNC + litigator scrub. **CONFIRMED** | DNC + TCPA litigator flag. **CONFIRMED** | `doNotCall`. **CONFIRMED**; litigator **UNKNOWN** | DNC flag. **CONFIRMED**; litigator **UNKNOWN** | Does **not** scrub DNC. **CONFIRMED** ([terms](https://go.enformion.com/terms/)) | Litigator add-on +$0.005. **CONFIRMED** | n/a |
| **Emails** | Yes. **CONFIRMED** | Yes, ranked. **CONFIRMED** | Yes, with type. **CONFIRMED** | Yes. **CONFIRMED** | Yes. **CONFIRMED** | Email add-on. **CONFIRMED** | n/a |
| **Confidence / match** | "Phone confidence score"; field **UNKNOWN** | `rank` only | Boolean `match` (no score). **CONFIRMED** | **UNKNOWN** | `identityScore`. **CONFIRMED** | Activity score. **CONFIRMED** | n/a |
| **Freshness** | **UNKNOWN** | **UNKNOWN** | `lastSeen`. **CONFIRMED** | **UNKNOWN** | first/last reported dates. **CONFIRMED** | activity score | n/a |
| **Provider reference id** | **UNKNOWN** | Batch queue id only | `requestId`, `personId`. **CONFIRMED** | **UNKNOWN** | `tahoeId`. **CONFIRMED** | **UNKNOWN** | n/a |
| **API / trial** | REST; sandbox **UNKNOWN** | REST, Bearer; **no sandbox** | REST, `x-api-key`; sandbox **UNKNOWN** | REST, Bearer | REST (header keys); **100 free matches/month**. **CONFIRMED** | REST; **free trial, no card**. **CONFIRMED** | REST |
| **Batch / async** | Async variant. **CONFIRMED** ([doc](https://developer.batchdata.com/docs/batchdata/batchdata-v1/operations/create-a-property-skip-trace-async)); webhook **UNKNOWN** | Async batch + webhook. **CONFIRMED** | Bulk 10–1,000 with per-item and completion webhooks. **CONFIRMED** ([doc](https://developer.realestateapi.com/reference/bulk-skiptrace-api)) | 100 addresses/request. **CONFIRMED** | "Batch & API" plans; mechanics **SALES** | — | — |
| **Rate limits** | **UNKNOWN** | 500/min single; batch 10 POSTs/5 min. **CONFIRMED** | 10 rps; 1M matches/day. **CONFIRMED** ([doc](https://developer.realestateapi.com/reference/rate-limiting-1)) | 500/min single, 250/min bulk. **CONFIRMED** | **UNKNOWN** | 10 QPS self-serve, 32 enterprise. **CONFIRMED** | **UNKNOWN** |
| **Pricing** | Plans $2,000/mo (100k) to $20,000/mo (3M). **CONFIRMED** ([pricing](https://batchdata.io/pricing)). PAYG per match and miss-free: **SALES** | **Normal Trace $0.02/hit, Advanced $0.04/hit, misses free; API included at the same per-hit pricing. CONFIRMED** ([pricing](https://www.tracerfy.com/pricing)). The **instant (single) API lookup is 5 credits = $0.10 per hit**, 0 on a miss; the batch trace is 1 (normal) or 2 (advanced) credits per lead. **CONFIRMED** ([API docs](https://www.tracerfy.com/skip-tracing-api-documentation/)) | Reported on REAPI's official pricing page: **$0.10/property PAYG; skip trace $0.05/match with a property-data subscription; Starter $599/mo, Growth $1,200/mo, Pro $2,500/mo; Pro positioned for consumer-facing SaaS** (reviewed by Mike's research 9/27; our fetcher could not render the page, so treat as **reported, re-confirm**) | **$0.04 per match, misses free**, no subscription. **CONFIRMED** | From $0.25/match (Pro as low as $0.01, custom); pay only for matches. **CONFIRMED** ([pricing](https://go.enformion.com/pricing/)) | Phone Validation $0.015; Reverse Address $0.07; Real Contact $0.03. **CONFIRMED** ([pricing](https://trestleiq.com/pricing/)) | $0.008/request. **CONFIRMED** ([pricing](https://www.twilio.com/en-us/user-authentication-identity/pricing/lookup)) |
| **Minimums** | Fees non-refundable (§3.7a). **CONFIRMED**; plan minimum = plan | None; start at $20; plans from $700/mo. **CONFIRMED** | **SALES** | None. **CONFIRMED** | None (Starter). **CONFIRMED** | None (self-serve) | None |
| **Storage / retention** | Refresh caches at least every **30 days**; delete/suppress on notice; ≤3% of database. **CONFIRMED** ([ToS](https://batchdata.io/terms-of-service) §6.20, §6.2) | **UNKNOWN** (terms page 404) | **UNKNOWN** (terms 404); responses carry a `cached` flag | Terms have **no** storage, resale or permissible-use clauses at all ([terms](https://dataskip.io/terms)) → **SALES** | **May not store/cache to avoid new inquiries.** **CONFIRMED** | **May not store/cache to avoid new queries**; merged data must be tagged as Trestle. **CONFIRMED** (§3.3.2) | **UNKNOWN** |
| **Multi-tenant / resale** | Internal use, non-sublicensable; resale needs a **Reseller Addendum** (VP-signed). **CONFIRMED** (§6.25); addendum terms **SALES** | White-label / API partnerships **explicitly offered by contract. CONFIRMED** ([FAQ](https://www.tracerfy.com/faqs)); the actual storage, redistribution and tenant rights **must be confirmed in writing before commercial rollout (SALES)** | **UNKNOWN / SALES** | **UNKNOWN / SALES** | No disclosing, sublicensing or reselling without written consent. **CONFIRMED** | Written consent needed; restrictions flow down. **CONFIRMED** (§3.3.1). **No marketing use except responding to inbound requests** (§3.3.2), which conflicts with outbound wholesaling | **UNKNOWN** |
| **FCRA / GLBA / DPPA** | No FCRA use (§6.13b); DPPA §2721(b) only; GLBA clause; customer owns TCPA/DNC. **CONFIRMED** | **UNKNOWN** | Docs mention "FCRA/TCPA" tools; terms **UNKNOWN** | **UNKNOWN** | Not a CRA; GLBA/DPPA not mentioned. **CONFIRMED** | Not a CRA; no people-search. **CONFIRMED** | n/a |

**Ruled out:**

* **PropertyRadar.** "The PropertyRadar API is intended for end-users only – you can not use it to build applications you sell to others." **CONFIRMED** ([developers](https://developers.propertyradar.com/))
* **DirectSkip.** No public API found.
* **PropStream / REISkip.** No public developer API found.
* **Melissa Personator.** Every page was blocked, so everything is **UNKNOWN**. Re-check if wanted.

**Read-across:**

* **BatchData and Tracerfy** are the only two with a *documented* written path to multi-tenant resale.
* **REAPI** has the most complete documented response fields (line type, connected, last seen, person id, bulk webhooks), but its **terms and pricing are unknown**.
* **EnformionGO and Trestle** currently forbid caching results to avoid new queries, and forbid disclosure without consent. That is incompatible with storing contacts per tenant unless they grant written consent. Trestle's inbound-only marketing clause is a further conflict.
* **Twilio Lookup** is a clean, independent line-type check. It is also useful as the *referee* in a test.

---

## 3. Sold comps / property-data candidates (side by side)

**The Texas fact that decides this category.** Texas is a non-disclosure state, so deeds carry no price. NTREIS/MetroTex rules *do* require brokers to report sale prices to the MLS **CONFIRMED** ([MetroTex](https://www.mymetrotex.com/fact-or-fiction-must-i-report-sales-prices-to-the-mls/)). So **the only source of actual DFW closed prices is NTREIS MLS data**, obtained directly (via Cotality Trestle) or through a vendor that licenses it. Anything else is an estimate, a list price or an AVM, and EvoSys will not use it as an ARV.

| | NTREIS via Cotality Trestle | REAPI Comps | ATTOM | BatchData property | RentCast | Realie | HouseCanary | First American DataTree | Repliers |
|---|---|---|---|---|---|---|---|---|---|
| **Closed prices?** | **Yes**: IDX Plus / VOW / back-office feeds carry sold listings up to 7 yrs with close date. **CONFIRMED** ([Trestle PDF](https://trestle.corelogic.com/Content/Trestle%20for%20Technology%20Providers%20Release%20v3.pdf)) | Separate `lastSaleAmount` (record) and **`mlsSoldPrice`** / `mlsLastSaleDate`. **CONFIRMED** ([schema](https://developer.realestateapi.com/reference/v3-comps-response-object.md)); MLS is a premium add-on | /sale, /saleshistory (10 yr), AVM with confidence. **CONFIRMED** ([docs](https://api.developer.attomdata.com/docs)) | "Active, sold and expired listing data" add-on. **CONFIRMED** ([page](https://batchdata.io/api-solutions)) | **No**: public-record sales; listings are **list prices**; AVM comps use listings. **CONFIRMED** ([docs](https://developers.rentcast.io/reference/property-listings)) | Transfer price/date comps. **CONFIRMED** ([blog](https://blog.realie.ai/blog/introducing-realie-comparable-property-search-api-endpoint)); source **UNKNOWN** | AVM, comps, transactions; **no live MLS**. **CONFIRMED** ([blog](https://www.housecanary.com/blog/real-estate-data-api-for-developers)) | Comps report with sold price/date from "MLS, public records". **CONFIRMED** ([sample](https://dna.firstam.com/solutions/property-data/property-reports/sales-comparables-report-sample)) | MLS API reseller; NTREIS listed. **CONFIRMED** ([page](https://repliers.com/ntreis/)) |
| **TX / DFW closed prices** | **Yes**, this is the source | `mlsSoldPrice` fill rate for NTREIS: **SALES** (the key question) | TX = "**estimate** the sales value". **CONFIRMED** ([ATTOM](https://www.attomdata.com/data/transactions-mortgage-data/estimated-sales-price/)); whether estimates are flagged: **UNKNOWN** | Estimates from mortgage/LTV; MLS "where accessible". **CONFIRMED** ([blog](https://batchdata.io/blog/non-disclosure-states)); TX sold fill: **SALES** | "Sale prices may not be available in non-disclosure states". **CONFIRMED** | **SALES** (likely none, if public-record) | "Coverage excludes some non-disclosure states"; suggests discounting list prices. **CONFIRMED** | **SALES** | Via NTREIS approval only |
| **Source field (MLS vs record vs estimate)** | MLS by definition | Only by field name (`mls*` vs `lastSale*`) | **UNKNOWN** | **UNKNOWN** | n/a | **UNKNOWN** | n/a | **UNKNOWN** | MLS |
| **Arm's-length / sale type** | MLS status fields (**SALES**) | `armsLength`, `preForeclosure`, `cashBuyer`. **CONFIRMED** | **UNKNOWN** | **UNKNOWN** | **UNKNOWN** | **UNKNOWN** | **UNKNOWN** | **UNKNOWN** | MLS fields |
| **Comp search filters** | Query your own replica | Radius 0.1–100 mi, days back, beds/baths, sqft/lot/year weights, census tract, arm's-length. **CONFIRMED** | /salescomparables filters **UNKNOWN** | **UNKNOWN** | radius, daysOld, compCount (listings). **CONFIRMED** | Radius, ≤18 mo, sqft, beds/baths, type, ≤50. **CONFIRMED** | **UNKNOWN** | Radius, timeframe, ≤50 (report). API **SALES** | MLS search |
| **Freshness** | RESO Web API; updates in <2 min. **CONFIRMED** ([Cotality](https://www.cotality.com/products/trestle)) | **UNKNOWN** | **UNKNOWN** | "Daily". **CONFIRMED** | Weekly; county lag of weeks to months. **CONFIRMED** | **UNKNOWN** | **UNKNOWN** | **UNKNOWN** | Near-real-time (MLS) |
| **Trial / sandbox** | No cost until an MLS approves a connection. **CONFIRMED** | **UNKNOWN** | **30-day free key**. **CONFIRMED** | **SALES** | Free 50 calls/mo. **CONFIRMED** | Free 25 tokens. **CONFIRMED** | Test keys. **CONFIRMED** | **SALES** | **UNKNOWN** |
| **Pricing** | Monthly per connection (Trestle) + MLS fees: **SALES** | 1 credit per comp (PAYG) or per subject (Starter+); comps need Growth tier+; $: **SALES** | **SALES** | $1k–$10k/mo; listings and valuation are add-ons. **CONFIRMED** ([pricing](https://batchdata.io/pricing)) | $0 / $74 / $199 / $449 per month. **CONFIRMED** ([api](https://www.rentcast.io/api)) | $0 / $50 / $150 / $350 per month. **CONFIRMED** ([pricing](https://www.realie.ai/pricing)) | From $79/mo; $0.40–$5/call. **CONFIRMED** | **SALES** | From $199/mo. **CONFIRMED** |
| **Storage / display** | MLS rules; the 2002 NTREIS rules barred showing aggregated sold data on consumer sites and passing it to third parties (**dated; current rules SALES**) | **UNKNOWN** | Trial terms: no third-party products, **no caching over 24 h**. **CONFIRMED** ([legal](https://api.developer.attomdata.com/legal)); production **SALES** | Reseller Addendum rule applies (see A) | Terms reportedly allow sublicense/display/resale. **CONFIRMED, re-read** ([terms](https://www.rentcast.io/terms-api)) | Redistribution negotiated separately. **CONFIRMED** | **UNKNOWN** | **SALES** | Building your own product on MLS data **not allowed**; you need MLS approval. **CONFIRMED** ([help](https://help.repliers.com/en/article/mls-data-access-requirements-who-can-use-mls-apis-wyl8lw/)) |
| **Multi-tenant white-label** | **SALES**; NTREIS may decline a non-broker vendor; likely needs a broker sponsor | **SALES** | **SALES** (trial terms prohibit) | **SALES** (addendum) | Appears permitted, but no TX closed prices | **SALES** | **SALES** | **SALES** | No (per its own rules) |

**Not candidates for ARV:**

* **Regrid.** Parcels only; no TX prices.
* **Zillow / Bridge.** MLS feeds need MLS approval. Zillow public-records API is invite-only; no storage.
* **PropertyRadar.** End-user only.
* **Cotality Realist.** MLS members only.
* **Estated.** Now ATTOM.

**Read-across:**

1. For **DFW actual closed prices** there are two realistic routes:
   * **(a) NTREIS data directly or with a broker sponsor** (Trestle). The legitimate source, but slow and its licensing is uncertain for a non-broker SaaS.
   * **(b) A data vendor that holds NTREIS sold data** and will say so in writing. REAPI's `mlsSoldPrice` field and BatchData's sold-listing add-on are the two documented candidates; both need their **TX fill rate** and **redistribution rights** confirmed.
2. ATTOM, RentCast, Realie, HouseCanary and BatchData's own estimates will mostly give **estimates, list prices or AVMs in Texas**. EvoSys can store those as reference values, **never** as ARV.

---

## 4. Pricing: confirmed vs unknown

| Vendor | Confirmed pricing | Needs sales |
|---|---|---|
| Tracerfy | Normal Trace $0.02/hit, Advanced $0.04/hit, misses free, API included; instant API lookup 5 credits ($0.10)/hit, 0 on a miss; batch 1–2 credits per lead; plans $700 / $1,500 / $3,000 per month | Whether batch credits are per uploaded lead or per hit (the docs say "per lead", the pricing page says misses are free) |
| DataSkip | $0.04/match, misses free, no subscription | — |
| EnformionGO | 100 free matches/mo; from $0.25/match; Pro to $0.01 | Batch pricing |
| BatchData | Plans $2,000–$20,000/mo (skip); $1k–$10k/mo (property) | PAYG per match; miss charging; reseller addendum cost |
| REAPI | Reported: $0.10/property PAYG; skip $0.05/match with a subscription; Starter $599 / Growth $1,200 / Pro $2,500 per month (not machine-verified here) | Written quote; which tier includes Comps and the MLS add-on; white-label/redistribution; DFW `mlsSoldPrice` fill rate |
| Trestle IQ | $0.015 validation; $0.03 Real Contact; $0.07 reverse address | Whether misses are charged |
| Twilio Lookup | $0.008 line type; $0.007 line status; reassigned from $0.02 down | — |
| ATTOM | 30-day free key | Everything production |
| RentCast | $0/$74/$199/$449 per month; $0.20→$0.015 overage | — |
| Realie | $0/$50/$150/$350 per month | Bulk / redistribution |
| HouseCanary | From $79/mo; $0.40–$5/call | — |
| NTREIS/Trestle | No cost until an MLS approves a connection | Connection + MLS fees |
| Repliers | From $199/mo | — (but not permitted for our use) |

---

## 5. Licensing / SaaS questions each vendor must answer in writing

**For every candidate:**

1. May EvoSys (the platform) **store** results per customer workspace, and for how long? Is a cache refresh period required (BatchData: 30 days)?
2. May EvoSys **show** results to its customers under the **customer's own brand** (white-label), and do customers need their own agreement?
3. Is a **reseller / redistribution addendum** required? What does it cost, and what flows down to our customers?
4. Is our use a **permissible purpose**: identifying and contacting property owners for a potential purchase, not FCRA uses? What GLBA/DPPA obligations flow down?
5. Must data be **deleted or suppressed** on request, and how is that notified (API/webhook)?
6. Are **misses free**? Is there a minimum commitment, and are credits refundable?

**Skip-trace specific:**

* **Tracerfy:** the miss-charging conflict; a per-record id.
* **REAPI:** terms; litigator flag.
* **EnformionGO / Trestle:** written consent to store and re-serve. Is outbound owner contact "marketing" (Trestle §3.3.2)?

**Comps specific:**

1. For **2025–26 DFW closings**, what share of your sold records carry an **actual MLS closed price** (not an estimate)?
2. Is there a **field** marking each price as MLS / deed-recorded / estimated?
3. Is a **sale type / arm's-length** field included?
4. Do you hold an **NTREIS licence** that permits redistribution to a SaaS? If not, where do your TX prices come from?
5. **NTREIS/MetroTex directly:** will it approve a Trestle connection for a **non-broker SaaS**? What broker sponsorship, fees and display rules (sold data behind login only?) apply?

---

## 6. Proposed controlled test (no purchase commitment)

### Skip trace

**Sample.**

* **The 35 real DFW properties** now awaiting contact data (owner of record known from DCAD).
* **Plus 10–15 "truth" records:** properties where the true owner's phone is already known lawfully. Examples: Mike's own property; people who have consented; EVO's future closed deals.
* 45–50 total. Every lookup is research on a property owner; **no one is contacted**.

**Cost estimate at published prices, for 50 properties:**

| Vendor | Estimate |
|---|---|
| EnformionGO | $0 (within 100 free) |
| Tracerfy | ≤ $5 (≤$0.10 per hit) |
| DataSkip | ≤ $2 |
| Twilio line-type referee | ~$1.20 (~150 numbers) |
| REAPI / BatchData | trial credits: **SALES** |

**Total under about $10** for the self-serve vendors.

**How the harness runs it.** `evosense/provider_eval.py`, admin-only:

* It runs every vendor on the **same sample**, through the tenant budget and cost ledger.
* It is labelled **REAL**, and requires Mike's typed confirmation `RUN PAID EVALUATION <n>`.
* It **writes no contact points**, so nothing from the test can reach outreach.

**Metrics per vendor** (the harness computes these):

* match rate
* correct-owner rate (truth set)
* phone / **mobile** / email coverage
* line-type quality (share typed)
* false-positive rate (numbers already known wrong-party / opted-out / invalid, or contradicting the truth set)
* median last-seen age
* latency
* cost per lookup / usable contact / verified contact
* failures

**Additions to run alongside:**

* Twilio line type on every returned number, as the referee for mobile accuracy.
* A name-agreement check against the DCAD owner of record.

**Suggested thresholds** (proposals for Mike to set):

| Metric | Worth a contract | Reject |
|---|---|---|
| Match rate | ≥ 70% | < 50% |
| Correct owner (truth set) | ≥ 85% of matches | < 70% |
| Usable mobile per property | ≥ 50% | < 30% |
| Line-type agreement with Twilio | ≥ 90% | < 80% |
| False positives (truth set) | ≤ 10% | > 20% |
| Cost per usable contact | ≤ $0.50 | — |

### Sold comps

**Sample.**

* 30 DFW subject properties from the EvoSense inbox (Dallas + Tarrant; mixed ages and sizes).
* Plus 5–10 with **known recent MLS closings** (from a broker or MLS printout Mike can lawfully obtain) as ground truth.

**Vendors to test:**

* ATTOM (30-day key)
* Realie (25 free tokens)
* RentCast (free 50; expected to show *no* TX closed prices, which is a useful control)
* REAPI and BatchData if they grant trial access with the MLS add-on

**Metrics:**

* share of returned comps with an **actual closed price** (vs estimate/list/AVM)
* closed-price accuracy vs ground truth
* close-date recency
* property-fact completeness (sqft/beds/baths/year/lat-lng)
* share of subjects reaching **≥3 eligible comps** under EvoSys's own `comp_eligibility/v1` rules
* ARV confidence distribution
* latency
* cost per subject

**Suggested thresholds:**

* ≥ 80% of returned DFW comps carry a documented MLS closed price with a source field.
* ≥ 70% of subjects reach ≥ 3 eligible comps (1 mi / 12 mo).
* Price matches ground truth exactly.

**Comps mode is built** (section 11): `search(COMPS)` per subject, every comp scored in memory by `comp_eligibility` + the ARV engine, nothing written to deals, SYNTHETIC-tested with the sandbox comps adapter and against REAPI's documented response shape.

---

## 7. Can one provider cover both?

**Documented on paper:**

* **REAPI** documents both: SkipTrace (phones with line type, lastSeen, personId, bulk webhooks) and Comps with separate `mlsSoldPrice`. It would cover contact enrichment, property facts and possibly DFW closed prices. **Unknown:** terms, pricing, TX MLS fill.
* **BatchData** documents skip trace + property + a sold-listing add-on. **Its own TX method is estimates from mortgage data**; resale needs the Reseller Addendum.

**Don't force it.** The best DFW closed-price data may come only from NTREIS (directly, or via a vendor that licenses it). The cheapest good contact data may come from a pay-per-hit specialist (Tracerfy / DataSkip), plus Twilio for line type. Two specialised providers may well beat one on both data and cost. Test a combined vendor **and** the specialists on the same sample.

---

## 8. Architectural fit (per serious candidate)

All adapters are subclasses of `AcquisitionProvider` in `evosense/providers.py`, registered in `PROVIDERS`. They are routed by capability, budgeted per call (reserve → charge/refund), written to the cost ledger per tenant/property, and put behind the kill switch and the per-tenant enable. The Capability Registry turns a capability **OPERATIONAL only after a real call for that capability succeeds**.

| Candidate | Adapter work | Registry capabilities it would turn operational | Credentials | Metering | Caching / freshness | Multi-tenant licensing |
|---|---|---|---|---|---|---|
| Tracerfy | `enrich()` via sync lookup; async batch later. ~1 day | PHONE, EMAIL, LINE_TYPE (vendor-typed) | Platform-managed env key (the registry's `required_env` model) | Per hit, misses free → ledger charge on match only | Freshness cache; retention **SALES** | White-label contract **SALES** |
| DataSkip | `enrich()`. ~½–1 day | PHONE, EMAIL, LINE_TYPE | Platform | Per match | **SALES** | Terms not reviewed |
| REAPI | `enrich()` (SkipTrace) + `search(COMPS)` + property detail. ~2–3 days | PHONE, EMAIL, LINE_TYPE, OWNER_IDENTITY, PROPERTY_FACTS, **SOLD_COMPS** (if MLS add-on), VALUATION (as AVM) | Platform | Credits per comp/subject | `cached` flag in responses; retention **SALES** | **SALES** |
| BatchData | `enrich()` + property/listing. ~2 days | PHONE, EMAIL, LINE_TYPE, PROPERTY_FACTS, MORTGAGE/LIEN, FORECLOSURE, maybe SOLD_COMPS | Platform + **Reseller Addendum** | Subscription records → ledger per record | **Refresh ≤ 30 days** (a hard rule; the freshness cache must enforce it) | Addendum required |
| EnformionGO / Trestle IQ | `enrich()` / `validate_phone()`. ~1 day each | PHONE/EMAIL (Enformion); PHONE_VALIDATION, LINE_TYPE (Trestle) | **BYOK** would suit them: each tenant signs its own agreement, sidestepping the resale ban. BYOK credential storage per tenant is **not built yet** | Per match / per query | **No caching to avoid queries**: display the result, re-query rather than reuse | Written consent needed |
| Twilio Lookup | `validate_phone()`. ~½ day. Twilio credentials already exist platform-wide | PHONE_VALIDATION, LINE_TYPE | Platform (existing Twilio account; **not** activating messaging) | $0.008/number | Short cache (line type drifts) | Low concern |
| ATTOM | `search(COMPS)`, `/sale`, AVM. ~2 days | PROPERTY_FACTS, DEED, MORTGAGE, FORECLOSURE, VALUATION (AVM); SOLD_COMPS **only if TX closed prices are real and flagged** | Platform | Per call / contract | Trial: **≤ 24 h cache**; production **SALES** | **SALES** |
| NTREIS via Trestle | RESO Web API replica + local comp search. ~3–5 days | **SOLD_COMPS** (DFW), LISTING_HISTORY | Per-MLS connection; likely tied to a broker participant | Monthly per connection (not per call) | Replicated, near-real-time | MLS display rules (sold data behind login) |

**Tenant metering is already in place:**

* Every paid call is attributed to a tenant and property in the ledger.
* Budgets and policy tiers (free-only / standard / aggressive / manual approval) apply per strategy.
* Pass-through pricing to tenants is a billing decision, **not yet built**.

---

## 9. What stays true until Mike approves a provider (enforced in code today)

* Phone / email / line type: **MANUAL ONLY**. Sold comps: **manual only**.
* ARV: **INSUFFICIENT COMPARABLE SALES** wherever evidence is insufficient. Tax values and AVMs are never ARV.
* MAO: **gated**, NOT CALCULATED without an evidence-backed ARV and an accepted repair estimate.
* No synthetic or simulated data outside test records. Sandbox adapters run only on test properties.
* No real-owner outreach. Cold SMS off; discovered-owner email off. A phone is never consent.

## 10. Compliance notes (not legal advice)

* **Fifth Circuit, *Bradford v. Sovereign Pest Control* (Feb 2026).** The TCPA requires prior express consent, not "written" consent. Consent is still required ([Holland & Knight](https://www.hklaw.com/en/insights/publications/2026/03/tcpa-reset-fifth-circuit-rejects-prior-express-written-consent-rule)).
* **Texas SB 140 (from Sept 1, 2025).** Telephone-solicitation law covers texts. Solicitors register with the Texas Secretary of State ([Morgan Lewis](https://www.morganlewis.com/pubs/2025/09/texas-telephone-solicitation-law-now-covers-text-messages)). **This is a decision for Mike / counsel before any cold texting.**

## 11. Evaluation harness and adapters (built 2026-09-27; nothing called)

| Component | Status |
|---|---|
| **Adapters** (`evosense/vendors.py`), built from each vendor's published API reference | `tracerfy` (instant address trace), `reapi_skiptrace` (/v1/SkipTrace), `dataskip` (/api/v1/skip-trace), `twilio_lookup` (Lookup v2 line type - a lookup, never a message), `reapi_comps` (/v3/PropertyComps), `sandbox_comps` (synthetic, test records only) |
| **EVALUATION ONLY** | Every commercial adapter is excluded from production routing (`route()`), shows **EVALUATION ONLY** in the registry and Providers screen, and can be called by the evaluation harness alone. Making one a production provider is a one-line change that is Mike's decision. |
| **Contact mode** | Same sample through each vendor; optional independent line-type referee; metrics: match rate, correct owner (ground truth), owner-name agreement with the DCAD owner (proxy, labelled), phone/mobile/email coverage, line-type completeness and agreement, freshness, false positives, latency, failures, cost per lookup / matched owner / usable / verified contact |
| **Comps mode** | Calls `search(COMPS)` and runs every comp through `comp_eligibility` → ARV engine → confidence **in memory**; classifies each price as MLS CLOSED / PUBLIC RECORD / UNVERIFIED RECORD (non-disclosure state) / ESTIMATED / LIST / AVM. Only a closed sale can count (new rule `PRICE_NOT_CLOSED_SALE`). A Texas record price of unknown origin never counts. Every comps provider reports `production_ready: false` until its Texas closed-price origin is confirmed in writing. |
| **Isolation** | Nothing returned becomes a contact point, comp, lead or deal value; nothing is sent; contactability untouched. Returned values are shown to admins only and can be **deleted** ("Delete returned data"), keeping the metrics. |
| **Money - the approval gate** | **Plan first:** a plan prices every call (per provider, referee included) and shows the **maximum spend before anything runs**; nothing is called. A paid plan runs only with `RUN PAID EVALUATION <evaluation id>` and **never spends beyond its planned maximum** (a call that would exceed it is recorded "not attempted - authorized maximum reached"). Every call is reserved against the workspace budget and settled to what the vendor actually billed; hits charged / hits free / misses / misses charged / refunded are counted per provider. |
| **Isolation guard** | Before and after every run, a fingerprint of each sample property (Property Opportunity, Contact Confidence, Seller Intent, contactability, status, data confidence) and of the workspace's contacts, leads, deals, comps, engagements and scores is compared. Any change is recorded as an ISOLATION VIOLATION, the property values are restored, and the evaluation is marked failed. Tested: NEEDS YOU and the Morning Command Center counts are identical before and after a run. |
| **Ground truth** | Only what the workspace already lawfully knows, entered by an admin **with how it is known**: known owner contacts (per property) and known closed sales (address, price, date). Scores correct-owner rate and comp price accuracy. Never inferred; deletable. |
| **DFW truth gate** | For every comps provider: PRICE_MEANING, CLOSED_SALE, ORIGIN (MLS or public record only), STORAGE_RIGHT, DISPLAY_RIGHT are **attested with evidence** by an admin; COVERAGE (≥70% of ≥10 Dallas/Tarrant subjects reach 3 eligible comps and ≥80% of comps carry a closed price) and FRESHNESS (median close age ≤ 270 days) are **measured** from a completed REAL evaluation. Until all are met, `route()` sends no comps call and the registry shows TRUTH GATE NOT MET. Passing it activates nothing by itself. |
| **Report** | Provider by provider on the same records - "X% correct owner, X% usable mobile, X% usable email, $X per usable contact" (and the comps equivalent) - with notes; **no winner is declared**. |
| **Screen** | EvoSense → Providers & Controls → **Provider Evaluation** tab: proposed sample, provider readiness, referee, **Plan evaluation** → maximum spend → **Authorize and run** with the phrase, report + side-by-side metrics, delete returned data; Ground truth panel; DFW truth gate panel. |

**What Mike does to run the first paid contact test (only after he decides to):**

1. Open a Tracerfy account ($20 minimum credit purchase) and put the API token on the Render backend as `TRACERFY_API_TOKEN`. Optionally add `DATASKIP_API_TOKEN` (DataSkip, $0.04/match). `TWILIO_ACCOUNT_SID` / `TWILIO_AUTH_TOKEN` already exist and enable the line-type referee.
2. In EvoSense → Providers & Controls → Service Providers, enable those providers for the workspace. They will read **EVALUATION ONLY**.
3. If an EvoSense org budget is set (Controls), make sure it has room for the run; a lookup that would exceed it is recorded as "not attempted" rather than spending.
4. Provider Evaluation tab: choose providers and referee, keep the proposed sample, press **Plan evaluation**. Nothing is called; the plan shows the maximum spend line by line. To run it, type the phrase shown (`RUN PAID EVALUATION <that evaluation's id>`) and press **Authorize and run**. It cannot spend more than the plan.

**The sample in production (verified 2026-09-27):** the qualifying share of the awaiting properties is shown on the Provider Evaluation tab. The rest are left out with the reason shown: true entities (LLC, company, trust, institutional) or no city on file (a guaranteed miss that would skew the rates). Joint owners, "ET AL", life estates and estates are people and stay in.

**Maximum cost at published prices, 35 properties:**

| Setup | Maximum |
|---|---|
| Tracerfy instant API, every property a hit | 35 × $0.10 = **$3.50** |
| DataSkip, every property a hit | 35 × $0.04 = **$1.40** |
| Twilio referee, at most 105 numbers | 105 × $0.008 = **$0.84** |

Misses cost nothing at Tracerfy and DataSkip.

## 12. Pages that could not be read

**Skip-trace vendors:**

* **Melissa:** all pages blocked.
* **Tracerfy:** /terms and /api returned 404.
* **REAPI:** terms returned 404; the pricing page loaded headers only.
* **BatchData:** /terms returned 404 (the ToS was read at /terms-of-service); the developer home page loaded headers only.
* **DirectSkip:** redirect only.
* **Trestle:** Find Person reference unreadable.

**Comps and property-data vendors:**

* **HouseCanary:** API docs came back empty.
* **Regrid:** schema CSV would not load.
* **BatchData:** listing-data page is password-protected.
* **Trestle:** FAQ looped on redirects.
* **NTREIS:** old data-access page would not load.
* **Zillow:** data terms would not load.
