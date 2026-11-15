"""CONTRACTOR ONBOARDING — invite / resend, the step-by-step progress view, per-document
review with correction notes, and in-app agreement signing.

Synthetic people, synthetic documents and a synthetic agreement template only.
"""
import itertools

import pytest

from app.models.models import Platform, User
from app.models.sales_models import BrandSalesOrg
from app.models.sales_contractor_models import SalesAgreementEnvelope, SalesInviteSend
from app.models.sales_team_models import SalesOnboarding, SalesOnboardingDocument
from app.models.staff_models import StaffActivation
from app.services import capabilities as caps
from app.services.auth_service import create_access_token, hash_password

_SEQ = itertools.count(1)
PDF = b"%PDF-1.4\n% synthetic test document\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"
SYNTHETIC_AGREEMENT = ("# Synthetic Contractor Agreement\n\nThis is a SYNTHETIC test agreement used only to "
                       "exercise the signing workflow. It has no legal effect.\n\n## 1. Services\n\n"
                       "The contractor sells EvoSys Pro on commission.")


def _user(db, name, role="advisor"):
    u = User(organization_id=None, email="co%d@example.test" % next(_SEQ), password_hash=hash_password("x"),
             full_name=name, role=role, must_change_password=False)
    db.add(u); db.commit()
    return u


@pytest.fixture()
def w(db_session, monkeypatch):
    db = db_session
    sent = []
    import app.services.email_service as es
    monkeypatch.setattr(es, "send_email_via_provider", lambda **kw: sent.append(kw) or {"success": True})
    plat = Platform(name="EvoSys", slug="evosys-co-%d" % next(_SEQ)); db.add(plat); db.commit()
    brand = BrandSalesOrg(platform_id=plat.id, name="EvoSys Pro Sales", slug="evosyspro-sales")
    db.add(brand); db.commit()
    god = _user(db, "God", role="god_admin")
    owner = _user(db, "Owner One")
    caps.set_brand_grants(db, owner, brand.id, brand.name, god, ["sales_program_owner"])
    return dict(db=db, brand=brand, god=god, owner=owner, sent=sent)


def H(w, u):
    return {"Authorization": "Bearer " + create_access_token(u, w["db"])}


def _add_contractor(client, w, actor, seat="rep", manager_id=None, name="Casey Contractor"):
    email = "new%d@example.test" % next(_SEQ)
    r = client.post("/sales-team/invite", headers=H(w, actor),
                    json={"email": email, "full_name": name, "seat": seat, "manager_id": manager_id})
    assert r.status_code == 200, r.text
    u = w["db"].query(User).filter(User.email == email).first()
    return u, r.json()


def _activate(w, u):
    u.must_change_password = False
    w["db"].commit()


def _onb(w, u):
    return w["db"].query(SalesOnboarding).filter(SalesOnboarding.user_id == u.id).one()


def _profile(client, w, viewer, u):
    r = client.get("/sales-team/members/%s/profile" % u.id, headers=H(w, viewer))
    assert r.status_code == 200, r.text
    return r.json()


def _fill(client, w, u, kinds=("w9", "photo_id_front", "photo_id_back", "i9")):
    h = H(w, u)
    r = client.put("/sales-team/onboarding/me/application", headers=h,
                   json={"legal_name": u.full_name, "home_address": "1 Synthetic Way", "phone": "555-0100",
                         "email": u.email, "start_date": "2026-12-01"})
    assert r.status_code == 200, r.text
    for k in kinds:
        r = client.post("/sales-team/onboarding/me/documents/%s" % k, headers=h,
                        files={"file": ("%s.pdf" % k, PDF, "application/pdf")})
        assert r.status_code == 200, r.text


