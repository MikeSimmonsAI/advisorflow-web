# `reconcile_business.py` — safe usage

READ-ONLY reconciliation of one operational population against one historical
master, with the business layer on top: canonical permission resolution,
per-channel eligibility, and exactly one work classification per record.

It is a **platform capability, not one customer's report**. Both populations
are arguments. No customer, advisor, file name or population size is written
into the code, and a test asserts that.

## Running it

```
python scripts/reconcile_business.py \
    --target <operational population file> \
    --source <historical master file> \
    [--opportunities <opportunity export>] \
    [--out <output.csv>] [--top 25]
```

`.csv`, `.tsv`, `.xlsx` are accepted on every input.

## What it will not do

- **No database handle.** It imports no SQLAlchemy, no session, no `get_db`.
  It cannot read or write a Lead.
- **No send path.** No Twilio, Resend, SMTP or HTTP client. Nothing can leave
  the building because this ran.
- **No writes to either input.** Workbooks are opened `read_only=True`.
- **No credentials.** It reads no `.env` and takes no secret.

Tests in `tests/test_reconcile_business.py` assert each of these against the
source of the script itself, so the guarantee is a property of the code rather
than a claim in this file.

## Where the output goes — this matters

The per-record CSV contains **real contact details for real families**: names,
email addresses, phone numbers, addresses, dispositions and permission state.

- Write it **outside the repository**. Never into the working tree.
- It is **never committed**. `*.csv` output belongs with the customer's own
  data, on the operator's machine or in the customer's own storage.
- Prefer a path under the operator's own documents/downloads directory, beside
  the source exports it was derived from.

If you need to share findings, share the aggregate counts and the classification
reasons. The per-record file is working data, not a deliverable.

## The rules the business layer enforces

**Permission.** Resolved per channel, most-restrictive-wins, across the
operational row **and every candidate historical record** — not just the one
the matcher ranked first. Two master rows matching one lead and disagreeing
about a permission resolve to the denial: identity is the ambiguous part, a
restriction is not. UNKNOWN is never converted to consent.

**Zero-variance columns are not consent.** A permission column carrying the
same value for every row in the entire source states no per-person decision.
It is demoted to UNKNOWN for classification while its stated value is still
reported. Reading such a column as permission manufactures consent for a whole
database from a default nobody ever set.

**Channel eligibility is independent of work classification.** A record held
out of the email work list may still be lawfully reachable by text.
`EMAIL_ELIGIBLE` / `SMS_ELIGIBLE` / `VOICE_ELIGIBLE` / `CHANNEL_REVIEW` /
`DO_NOT_CONTACT` are reported per record and never collapsed into the primary
classification. `DO_NOT_CONTACT` requires every contactable channel to be
denied — one denied channel is not a do-not-contact.

**Weak joins never decide.** An opportunity export that identifies its contact
by display name only is corroborating evidence. It can support a sale the
master already states; it can never retire a record on its own. On a large
population a common name matches many unrelated opportunities.

**A missing match is not a clean record.** When the historical master is itself
a filtered extract — anything titled "excludes non-viable" and similar — a
record absent from it may have been excluded *because* it was made non-viable.
`NO_CONFIDENT_MASTER_MATCH` means unresolved, never "clear to work".

## Work classifications

Exactly one per record, decided in this order so nothing is promoted past a
restriction by scoring well on something else:

```
DO_NOT_CONTACT > DUPLICATE_REVIEW > ALREADY_RESOLVED > BAD_CONTACT_DATA
    > NO_CONFIDENT_MASTER_MATCH > REVIEW_REQUIRED
    > WORK_NOW_EMAIL > WORK_NOW_VOICE > WORK_LATER
```

## Ranking

Evidence order, not a score: disposition, then a logged action, then activity
recency, then reachability. Ties are left as ties. Nothing is invented to
create separation between records that the evidence does not separate.
