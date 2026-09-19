# Chariot Energy — broker API integration

Source of truth: **Chariot Energy Broker API Guide v1.0** (received 2026-01-15).
Every wire-level fact below is quoted from that document. Nothing in this
integration is inferred from a sample, a screenshot or a guess.

---

## 1. Where the code lives

| File | What it is |
|---|---|
| `app/services/providers/base.py` | The provider boundary: normalized status vocabulary, `ProviderResult`, and the sensitive-field redactor. No provider named. |
| `app/services/providers/chariot.py` | The Chariot adapter. All eight functions, the status normalizer, the transport seam. |
| `app/models/provider_transaction_models.py` | `provider_transactions` — the authoritative record of every attempt. |
| `app/services/provider_transactions.py` | The single write path, which refuses sensitive data. |
| `app/services/energy_enrollment.py` | Classification, pipeline stages, and the payload/audit split. |
| `tests/test_chariot_provider_adapter.py` | 47 tests. None of them opens a socket. |

## 2. Credential

```
CHARIOT_API_KEY          the broker key Chariot issued. Server-side only.
CHARIOT_ENV              test | production.  ABSENT = test.
CHARIOT_PROMO_CODE       the promo code(s) Chariot allows this broker.
CHARIOT_ALLOW_TRANSACTIONS   NOT set. Must never be set casually — see §5.
```

The key travels as a `Key` parameter on every call, per the guide. It is read
from the environment at call time. It is never written to an organization row,
never sent to a browser, never logged, never placed in a test fixture, and it
is stripped — not redacted, removed — from the request echo stored on a
transaction row.

`chariot.configuration_report()` proves presence without exposing the value:
it returns `key_present` and an 8-character SHA-256 fingerprint, which is
enough to confirm two environments hold the same key and useless otherwise.

## 3. Base URLs

```
Test        http://35.174.234.23:8084
Production  https://api.chariotenergy.com
```

The guide's `Link:` lines all name production; its Sample Code all names the
test IP, which is the older artefact. The adapter selects by configuration and
defaults to test.

## 4. The domain Chariot must map

> "In Live environment you will also need to provide the domain from where you
> will be calling the API. Chariot Energy will map API key with broker domain
> for security purpose."

