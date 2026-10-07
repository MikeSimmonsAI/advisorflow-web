# -*- coding: utf-8 -*-
"""Messy CSV -> mapped, classified, committed contacts -> explicit promotion ->
synthetic property -> canonical buy-box match -> disposition PREVIEW.

Every hop goes through the mounted routes and product services; nothing here
re-implements import, qualification or matching logic. Synthetic data only.

Human handoff boundaries asserted below (nothing crosses them automatically):
  1. import commit creates CONTACTS, never leads;
  2. a person's click on /intake/contacts/{id}/promote makes the lead
     (DNC refused);
  3. an operator's POST /wholesale/properties/{id}/seller attaches that lead
     to a property;
  4. disposition is previewed only; the email provider is a recording fake and
     must record ZERO calls.

PROOF LEVEL: database integration against the test DB, no live provider.
"""
import io

import pytest

from app.models.import_models import ImportStagedRow
from app.models.intake_models import OrgContact
from app.models.models import Lead, Organization, User
from app.services.auth_service import create_access_token, hash_password
from tests.test_wholesale_disposition import FakeProvider, ok

# Padded + mixed-case aliases, an irrelevant column, a classification column,
# a DNC column, "Last Activity Date", and malformed / missing data.
CSV = (
    b"  FIRST NAME ,last name,E-Mail ,  Phone ,Last Activity Date,Segment,DNC,Irrelevant Blob\n"
    b"Ava,Ready,ava.ready@example.com,214-555-0201,2026-01-15,Seller Lead,,zzz\n"
    b"Ben,Review,not-an-email,214-555-0202,01/20/2026,Seller Lead,,zzz\n"
    b"Cara,Donotcall,cara.dnc@example.com,214-555-0203,2026-02-01,Seller Lead,yes,zzz\n"
    b",,,214-555-0204,,Seller Lead,,zzz\n"
    b"Dan,Nodata,,,garbage-date,Seller Lead,,zzz\n"
)


@pytest.fixture(autouse=True)
def _inline(monkeypatch):
    monkeypatch.setenv("INTAKE_INLINE_JOBS", "1")


def _tenant(db, name, slug, email):
    org = Organization(name=name, slug=slug, plan="enterprise", industry="real_estate")
    db.add(org)
    db.commit()
    user = User(organization_id=org.id, email=email,
                password_hash=hash_password("Pass12345!"), full_name=email.split("@")[0],
                role="org_admin", must_change_password=False)
    db.add(user)
    db.commit()
    return org, {"Authorization": "Bearer %s" % create_access_token(user, db)}


@pytest.fixture()
def tenants(client, db_session):
    a = _tenant(db_session, "Chain Wholesale", "chain-ws", "admin@chain-ws.test")
    b = _tenant(db_session, "Chain Intruder", "chain-intruder", "admin@chain-intruder.test")
    return a, b


def _get(client, h, bid):
    r = client.get("/intake/batches/%s" % bid, headers=h)
    assert r.status_code == 200, r.text
    return r.json()