def _approved_template(client, w, countersign=False):
    r = client.post("/sales-team/agreement-templates", headers=H(w, w["owner"]),
                    json={"title": "Independent Sales Representative Agreement (synthetic)",
                          "version_label": "TEST-1", "exhibit_version": "A-TEST-1",
                          "body_text": SYNTHETIC_AGREEMENT, "requires_countersign": countersign})
    assert r.status_code == 200, r.text
    t = r.json()
    r = client.post("/sales-team/agreement-templates/%s/approve" % t["id"], headers=H(w, w["owner"]),
                    json={"approved_for_signing": True})
    assert r.status_code == 200, r.text
    return t


# ── invite / resend / account ────────────────────────────────────────────────

def test_add_contractor_then_resend_expires_the_old_link(client, w):
    u, out = _add_contractor(client, w, w["owner"])
    assert out["email_sent"] is True and out["new_account"] is True and out["setup_url"] is None
    sends = w["db"].query(SalesInviteSend).filter_by(user_id=u.id).all()
    assert [(s.kind, s.email_ok, s.link_issued) for s in sends] == [("invite", True, True)]
    p = _profile(client, w, w["owner"], u)
    steps = {s["key"]: s for s in p["onboarding"]["steps"]}
    assert steps["account"]["done"] is False
    assert p["onboarding"]["next"] == {"who": "contractor", "what": "Open the invite email and set a password"}
    first = w["db"].query(StaffActivation).filter_by(user_id=u.id).one()
    r = client.post("/sales-team/members/%s/resend-invite" % u.id, headers=H(w, w["owner"]))
    assert r.status_code == 200, r.text
    assert r.json()["new_link_issued"] is True and r.json()["email_sent"] is True
    w["db"].refresh(first)
    assert first.status == "revoked"
    # The token never appears in what the owner gets back (email worked).
    assert r.json()["setup_url"] is None
    # Nothing in the invite email is an attachment.
    assert all(not m.get("attachments") for m in w["sent"])


def test_invite_to_existing_account_needs_no_new_link(client, w):
    existing = _user(w["db"], "Existing Person")
    r = client.post("/sales-team/invite", headers=H(w, w["owner"]),
                    json={"email": existing.email, "full_name": existing.full_name, "seat": "rep"})
    assert r.status_code == 200 and r.json()["new_account"] is False
    r = client.post("/sales-team/members/%s/resend-invite" % existing.id, headers=H(w, w["owner"]))
    assert r.json()["new_link_issued"] is False and r.json()["existing_account"] is True
    acct = _profile(client, w, w["owner"], existing)["onboarding"]["account"]
    assert acct["verified"] is True and "Already had" in acct["how"]


# ── document review and corrections ──────────────────────────────────────────

def test_review_send_back_fix_and_approve(client, w):
    u, _ = _add_contractor(client, w, w["owner"])
    _activate(w, u)
    _fill(client, w, u, kinds=("contractor_agreement", "w9", "photo_id_front", "photo_id_back", "i9"))
    assert client.post("/sales-team/onboarding/me/submit", headers=H(w, u)).status_code == 200
    onb = _onb(w, u)
    docs = {d["kind"]: d for d in _profile(client, w, w["owner"], u)["onboarding"]["documents"]}
    assert docs["w9"]["received"] and docs["w9"]["review"] == "not_reviewed" and docs["w9"]["filename"]
    # Mark the ID front as needing correction, with a note.
    r = client.post("/sales-team/onboarding/%s/documents/%s/review" % (onb.id, docs["photo_id_front"]["document_id"]),
                    headers=H(w, w["owner"]), json={"status": "needs_correction", "note": "Photo is blurry"})
    assert r.status_code == 200, r.text
    # A correction needs a note.
    assert client.post("/sales-team/onboarding/%s/documents/%s/review" % (onb.id, docs["w9"]["document_id"]),
                       headers=H(w, w["owner"]), json={"status": "needs_correction"}).status_code == 422
    # Approval is blocked while a file is marked for correction.
    assert client.post("/sales-team/onboarding/%s/approve" % onb.id, headers=H(w, w["owner"])).status_code == 422
    r = client.post("/sales-team/onboarding/%s/send-back" % onb.id, headers=H(w, w["owner"]),
                    json={"note": "Please retake the ID photo"})
    assert r.status_code == 200 and r.json()["email_sent"] is True
    assert r.json()["corrections"] == [{"label": "Photo ID — front", "note": "Photo is blurry",
                                        "step_link": "/sales-onboarding#doc-photo_id_front"}]
    mail = w["sent"][-1]
    assert "/sales-onboarding#doc-photo_id_front" in mail["body_html"] and not mail.get("attachments")
    # The contractor sees exactly what to fix.
    me = client.get("/sales-team/onboarding/me", headers=H(w, u)).json()
    assert me["journey"]["corrections"][0]["note"] == "Photo is blurry"
    assert me["journey"]["next"]["who"] == "contractor"
    # Re-upload clears that file's review; resubmit; approve (unreviewed files are accepted at approval).
    r = client.post("/sales-team/onboarding/me/documents/photo_id_front", headers=H(w, u),
                    files={"file": ("front2.pdf", PDF, "application/pdf")})
    assert r.status_code == 200
    assert client.post("/sales-team/onboarding/me/submit", headers=H(w, u)).status_code == 200
    assert client.post("/sales-team/onboarding/%s/approve" % onb.id, headers=H(w, w["owner"])).status_code == 200
    docs = _profile(client, w, w["owner"], u)["onboarding"]["documents"]
    assert all(d["review"] == "accepted" for d in docs)


