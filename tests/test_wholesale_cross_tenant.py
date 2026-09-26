"""The attack: tenant B holds tenant A's identifiers and uses every one of them.

`test_wholesale_guards.py` checks that a listing does not leak and that a deal
id is refused. This file is the systematic version of the same question, and it
is deliberately written as an attack rather than as a feature test: tenant A
creates one of EVERY object the module has an endpoint for, and tenant B then
calls every endpoint that takes an id, with A's ids, using B's own valid token.

The bar is absolute. Not one of those calls may return 2xx. A 404 is preferred
over a 403 because a 403 confirms the id exists, but either is a denial; what
is forbidden is a success, a leaked field, or a write that lands.

The route table below is checked against the live application at the end of the
file, so an endpoint added later without a line here fails this file rather than
silently going untested.
"""

import pytest

from app.main import app
from app.models.models import Organization, User
from app.services.auth_service import create_access_token, hash_password


@pytest.fixture()
def other_org(db_session):
    org = Organization(name="Attacker Wholesaler", slug="attacker-wholesaler",
                       plan="standard", industry="real_estate")
    db_session.add(org)
    db_session.commit()
    return org


@pytest.fixture()
def other_headers(db_session, other_org):
    """A perfectly valid token. That is the point: the caller is a real,
    signed-in user of a DIFFERENT organization, not an anonymous request."""
    user = User(organization_id=other_org.id, email="attacker@wholesale.test",
                password_hash=hash_password("TestPass123!"),
                full_name="Attacker Advisor", role="org_admin",
                must_change_password=False)
    db_session.add(user)
    db_session.commit()
    return {"Authorization": "Bearer %s" % create_access_token(user, db_session)}


def ok(response):
    assert response.status_code in (200, 201), \
        "%s %s" % (response.status_code, response.text[:400])
    return response.json()


def an_id(payload, *keys):
    """The new row's id, wherever this endpoint chose to put it.

    Some of these endpoints answer with the row, some wrap it ({"comp": …}),
    some return the recalculated deal alongside it. The attack only needs the
    identifier, so it is fished out rather than each shape being hard-coded.
    """
    if isinstance(payload, dict):
        if isinstance(payload.get("id"), str):
            return payload["id"]
        for key in keys:
            nested = payload.get(key)
            if isinstance(nested, dict) and isinstance(nested.get("id"), str):
                return nested["id"]
            if isinstance(nested, list) and nested:
                first = nested[0]
                if isinstance(first, dict) and isinstance(first.get("id"), str):
                    return first["id"]
    raise AssertionError("no id in %r" % (payload,))


@pytest.fixture
def local_storage(tmp_path, monkeypatch):
    """Turn file storage on, into a throwaway directory.

    The file endpoints are the ones most worth attacking, and an attack against
    a 503 proves nothing. This switches on the `local` backend for the duration
    of the test so tenant A owns a REAL stored document for tenant B to fail to
    read. Nothing leaks: the directory is pytest's own and dies with the test.
    """
    monkeypatch.setenv("MEDIA_STORAGE_BACKEND", "local")
    monkeypatch.setenv("WHOLESALE_LOCAL_MEDIA_ROOT", str(tmp_path / "media"))
    return tmp_path