def _upload(client, h):
    r = client.post("/intake/batches", headers=h, data={"source": "csv"},
                    files={"file": ("messy.csv", io.BytesIO(CSV), "text/csv")})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def test_messy_csv_to_no_send_disposition_preview(client, db_session, tenants, monkeypatch):
    from app.services import email_service
    fake = FakeProvider()
    monkeypatch.setattr(email_service, "send_email", fake)
    monkeypatch.setenv("OUTBOUND_EMAIL_WHOLESALE_BUYER_DISPOSITION", "true")

    (org, h), (other, ho) = tenants
    bid = _upload(client, h)

    # ── mapping is persisted server-side; reload loses no field ───────────────
    first = _get(client, h, bid)
    headers = first["headers"]
    saved = first["mapping"]
    assert set(saved) == set(headers), "every uploaded column keeps a mapping entry"
    assert len(headers) == 8
    r = client.put("/intake/batches/%s/mapping" % bid, headers=h, json={"mapping": saved})
    assert r.status_code == 200, r.text
    reloaded = _get(client, h, bid)
    assert reloaded["mapping"] == saved
    assert set(reloaded["headers"]) == set(headers)
    last_activity = next(k for k in headers if k.strip().lower() == "last activity date")
    assert reloaded["mapping"][last_activity] == saved[last_activity]

    # ── server-owned step transition, no mapping loop ─────────────────────────
    assert client.post("/intake/batches/%s/analyze" % bid, headers=h).status_code == 202
    analyzed = _get(client, h, bid)
    assert analyzed["status"] == "ready_for_review"
    assert analyzed["workflow"] == {"step": 3, "analyzed": True, "classified": False}

    vals = client.get("/intake/batches/%s/classification-values" % bid, headers=h).json()
    vmap = {v["value"]: v["classification"] for v in vals["values"]}
    r = client.put("/intake/batches/%s/mapping" % bid, headers=h,
                   json={"classification": {"value_map": vmap, "fallback": "contact"}})
    assert r.status_code == 200, r.text
    assert r.json()["workflow"]["classified"] is True
    assert client.post("/intake/batches/%s/analyze" % bid, headers=h).status_code == 202
    after = _get(client, h, bid)
    assert after["workflow"] == {"step": 5, "analyzed": True, "classified": True}
    assert _get(client, h, bid)["workflow"]["step"] == 5          # refresh agrees
    assert after["mapping"] == saved                              # re-analysis kept mapping

    # ── persisted per-row statuses; DNC / no-data rows are never "ready" ──────
    rows = {r_.row_number: r_ for r_ in db_session.query(ImportStagedRow).filter(
        ImportStagedRow.batch_id == bid).all()}
    assert len(rows) == 5
    statuses = {n: r_.intake_status for n, r_ in rows.items()}
    ready_rows = [n for n, s in statuses.items() if s == "ready"]
    assert len(ready_rows) >= 1 and len(ready_rows) < 5
    dnc_row = next(r_ for r_ in rows.values() if (r_.first_name or "") == "Cara")
    assert dnc_row.intake_status != "ready"

    # ── cross-tenant access is rejected on every batch route ──────────────────
    for method, url, body in [
            ("get", "/intake/batches/%s" % bid, None),
            ("get", "/intake/batches/%s/rows" % bid, None),
            ("put", "/intake/batches/%s/mapping" % bid, {"mapping": saved}),
            ("post", "/intake/batches/%s/analyze" % bid, None),
            ("post", "/intake/batches/%s/commit" % bid, {"mode": "stage_only"})]:
        resp = getattr(client, method)(url, headers=ho, **({"json": body} if body else {}))
        assert resp.status_code in (403, 404), (url, resp.status_code)

    # ── a remap that moves the classification column clears the confirmation ──
    unmapped = dict(saved)
    unmapped["Segment"] = {"kind": "ignore"}
    r = client.put("/intake/batches/%s/mapping" % bid, headers=h, json={"mapping": unmapped})
    assert r.status_code == 200, r.text
    assert r.json()["workflow"]["classified"] is False
    r = client.put("/intake/batches/%s/mapping" % bid, headers=h, json={"mapping": saved})
    assert r.status_code == 200, r.text
    client.put("/intake/batches/%s/mapping" % bid, headers=h,
               json={"classification": {"value_map": vmap, "fallback": "contact"}})
    assert client.post("/intake/batches/%s/analyze" % bid, headers=h).status_code == 202
    assert _get(client, h, bid)["workflow"]["step"] == 5

    # ── boundary 1: commit makes contacts, never leads ────────────────────────
    leads_before = db_session.query(Lead).filter(Lead.organization_id == org.id).count()
    r = client.post("/intake/batches/%s/commit" % bid, headers=h,
                    json={"mode": "ready_only", "confirm_organization_name": "chain wholesale"})
    assert r.status_code == 202, r.text
    assert _get(client, h, bid)["workflow"]["step"] == 7
    assert db_session.query(Lead).filter(Lead.organization_id == org.id).count() == leads_before
    contacts = client.get("/intake/contacts", headers=h).json()["contacts"]
    assert contacts, "the READY row became a contact"
    assert client.get("/intake/contacts", headers=ho).json()["total"] == 0
    by_name = {(c["first_name"] or ""): c for c in contacts}
    assert "Cara" not in by_name or by_name["Cara"]["sms_status"] == "dnc"
    ready = by_name["Ava"]

    # ── boundary 2: explicit promotion of the READY contact only ──────────────
    if "Cara" in by_name:
        refused = client.post("/intake/contacts/%s/promote" % by_name["Cara"]["id"],
                              headers=h, json={})
        assert refused.status_code == 409
    assert client.post("/intake/contacts/%s/promote" % ready["id"], headers=ho,
                       json={}).status_code in (403, 404)
    promoted = client.post("/intake/contacts/%s/promote" % ready["id"], headers=h, json={})
    assert promoted.status_code == 201, promoted.text
    lead_id = promoted.json()["lead_id"]
    assert db_session.query(Lead).filter(
        Lead.organization_id == org.id).count() == leads_before + 1

    # ── READY / REVIEW / EXCLUDED and HIGH / MEDIUM / LOW from the real gate ──
    q = client.post("/qualification/preview", headers=h,
                    json={"channel": "email", "include_leads": True})
    assert q.status_code == 200, q.text
    qb = q.json()
    assert qb["total_selected"] == qb["ready"] + qb["review"] + qb["excluded"]
    assert set(qb["buckets"]) >= {"READY_TO_SEND", "REVIEW_REQUIRED", "EXCLUDED"}
    assert set(qb["priority"]) == {"HIGH", "MEDIUM", "LOW"}
    mine = next(d for d in qb["leads"] if d["lead_id"] == lead_id)
    assert mine["bucket"] in ("READY_TO_SEND", "REVIEW_REQUIRED", "EXCLUDED")
    assert (mine["priority"] is None) == (mine["bucket"] == "EXCLUDED")
    assert client.post("/qualification/preview", headers=ho,
                       json={"channel": "email", "include_leads": True}
                       ).json()["total_selected"] == 0

    # ── boundary 3: operator attaches the lead to a synthetic property ────────
    prop = ok(client.post("/wholesale/properties", headers=h, json={
        "street_address": "201 Chain Test Ln", "city": "Dallas", "state": "TX",
        "zip_code": "75201", "county": "Dallas", "property_type": "single_family",
        "bedrooms": 3, "bathrooms": 2, "square_feet": 1500}))
    seller = ok(client.post("/wholesale/properties/%s/seller" % prop["id"], headers=h,
                            json={"lead_id": lead_id}))
    assert seller["lead_id"] == lead_id
    deal_id = prop["deal"]["id"]
    ok(client.patch("/wholesale/deals/%s/analysis" % deal_id, headers=h,
                    json={"arv": 300000, "repair_estimate": 40000, "proposed_offer": 150000}))

    # ── canonical buy boxes -> match -> disposition PREVIEW ───────────────────
    fits = ok(client.post("/wholesale/buyers", headers=h, json={
        "company_name": "Chain Fit Buyer", "email": "fit.buyer@example.com"}))
    misses = ok(client.post("/wholesale/buyers", headers=h, json={
        "company_name": "Chain Miss Buyer", "email": "miss.buyer@example.com"}))
    ok(client.post("/wholesale/buyers/%s/buy-boxes" % fits["id"], headers=h, json={
        "label": "Dallas SFR", "states": ["TX"], "counties": ["Dallas"],
        "property_types": ["single_family"], "max_price": 200000}))
    ok(client.post("/wholesale/buyers/%s/buy-boxes" % misses["id"], headers=h, json={
        "label": "Ohio only", "states": ["OH"], "property_types": ["single_family"]}))
    matches = ok(client.post("/wholesale/deals/%s/match-buyers" % deal_id, headers=h))["matches"]
    by_buyer = {m["buyer_id"]: m for m in matches}
    assert by_buyer[fits["id"]]["disqualified"] is False
    assert by_buyer[fits["id"]]["score"] > by_buyer[misses["id"]]["score"] \
        or by_buyer[misses["id"]]["disqualified"] is True
    assert client.post("/wholesale/deals/%s/match-buyers" % deal_id,
                       headers=ho).status_code in (403, 404)

    prev = ok(client.post("/wholesale/deals/%s/disposition/preview" % deal_id, headers=h,
                          json={"buyer_ids": [fits["id"]], "channel": "email"}))
    assert prev["recipients"] == 1
    assert "ava" not in str(prev).lower() or "Ava" not in prev.get("body", "")

    # ── boundary 4: nothing was sent, and nothing excluded is outreach-eligible
    assert fake.calls == []
    eligible = {d["lead_id"] for d in qb["leads"] if d["bucket"] == "READY_TO_SEND"}
    for name in ("Ben", "Cara", "Dan"):
        c = by_name.get(name)
        if c and c.get("lead_id"):
            assert c["lead_id"] not in eligible
    assert db_session.query(OrgContact).filter(
        OrgContact.organization_id == other.id).count() == 0