def test_leaders_see_status_only_and_cannot_review(client, w):
    sevp, _ = _add_contractor(client, w, w["owner"], seat="sevp", name="Sam Sevp")
    _activate(w, sevp)
    w["db"].query(SalesOnboarding).filter_by(user_id=sevp.id).one().status = "approved"
    w["db"].commit()
    rep, _ = _add_contractor(client, w, sevp, seat="rep", manager_id=sevp.id, name="Riley Rep")
    _activate(w, rep)
    _fill(client, w, rep)
    p = _profile(client, w, sevp, rep)
    text = str(p["onboarding"])
    assert p["onboarding"]["can_act"] is False
    assert "document_id" not in text and "filename" not in text and ".pdf" not in text
    onb = _onb(w, rep)
    doc = w["db"].query(SalesOnboardingDocument).filter_by(onboarding_id=onb.id, kind="w9").one()
    assert client.post("/sales-team/onboarding/%s/documents/%s/review" % (onb.id, doc.id), headers=H(w, sevp),
                       json={"status": "accepted"}).status_code == 403
    assert client.get("/sales-team/onboarding/%s/documents/%s" % (onb.id, doc.id), headers=H(w, sevp)).status_code == 403
    assert client.post("/sales-team/onboarding/%s/send-agreement" % onb.id, headers=H(w, sevp),
                       json={"template_id": "x"}).status_code == 403
    # The Sr EVP may resend his own rep's invite.
    assert client.post("/sales-team/members/%s/resend-invite" % rep.id, headers=H(w, sevp)).status_code == 200


# ── agreement signing ────────────────────────────────────────────────────────

def test_draft_marked_text_can_never_be_approved_for_signing(client, w):
    r = client.post("/sales-team/agreement-templates", headers=H(w, w["owner"]),
                    json={"title": "Rep Agreement", "version_label": "D-1", "exhibit_version": "A-D1",
                          "body_text": "DRAFT FOR LEGAL REVIEW — NOT FOR SIGNATURE\n\n" + SYNTHETIC_AGREEMENT})
    t = r.json()
    assert t["is_draft_text"] is True and t["status"] == "draft"
    r = client.post("/sales-team/agreement-templates/%s/approve" % t["id"], headers=H(w, w["owner"]),
                    json={"approved_for_signing": True})
    assert r.status_code == 422
    u, _ = _add_contractor(client, w, w["owner"])
    r = client.post("/sales-team/onboarding/%s/send-agreement" % _onb(w, u).id, headers=H(w, w["owner"]),
                    json={"template_id": t["id"]})
    assert r.status_code == 422