@pytest.fixture
def theirs(client, auth_headers, local_storage, db_session, sample_org, sample_advisor):
    """Every id tenant A owns, made once and handed to the attacker."""
    prop = ok(client.post("/wholesale/properties", headers=auth_headers,
                          json={"street_address": "1 Victim Ln", "city": "Dallas",
                                "state": "TX", "county": "Dallas",
                                "zip_code": "75201", "property_type": "single_family",
                                "bedrooms": 3, "bathrooms": 2, "square_feet": 1500,
                                "is_test": True}))
    deal_id = prop["deal"]["id"]

    seller = ok(client.post("/wholesale/properties/%s/seller" % prop["id"],
                            headers=auth_headers,
                            json={"first_name": "Vic", "last_name": "Tim",
                                  "phone": "2145559001",
                                  "email": "vic@example.com"}))

    comp = ok(client.post("/wholesale/deals/%s/comps" % deal_id,
                          headers=auth_headers,
                          json={"street_address": "2 Comp St", "sale_price": 300000,
                                "square_feet": 1500}))

    approval = ok(client.post("/wholesale/deals/%s/approvals" % deal_id,
                              headers=auth_headers,
                              json={"kind": "offer", "amount": 100000}))

    document = ok(client.post("/wholesale/deals/%s/documents" % deal_id,
                              headers=auth_headers,
                              json={"doc_type": "purchase_contract",
                                    "title": "Victim contract"}))

    buyer = ok(client.post("/wholesale/buyers", headers=auth_headers,
                           json={"company_name": "Victim Capital",
                                 "email": "buyer@example.com", "is_test": True}))
    box = ok(client.post("/wholesale/buyers/%s/buy-boxes" % an_id(buyer, "buyer"),
                         headers=auth_headers,
                         json={"label": "DFW", "states": ["TX"],
                               "min_price": 1, "max_price": 999999}))

    ok(client.post("/wholesale/deals/%s/match-buyers" % deal_id,
                   headers=auth_headers, json={}))
    composed = ok(client.post("/wholesale/deals/%s/disposition" % deal_id,
                              headers=auth_headers,
                              json={"buyer_ids": [an_id(buyer, "buyer")],
                                    "channel": "email"}))
    outreach_rows = composed.get("results") or []

    # Phase 3: a negotiation row and a stored file, so the endpoints that name
    # one have a real id of tenant A's to be refused.
    offer = ok(client.post("/wholesale/deals/%s/offers" % deal_id,
                           headers=auth_headers,
                           json={"amount": 100000, "direction": "us"}))

    # A real stored photo, so the file endpoints are attacked against an object
    # that actually exists rather than against a 404 they would return anyway.
    photo = ok(client.post(
        "/wholesale/properties/%s/photos" % prop["id"], headers=auth_headers,
        files={"file": ("victim.png",
                        b"\x89PNG\r\n\x1a\n" + b"\x00" * 128, "image/png")}))

    # Phase 5: a contract template of A's, so the template endpoints are
    # attacked against a real row rather than against a 404 they would return
    # anyway.
    template = ok(client.post(
        "/wholesale/contract-templates", headers=auth_headers,
        files={"file": ("victim-contract.pdf", b"%PDF-1.4\n" + b"\x00" * 64,
                        "application/pdf")},
        data={"name": "Victim purchase agreement", "doc_type": "purchase_contract",
              "jurisdiction": "Texas", "source_note": "their attorney"}))

    # Phase 7: EvoSense. A seeded SANDBOX acquisition world of A's — a
    # strategy, a discovered property with a live conversation and a hand-off,
    # an open identity review and a contact point — so every EvoSense route
    # that names a record is attacked against a real row.
    from app.models.evosense_models import (EvoSenseContactPoint, EvoSenseHandoff,
                                            EvoSenseIdentityReview)
    from app.services.evosense import sandbox_seed
    seeded = sandbox_seed.seed_review(db_session, sample_org.id, sample_advisor, replies=True)
    db_session.commit()
    es_prop = sandbox_seed.prop_at(db_session, sample_org.id, "1418 Cedar Springs Rd")
    es_ids = {
        "es_property_id": es_prop.id,
        "es_strategy_id": seeded["strategies"]["dfw"],
        "es_handoff_id": db_session.query(EvoSenseHandoff).filter(
            EvoSenseHandoff.organization_id == sample_org.id).first().id,
        "es_review_id": db_session.query(EvoSenseIdentityReview).filter(
            EvoSenseIdentityReview.organization_id == sample_org.id).first().id,
        "es_contact_id": db_session.query(EvoSenseContactPoint).filter(
            EvoSenseContactPoint.organization_id == sample_org.id).first().id,
    }
    # DFW acquisition: raw source evidence behind one of A's observations.
    from app.models.evosense_models import EvoSenseObservation
    es_ids["es_observation_id"] = db_session.query(EvoSenseObservation).filter(
        EvoSenseObservation.organization_id == sample_org.id,
        EvoSenseObservation.property_id == es_prop.id).first().id
    # Phase 7.1: a real open routing review of A's (an inbound SMS that could
    # belong to two of A's conversations), so its resolve route is attacked
    # against a row that exists.
    from app.models.models import Lead, Reply
    from app.services.evosense import inbound as evosense_inbound
    es_eng = sandbox_seed.engagement_for(db_session, es_prop)
    es_lead = db_session.query(Lead).filter(Lead.id == es_eng.lead_id).first()
    es_reply = db_session.query(Reply).filter(Reply.lead_id == es_lead.id).first()
    other_eng = sandbox_seed.engagement_for(
        db_session, sandbox_seed.prop_at(db_session, sample_org.id, "7302 Ferguson Rd"))
    es_ids["es_routing_review_id"] = evosense_inbound.open_routing_review(
        db_session, sample_org.id, es_lead, es_reply, [es_eng, other_eng])["review_id"]

    # Priority 3: a re-derivation dry run of A's.
    from app.services.evosense import reprocess as evosense_reprocess
    es_ids["es_reprocess_run_id"] = evosense_reprocess.dry_run(
        db_session, sample_org.id, user=sample_advisor).id
    db_session.commit()

    return {
        **es_ids,
        "file_id": photo["id"],
        "template_id": an_id(template, "template"),
        "property_id": an_id(prop, "property"),
        "deal_id": deal_id,
        "offer_id": an_id(offer, "offer"),
        "profile_id": an_id(seller, "seller", "seller_profile", "profile"),
        "comp_id": an_id(comp, "comp"),
        "approval_id": an_id(approval, "approval"),
        "document_id": an_id(document, "document"),
        "buyer_id": an_id(buyer, "buyer"),
        "box_id": an_id(box, "buy_box", "box"),
        "outreach_id": (outreach_rows[0].get("outreach_id")
                        if outreach_rows else None),
    }