**Recommended: `api.atlantis-enterprises.com`**, as a Render custom domain on
`advisorflow-backend`, with the customer-facing enrollment experience on the
same registrable domain (`atlantis-enterprises.com`, taken from the contact
address on Atlantis's own approved public site).

Why not the hostname that works today:

- `advisorflow-backend.onrender.com` is **platform infrastructure, not the
  broker**. Chariot is mapping a key to *the broker's identity*. The broker is
  Atlantis Light & Power. Handing them a hostname that names the white-label
  platform ties Atlantis's provider credential to EvoSys Pro's infrastructure
  name, which is the same layering mistake the workspace presentation exists
  to avoid.
- It is **not owned by Atlantis**. It is a Render-generated name on a shared
  domain; it changes if the service is ever recreated, and a key mapped to it
  would have to be re-registered with Chariot to recover.
- It is **shared by every customer on the platform**. A key bound to it is
  bound to a host that also serves other tenants.

`app.evosyspro.live` is the wrong answer for the same reason with a different
name: it is the platform's application domain, not the broker's.

**DNS was not changed in this pass** — no authorization to do so. What is
needed, when authorized: a `CNAME` for `api.atlantis-enterprises.com` →
`advisorflow-backend.onrender.com`, added as a custom domain on that Render
service, then given to Chariot for mapping.

## 5. Why transactions are disarmed

`SubmitEnrollment` and `SubmitRenewal` are refused unless
`CHARIOT_ALLOW_TRANSACTIONS` is explicitly set. `ChariotTransactionsDisarmed`
is raised **before any network call is attempted**.

This is a separate switch from the key on purpose. The key is needed for the
read-only work — plans, addresses, dates, charges, waivers. If arming rode on
the key, then getting plan lookups working would silently arm enrollment. A
successful enrollment changes a real person's electricity supplier, generates
contract documents, and causes Chariot to email and text them. **The guide
documents no way to cancel one.**

## 6. Status normalization

| Chariot `Status` / `Message` | EvoSys normalized | Pipeline stage |
|---|---|---|
| `Enrollment has been completed` + `Passed CreditCheck & No Deposit required` | `provider_confirmed` | `provider_confirmed` |
| `Enrollment has been completed` + `Passed CreditCheck & Submit Deposit Successfully` | `provider_confirmed` | `provider_confirmed` |
| `Enrollment has been completed with Pending Status` + `Deposit Waiver has been requested` | `pending_provider` | `provider_pending` |
| `Deposit Required Your Deposit Amount is $X` | `action_required` | `deposit_required` |
| `CreditCheck Failed` | `provider_failed` | `provider_failed` |
| `DepositAutopayFail` | `action_required` | `human_review` |
| `Validation Failed` | `failed_validation` | `human_review` |
| `Error Message : Invalid APIKey` | `integration_error` | `human_review` |
| anything else | `pending_provider` | `provider_pending` |

Two things in that table are load-bearing:

1. **Ordering.** `"Enrollment has been completed with Pending Status"` starts
   with `"Enrollment has been completed"`. A prefix test written the obvious
   way marks a deposit-waiver request as a finished enrollment — and there is
   no endpoint in this API that would ever correct it. The pending branch is
   tested first, and a test asserts it.

2. **The default is pending, never confirmed.** A sentence the adapter has not
   been taught is a sentence nobody has read, and the safe resting place for
   an unread enrollment is a queue with a human in front of it.

`enrolled` is never reached by a provider answer. `provider_confirmed` is as
far as Chariot can move it; `enrolled` is the workspace asserting that the
confirmation has been reconciled, and only a person makes that assertion.

## 7. The reference number

Chariot's JSON spells it **`RefrenceNumber`**. The adapter reads that spelling
and the correct one. It is the authoritative external id for an enrollment,
there is no lookup endpoint, and `apply_result` will never overwrite a stored
reference with an empty one — a lost reference is unrecoverable.

## 8. The asynchronous status gap

Broker API Guide v1.0 documents **eight functions**. All 33 pages were read.
There is:

- **no webhook or callback registration**
- **no `GetEnrollmentStatus` or transaction-status endpoint**
- **no document-retrieval endpoint**

None was invented. The consequence is architectural:

- `pending_provider` is a **resting state**, not a transient one.
- **Nothing promotes it on a timer.** Time is not evidence.
- It surfaces via `provider_transactions.pending_provider()` with its age, for
  a human to chase with Chariot directly.

`chariot.HAS_STATUS_CALLBACK` / `HAS_STATUS_LOOKUP` / `HAS_DOCUMENT_RETRIEVAL`
are `False` and are asserted by a test, so the absence is a fact the platform
acts on rather than a comment somebody hopes is still true. When Chariot
publishes a status mechanism, it becomes one more function returning a
`ProviderResult` and calls `provider_transactions.apply_result()` on the same
row. Nothing above the adapter changes.

## 9. Sensitive data

`SubmitEnrollment` requires SSN or driver's licence, date of birth, and often
card or bank details. The path is:

```
customer browser -> server-side handler (local variable)
                 -> chariot.submit_enrollment()
                 -> redacted ProviderResult
                 -> provider_transactions.record()  [REFUSES sensitive fields]
```

`build_enrollment_payload()` returns `(payload, audit_fields)` **separately and
never merged**. `payload` is for the wire. `audit_fields` is what may be kept:
product, meter, service type and date. A caller that tries to persist `payload`
hits `assert_no_sensitive` and gets an exception — the write fails rather than
succeeding with a redaction nobody notices.

The AI never sees any of it. Identity and payment fields never land in a lead
field, an activity, a note or a conversation body, so they have no route into
a prompt.

## 10. Plan snapshot

Captured at selection, non-sensitive by construction: product id and ref id,
utility, title, term, the three published price points, ETF and ETF type,
category, renewable percentage, the pricing components, and the **EFL, TOS and
YRAC URLs**.

The documents are stored as **links, not files**. Nothing downloads them.
Nothing calls them "signed": the guide says Chariot generates PDF documents
during enrollment and says nothing about returning signed evidence, so this
integration makes no such claim.

## 11. Residential only

> `CustomerTypeID Mandatory Numeric / Possible value is 1 - For Residential`

The guide documents no commercial customer type. `energy_enrollment.classify()`
routes a commercial premise to `commercial_energy`, which is **not** in
`PROVIDER_ELIGIBLE_CLASSIFICATIONS`, and `build_enrollment_payload` raises if
asked anyway. A business that filled in the residential form is still a
business — that case is tested.