def test_send_read_sign_and_store_the_signed_copy(client, w):
    t = _approved_template(client, w)
    u, _ = _add_contractor(client, w, w["owner"], name="Jordan Synthetic")
    _activate(w, u)
    _fill(client, w, u)
    onb = _onb(w, u)
    r = client.post("/sales-team/onboarding/%s/send-agreement" % onb.id, headers=H(w, w["owner"]),
                    json={"template_id": t["id"]})
    assert r.status_code == 200, r.text
    assert r.json()["agreement"]["status"] == "sent"
    assert "/sales-onboarding#agreement" in w["sent"][-1]["body_html"] and not w["sent"][-1].get("attachments")
    # The contractor reads it: only HIS compensation terms.
    me = client.get("/sales-team/onboarding/me/agreement", headers=H(w, u)).json()
    html = me["agreement"]["document_html"]
    assert "Synthetic Contractor Agreement" in html and "TEST-1" in html and "A-TEST-1" in html
    assert "$500" in html and "Salesperson" in html
    assert "Area Sales Manager" not in html and "Sr EVP" not in html and "$200" not in html and "$300" not in html
    assert client.get("/sales-team/onboarding/me/agreement/document", headers=H(w, u)).status_code == 200
    # Explicit consent and his own legal name are required.
    sign = "/sales-team/onboarding/me/agreement/sign"
    assert client.post(sign, headers=H(w, u), json={"typed_name": "Jordan Synthetic", "consent": False}).status_code == 422
    assert client.post(sign, headers=H(w, u), json={"typed_name": "Someone Else", "consent": True}).status_code == 422
    other = _user(w["db"], "Other Person")
    assert client.post(sign, headers=H(w, other), json={"typed_name": "Other Person", "consent": True}).status_code in (403, 404)
    r = client.post(sign, headers=H(w, u), json={"typed_name": "jordan  synthetic", "consent": True})
    assert r.status_code == 200, r.text
    a = r.json()["agreement"]
    assert a["status"] == "completed" and a["has_signed_copy"] and a["final_sha256"]
    # Stored as his contractor-agreement file; checklist item done.
    doc = w["db"].query(SalesOnboardingDocument).filter_by(onboarding_id=onb.id, kind="contractor_agreement",
                                                            is_current=True).one()
    assert doc.content_type == "application/pdf"
    pdf = client.get("/sales-team/onboarding/me/agreement/signed-copy", headers=H(w, u))
    assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF")
    assert client.get("/sales-team/onboarding/%s/signed-agreement" % onb.id, headers=H(w, w["owner"])).status_code == 200
    env = w["db"].query(SalesAgreementEnvelope).filter_by(onboarding_id=onb.id).one()
    assert env.signer_typed_name == "jordan  synthetic" and env.signed_at and env.version_label == "TEST-1"
    me = client.get("/sales-team/onboarding/me", headers=H(w, u)).json()
    assert {s["key"]: s["done"] for s in me["journey"]["steps"]}["agreement"] is True
    assert client.post("/sales-team/onboarding/me/submit", headers=H(w, u)).status_code == 200
    assert client.post("/sales-team/onboarding/%s/approve" % onb.id, headers=H(w, w["owner"])).status_code == 200