def attacks(ids):
    """(method, path, json) for every endpoint that names somebody's record."""
    p, d = ids["property_id"], ids["deal_id"]
    return [
        # ── Reads: does anything of A's come back? ──────────────────────────
        ("get", "/wholesale/deals/%s" % d, None),
        ("get", "/wholesale/deals/%s/cadence" % d, None),
        ("get", "/wholesale/deals/%s/matches" % d, None),
        ("get", "/wholesale/properties/%s/enrichment" % p, None),

        # ── Writes on the property and the owner ────────────────────────────
        ("patch", "/wholesale/properties/%s" % p, {"city": "Stolen"}),
        ("post", "/wholesale/properties/%s/seller" % p,
         {"first_name": "Attacker", "phone": "2145559999"}),
        ("post", "/wholesale/properties/%s/manual-contact" % p,
         {"owner_name": "Attacker", "phone": "2145559999"}),
        ("patch", "/wholesale/sellers/%s" % ids["profile_id"],
         {"motivation": "rewritten by a stranger"}),

        # ── Writes that move the deal ───────────────────────────────────────
        ("post", "/wholesale/deals/%s/stage" % d, {"stage": "dead"}),
        ("post", "/wholesale/deals/%s/seller-reply" % d, {"message": "STOP"}),
        ("post", "/wholesale/deals/%s/cadence" % d, {"action": "start"}),
        ("post", "/wholesale/deals/%s/outreach" % d, {"message": "hello"}),

        # ── Writes that move money or bind the company ──────────────────────
        ("patch", "/wholesale/deals/%s/analysis" % d, {"arv": 1}),
        ("post", "/wholesale/deals/%s/analysis/recalculate" % d, {}),
        ("post", "/wholesale/deals/%s/approvals" % d, {"kind": "offer", "amount": 1}),
        ("post", "/wholesale/approvals/%s/decide" % ids["approval_id"],
         {"decision": "approved"}),
        ("patch", "/wholesale/deals/%s/contract" % d, {"contract_price": 1}),
        ("patch", "/wholesale/deals/%s/title" % d, {"title_company": "Stolen Title"}),
        ("post", "/wholesale/deals/%s/close" % d, {"wholesale_fee_collected": 1}),
        # Phase 5: recording collected money is the one write that creates
        # revenue, so a stranger reaching it would invent somebody's income.
        ("post", "/wholesale/deals/%s/fee-collected" % d, {"amount": 1}),
        ("post", "/wholesale/deals/%s/assign" % d, {"buyer_price": 1}),

        # ── Comps and documents ─────────────────────────────────────────────
        ("post", "/wholesale/deals/%s/comps" % d,
         {"street_address": "planted", "sale_price": 1}),
        ("patch", "/wholesale/comps/%s" % ids["comp_id"], {"sale_price": 1}),
        ("delete", "/wholesale/comps/%s" % ids["comp_id"], None),
        ("post", "/wholesale/deals/%s/documents" % d,
         {"doc_type": "purchase_contract", "title": "planted"}),
        ("patch", "/wholesale/documents/%s" % ids["document_id"],
         {"signature_status": "signed"}),
        ("delete", "/wholesale/documents/%s" % ids["document_id"], None),

        # ── The buyer list, which is the asset worth stealing ───────────────
        ("patch", "/wholesale/buyers/%s" % ids["buyer_id"], {"company_name": "Taken"}),
        ("post", "/wholesale/buyers/%s/buy-boxes" % ids["buyer_id"],
         {"label": "planted", "states": ["TX"]}),
        ("patch", "/wholesale/buy-boxes/%s" % ids["box_id"], {"label": "taken"}),
        ("delete", "/wholesale/buy-boxes/%s" % ids["box_id"], None),

        # ── Disposition: the path that can actually send something ──────────
        ("post", "/wholesale/deals/%s/match-buyers" % d, {}),
        ("post", "/wholesale/deals/%s/disposition/preview" % d,
         {"buyer_ids": [ids["buyer_id"]]}),
        ("post", "/wholesale/deals/%s/disposition" % d,
         {"buyer_ids": [ids["buyer_id"]], "channel": "email"}),
        ("post", "/wholesale/outreach/%s/resend" % ids["outreach_id"], {}),
        ("patch", "/wholesale/outreach/%s" % ids["outreach_id"],
         {"status": "offer_submitted", "offer_amount": 1}),

        # ── Phase 3: the negotiation, the money and the drawer ──────────────
        ("get", "/wholesale/deals/%s/offers" % d, None),
        ("post", "/wholesale/deals/%s/offers" % d, {"amount": 1, "direction": "us"}),
        ("patch", "/wholesale/offers/%s" % ids["offer_id"], {"status": "accepted"}),
        ("post", "/wholesale/deals/%s/lost" % d, {"reason": "price_too_high"}),
        ("post", "/wholesale/deals/%s/economics-correction" % d,
         {"reason": "stealing the economics", "wholesale_fee_collected": 1}),

        # ── Phase 3: the disposition desk ───────────────────────────────────
        ("get", "/wholesale/deals/%s/buyer-board" % d, None),
        ("post", "/wholesale/deals/%s/select-buyer" % d,
         {"outreach_id": ids["outreach_id"]}),
        ("post", "/wholesale/outreach/%s/response" % ids["outreach_id"],
         {"status": "interested", "offer_amount": 1}),
        ("post", "/wholesale/outreach/%s/pof-status" % ids["outreach_id"],
         {"status": "verified"}),

        # Deleting somebody else's buyer takes their disposition list with it.
        ("delete", "/wholesale/buyers/%s" % ids["buyer_id"], None),

        # ── Phase 3: files. A stored document is the thing worth stealing. ──
        ("get", "/wholesale/properties/%s/photos" % p, None),
        ("get", "/wholesale/files/%s" % ids["file_id"], None),
        ("patch", "/wholesale/files/%s" % ids["file_id"], {"caption": "taken"}),
        ("delete", "/wholesale/files/%s" % ids["file_id"], None),
        ("post", "/wholesale/properties/%s/photos/reorder" % p,
         {"file_ids": [ids["file_id"]]}),

        # ── Phase 5: publication. The worst thing a stranger could reach. ───
        #
        # Publishing somebody else's deal does not steal data by itself — it
        # PUBLISHES it, to an audience the attacker then chooses, on a link the
        # attacker holds. That is a data breach initiated through a write, so
        # every one of these is attacked like a read AND a write.
        ("get", "/wholesale/deals/%s/publication" % d, None),
        ("patch", "/wholesale/deals/%s/publication" % d,
         {"buyer_room_summary": "planted", "buyer_room_show_arv": True}),
        ("post", "/wholesale/deals/%s/publication/state" % d,
         {"audience": "buyer", "published": True}),
        ("post", "/wholesale/deals/%s/share-links" % d,
         {"audience": "seller", "recipient_name": "attacker"}),
        ("get", "/wholesale/deals/%s/share-activity" % d, None),
        ("post", "/wholesale/share-links/%s/revoke" % ids["deal_id"], None),

        # -- Phase 5: contract templates and the document lifecycle ---------
        #
        # A contract template is the form a customer's ATTORNEY wrote for them.
        # Reading another tenant's is reading their lawyer's work product, and
        # editing one is worse: the next deal that organization papers would
        # use a form a stranger changed.
        ("patch", "/wholesale/contract-templates/%s" % ids["template_id"],
         {"name": "taken", "jurisdiction": "XX"}),
        ("post", "/wholesale/contract-templates/%s/archive" % ids["template_id"],
         None),
        # The fill sheet is every party, price and date on somebody's deal in
        # one payload. It is the single most concentrated read in the module.
        ("get", "/wholesale/deals/%s/fill-sheet" % d, None),
        # Moving a stranger's document to `signed` would fabricate an executed
        # contract in their own audit trail.
        ("post", "/wholesale/documents/%s/status" % ids["document_id"],
         {"status": "approved"}),
        ("post", "/wholesale/documents/%s/signature-request" % ids["document_id"],
         {"parties": [{"name": "attacker", "email": "a@example.com"}]}),
    ] + evosense_attacks(ids)