def test_countersignature_and_corrections(client, w):
    t = _approved_template(client, w, countersign=True)
    u, _ = _add_contractor(client, w, w["owner"], name="Morgan Synthetic")
    _activate(w, u)
    _fill(client, w, u)
    onb = _onb(w, u)
    client.post("/sales-team/onboarding/%s/send-agreement" % onb.id, headers=H(w, w["owner"]), json={"template_id": t["id"]})
    # The contractor spots a problem -> correction needed, approval blocked.
    r = client.post("/sales-team/onboarding/me/agreement/correction", headers=H(w, u), json={"note": "Start date is wrong"})
    assert r.status_code == 200 and r.json()["agreement"]["status"] == "correction_needed"
    assert client.post("/sales-team/onboarding/me/agreement/sign", headers=H(w, u),
                       json={"typed_name": "Morgan Synthetic", "consent": True}).status_code == 409
    # Owner sends a fresh version; contractor signs; owner countersigns.
    client.post("/sales-team/onboarding/%s/send-agreement" % onb.id, headers=H(w, w["owner"]), json={"template_id": t["id"]})
    r = client.post("/sales-team/onboarding/me/agreement/sign", headers=H(w, u),
                    json={"typed_name": "Morgan Synthetic", "consent": True})
    assert r.json()["agreement"]["status"] == "awaiting_countersign"
    p = _profile(client, w, w["owner"], u)
    assert p["onboarding"]["next"] == {"who": "owner", "what": "Countersign the agreement"} or \
        p["onboarding"]["next"]["who"] in ("owner", "contractor")
    assert client.post("/sales-team/onboarding/%s/agreement-countersign" % onb.id, headers=H(w, w["owner"]),
                       json={"typed_name": "Wrong Name"}).status_code == 422
    r = client.post("/sales-team/onboarding/%s/agreement-countersign" % onb.id, headers=H(w, w["owner"]),
                    json={"typed_name": "Owner One"})
    assert r.status_code == 200 and r.json()["agreement"]["status"] == "completed"
    envs = w["db"].query(SalesAgreementEnvelope).filter_by(onboarding_id=onb.id).all()
    assert sorted(e.status for e in envs) == ["completed", "void"]


def test_audit_records_views_and_downloads(client, w):
    from app.models.models import AuditLogEntry
    u, _ = _add_contractor(client, w, w["owner"])
    _activate(w, u)
    _fill(client, w, u)
    onb = _onb(w, u)
    doc = w["db"].query(SalesOnboardingDocument).filter_by(onboarding_id=onb.id, kind="w9").one()
    assert client.get("/sales-team/onboarding/%s/documents/%s?inline=true" % (onb.id, doc.id),
                      headers=H(w, w["owner"])).status_code == 200
    assert client.get("/sales-team/onboarding/%s/documents/%s" % (onb.id, doc.id),
                      headers=H(w, w["owner"])).status_code == 200
    acts = [a.action for a in w["db"].query(AuditLogEntry).filter(AuditLogEntry.target_id == u.id).all()]
    assert "sales_onboarding_document_viewed" in acts and "sales_onboarding_document_downloaded" in acts


def test_getting_started_follows_real_records(client, w):
    from app.models.sales_models import Opportunity
    u, _ = _add_contractor(client, w, w["owner"], name="Gina Starter")
    _activate(w, u)
    gs = client.get("/sales-team/getting-started", headers=H(w, u)).json()
    assert gs["approved"] is False and gs["next"]["key"] == "approved" and gs["next"]["link"] == "/sales-onboarding"
    onb = _onb(w, u); onb.status = "approved"; w["db"].commit()
    gs = client.get("/sales-team/getting-started", headers=H(w, u)).json()
    assert gs["next"]["key"] == "prospect" and gs["next"]["link"] == "/sales/pipeline?add=1"
    o = Opportunity(brand_sales_org_id=w["brand"].id, company_name="Synthetic Diner", owner_user_id=u.id,
                    status="open", stage="contacted", next_action="Call back")
    w["db"].add(o); w["db"].commit()
    gs = client.get("/sales-team/getting-started", headers=H(w, u)).json()
    done = {s["key"]: s["done"] for s in gs["steps"]}
    assert done["prospect"] and done["contact"] and done["follow_up"] and not done["quote"]
    assert gs["next"]["key"] == "quote" and gs["next"]["link"] == "/sales/opportunities/%s/quote" % o.id
    # The owner sees the same progress on the profile.
    p = _profile(client, w, w["owner"], u)
    assert p["getting_started"]["next"]["label"] == "Build Quote"