def evosense_attacks(ids):
    """Phase 7. Acquisition intelligence is the most sensitive thing a
    wholesaler holds before a deal exists: who is distressed, who inherited,
    what they will take. Every route that names one of it is attacked."""
    ep, es = ids["es_property_id"], ids["es_strategy_id"]
    base = "/wholesale/evosense"
    return [
        ("get", "%s/properties/%s" % (base, ep), None),
        ("post", "%s/properties/%s/enrich" % (base, ep), {"approved": True}),
        ("post", "%s/properties/%s/outreach" % (base, ep), None),
        ("post", "%s/properties/%s/reply" % (base, ep), {"text": "STOP", "delivery": "manual_entry"}),
        ("post", "%s/properties/%s/nurture" % (base, ep), {"choice": "30_days"}),
        ("post", "%s/properties/%s/promote" % (base, ep), {}),
        ("post", "%s/properties/%s/feedback" % (base, ep), {"kind": "BAD_FIT"}),
        ("post", "%s/properties/%s/contacts" % (base, ep), {"kind": "phone", "value": "2145559999"}),
        ("post", "%s/properties/%s/contacts/%s/wrong-party" % (base, ep, ids["es_contact_id"]), None),
        ("post", "%s/properties/%s/signals" % (base, ep), {"signal_type": "VACANT"}),
        ("post", "%s/properties/%s/rescore" % (base, ep), None),
        ("post", "%s/properties/%s/retry-reading" % (base, ep), None),
        ("post", "%s/routing-reviews/%s" % (base, ids["es_routing_review_id"]),
         {"engagement_id": None}),
        ("post", "%s/handoffs/%s" % (base, ids["es_handoff_id"]), {"status": "dismissed"}),
        ("post", "%s/identity-reviews/%s" % (base, ids["es_review_id"]), {"action": "new"}),
        ("get", "%s/strategies/%s" % (base, es), None),
        ("patch", "%s/strategies/%s" % (base, es), {"name": "taken"}),
        ("post", "%s/strategies/%s/clone" % (base, es), None),
        ("post", "%s/strategies/%s/hunt" % (base, es), None),
        ("post", "%s/strategies/%s/activate" % (base, es), None),
        ("post", "%s/strategies/%s/pause" % (base, es), None),
        ("post", "%s/strategies/%s/resume" % (base, es), None),
        ("post", "%s/strategies/%s/archive" % (base, es), None),
        # DFW acquisition: raw evidence, and the pilot rollback / restore.
        ("get", "%s/properties/%s/observations/%s/raw" % (base, ep, ids["es_observation_id"]), None),
        ("post", "%s/strategies/%s/pilot-archive" % (base, es), None),
        ("post", "%s/strategies/%s/pilot-restore" % (base, es), None),
        # Priority 3: a re-derivation run is a snapshot of every property's
        # derived state; applying or rolling back someone else's would rewrite
        # their records.
        ("get", "%s/reprocess/%s" % (base, ids["es_reprocess_run_id"]), None),
        ("post", "%s/reprocess/%s/apply" % (base, ids["es_reprocess_run_id"]),
         {"confirm": "APPLY %s" % ids["es_reprocess_run_id"]}),
        ("post", "%s/reprocess/%s/rollback" % (base, ids["es_reprocess_run_id"]),
         {"confirm": "ROLLBACK %s" % ids["es_reprocess_run_id"]}),
    ]


def upload_attacks(ids):
    """The multipart half. Separate because these need `files=`, not `json=`.

    An upload that lands in another tenant's deal is worse than a read: it puts
    the attacker's bytes inside the victim's document drawer.
    """
    png = (b"\x89PNG\r\n\x1a\n" + b"\x00" * 64, "planted.png", "image/png")
    # (path, file tuple, form fields, upload field name). The field name is
    # explicit because the batch endpoint takes `files`, and an attack that
    # sent the wrong field would be refused by VALIDATION rather than by the
    # tenant check — a pass for the wrong reason.
    return [
        ("/wholesale/properties/%s/photos" % ids["property_id"], png, {}, "file"),
        ("/wholesale/properties/%s/photos/batch" % ids["property_id"], png, {},
         "files"),
        ("/wholesale/comps/%s/photo" % ids["comp_id"], png, {}, "file"),
        ("/wholesale/deals/%s/documents/upload" % ids["deal_id"], png,
         {"doc_type": "purchase_contract"}, "file"),
        ("/wholesale/outreach/%s/proof-of-funds" % ids["outreach_id"], png, {},
         "file"),
    ]


def test_no_endpoint_of_another_tenant_answers_an_attacker(
        client, theirs, other_headers):
    """The whole point of the file. Not one 2xx."""
    failures = []
    for method, path, payload in attacks(theirs):
        kwargs = {"headers": other_headers}
        if payload is not None:
            kwargs["json"] = payload
        response = getattr(client, method)(path, **kwargs)
        if response.status_code < 400:
            failures.append("%s %s -> %s %s"
                            % (method.upper(), path, response.status_code,
                               response.text[:200]))

    for path, (data, name, ctype), form, field in upload_attacks(theirs):
        response = client.post(path, headers=other_headers,
                               files={field: (name, data, ctype)}, data=form)
        if response.status_code < 400:
            failures.append("UPLOAD %s -> %s %s"
                            % (path, response.status_code, response.text[:200]))

    assert not failures, "CROSS-TENANT ACCESS GRANTED:\n" + "\n".join(failures)


def test_the_attacker_is_denied_by_absence_not_by_permission(
        client, theirs, other_headers):
    """404 over 403 on the read paths: a 403 confirms the id is real.

    Only asserted where the module controls the shape of the refusal — a
    request rejected earlier, by validation, is still a refusal.
    """
    for path in ("/wholesale/deals/%s" % theirs["deal_id"],
                 "/wholesale/deals/%s/matches" % theirs["deal_id"],
                 "/wholesale/deals/%s/cadence" % theirs["deal_id"],
                 "/wholesale/properties/%s/enrichment" % theirs["property_id"]):
        assert client.get(path, headers=other_headers).status_code == 404, path


def test_the_attack_did_not_change_anything(client, theirs, auth_headers):
    """Belt and braces: the owner's deal is where they left it."""
    room = ok(client.get("/wholesale/deals/%s" % theirs["deal_id"],
                         headers=auth_headers))
    assert room["property"]["city"] == "Dallas"
    assert room["deal"]["stage"] not in ("dead", "closed")
    buyers = ok(client.get("/wholesale/buyers", headers=auth_headers,
                           params={"include_test": True}))
    assert buyers["buyers"][0]["display_name"] == "Victim Capital"


def test_every_id_bearing_wholesale_route_is_in_the_attack_list(theirs):
    """An endpoint added later without a line above fails here, not in the wild.

    Paths that take no identifier are excluded: they resolve the workspace from
    the caller's own token and there is nothing of anybody else's to name.
    """
    # Reduce each attacked path back to its route template by substituting the
    # exact id values the fixture handed out — no guessing at what looks like
    # an id, which is how this check previously reported a covered endpoint as
    # missing.
    values = {str(v) for v in theirs.values() if v}

    def template(path):
        return "/".join("{}" if part in values else part
                        for part in path.split("/"))

    # Keyed on METHOD AND PATH, not path alone.
    #
    # This check used to compare paths only, which meant a new METHOD on an
    # already-covered path passed silently. DELETE /wholesale/buyers/{id} was
    # added in Phase 3 and this file reported full coverage, because PATCH on
    # the same path was in the list — and a delete is exactly the verb you
    # least want an untested stranger to reach. Found by adding that endpoint
    # and noticing the guard did not go red.
    listed = {(method.upper(), template(path)) for method, path, _ in attacks(theirs)}
    listed |= {("POST", template(path)) for path, _, _, _ in upload_attacks(theirs)}

    missing = []
    for route in app.routes:
        path = getattr(route, "path", "")
        if not path.startswith("/wholesale/") or "{" not in path:
            continue
        squashed = "/".join("{}" if part.startswith("{") else part
                            for part in path.split("/"))
        for method in sorted(getattr(route, "methods", set()) - {"HEAD", "OPTIONS"}):
            if (method.upper(), squashed) in listed:
                continue
            missing.append("%s %s" % (method, path))

    assert not missing, (
        "These wholesale endpoints name a record but are not attacked in this "
        "file:\n" + "\n".join(sorted(set(missing))))
