"""Prospect logos in EvoSys Sales outreach (owner request 2026-11-17): a manager
saves the business's logo on the lead; the email shows it only when it is marked
usable; the public picture link is signed; lead lists never load the bytes."""
import base64
import io
from datetime import datetime

import pytest
from PIL import Image

from app.models.sales_models import Opportunity, OpportunityEvent
from app.services.evosys_sales import logos as L
from app.services.evosys_sales import outreach as O

from tests.test_sales_workspace import (  # noqa: F401  (fixtures)
    platform, brand, brand_b, rep, manager, rep_headers, manager_headers,
    _make_sales_user, _add_membership, _token_headers, _SEQ,
)


@pytest.fixture(autouse=True)
def _brand_env(monkeypatch, brand):
    monkeypatch.setenv("EVOSYS_SALES_BRAND_ID", brand.id)
    monkeypatch.setenv("PROGRAM_ASSET_BASE_URL", "https://api.example.test")


def _png(w=1200, h=600, color=(200, 30, 90)) -> str:
    buf = io.BytesIO()
    Image.new("RGB", (w, h), color).save(buf, format="JPEG")
    return base64.b64encode(buf.getvalue()).decode()


def _lead(db, brand, owner=None, company="Sparkle Co"):
    o = Opportunity(brand_sales_org_id=brand.id, owner_user_id=owner.id if owner else None,
                    company_name=company, contact_name="Ana Ruiz", phone="2145550100",
                    email="owner@sparkle.test", stage="prospect", status="open")
    db.add(o); db.commit()
    return o


STEP = {"day": 0, "channel": "email", "subject": "Hi {first_name}", "body": "A note for you."}


class TestUpload:
    def test_manager_saves_logo_resized_to_png_and_logged(self, client, db_session, brand,
                                                          manager, manager_headers):
        o = _lead(db_session, brand, manager)
        r = client.put("/sales/evosys/leads/%s/logo" % o.id,
                       json={"image_base64": "data:image/jpeg;base64," + _png(), "quality": "clean"},
                       headers=manager_headers)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["logo_in_email"] is True and body["logo_quality"] == "clean"
        db_session.refresh(o)
        im = Image.open(io.BytesIO(o.logo_png))
        assert im.format == "PNG" and im.size[0] <= L.MAX_W and im.size[1] <= L.MAX_H
        assert db_session.query(OpportunityEvent).filter_by(
            opportunity_id=o.id, summary="Logo saved (clean)").count() == 1

    def test_rep_cannot_save_logo(self, client, db_session, brand, rep, rep_headers):
        o = _lead(db_session, brand, rep)
        r = client.put("/sales/evosys/leads/%s/logo" % o.id,
                       json={"image_base64": _png(), "quality": "clean"}, headers=rep_headers)
        assert r.status_code == 403

    def test_not_a_picture_and_bad_quality_refused(self, client, db_session, brand, manager_headers):
        o = _lead(db_session, brand)
        bad = base64.b64encode(b"not an image").decode()
        assert client.put("/sales/evosys/leads/%s/logo" % o.id, json={"image_base64": bad},
                          headers=manager_headers).status_code == 400
        assert client.put("/sales/evosys/leads/%s/logo" % o.id,
                          json={"image_base64": _png(), "quality": "great"},
                          headers=manager_headers).status_code == 400

    def test_remove_clears_everything(self, client, db_session, brand, manager_headers):
        o = _lead(db_session, brand)
        client.put("/sales/evosys/leads/%s/logo" % o.id, json={"image_base64": _png()},
                   headers=manager_headers)
        assert client.delete("/sales/evosys/leads/%s/logo" % o.id,
                             headers=manager_headers).status_code == 200
        db_session.refresh(o)
        assert o.logo_png is None and o.logo_quality is None and o.logo_updated_at is None


class TestEmail:
    def test_clean_logo_goes_at_top_of_email(self, db_session, brand):
        o = _lead(db_session, brand, company="Ana's <Cleaning>")
        L.store(o, L.normalize(base64.b64decode(_png())), "clean", datetime(2026, 11, 17, 20, 0))
        db_session.commit()
        s = O.get_settings(db_session, brand.id)
        msg = O.render_email(o, s, STEP)
        assert msg["logo"] is True
        assert msg["html"].startswith('<p style="margin:0 0 16px 0"><img src="https://api.example.test/evosys-sales-public/logo/')
        assert 'alt="Ana&#x27;s &lt;Cleaning&gt;"' in msg["html"]

    def test_rough_logo_and_no_logo_leave_email_unchanged(self, db_session, brand):
        o = _lead(db_session, brand)
        s = O.get_settings(db_session, brand.id)
        before = O.render_email(o, s, STEP)["html"]
        assert "<img" not in before
        L.store(o, L.normalize(base64.b64decode(_png())), "rough", datetime.utcnow())
        db_session.commit()
        msg = O.render_email(o, s, STEP)
        assert msg["logo"] is False and msg["html"] == before

    def test_name_only_is_usable(self, db_session, brand):
        o = _lead(db_session, brand)
        L.store(o, L.normalize(base64.b64decode(_png())), "name_only", datetime.utcnow())
        db_session.commit()
        assert O.render_email(o, O.get_settings(db_session, brand.id), STEP)["logo"] is True


class TestPublicLink:
    def test_signed_link_serves_png_and_forged_token_refused(self, client, db_session, brand):
        o = _lead(db_session, brand)
        L.store(o, L.normalize(base64.b64decode(_png())), "clean", datetime.utcnow())
        db_session.commit()
        r = client.get("/evosys-sales-public/logo/%s.png" % L.make_token(o.id))
        assert r.status_code == 200 and r.headers["content-type"] == "image/png"
        assert r.content[:8] == b"\x89PNG\r\n\x1a\n"
        assert client.get("/evosys-sales-public/logo/%s.png" % o.id).status_code == 404
        from app.services.programs import unsubscribe as _u
        # an opt-out token for the same lead is not a logo token
        assert client.get("/evosys-sales-public/logo/%s.png" % _u.make_token("evs:" + o.id)).status_code == 404

    def test_no_logo_is_404(self, client, db_session, brand):
        o = _lead(db_session, brand)
        assert client.get("/evosys-sales-public/logo/%s.png" % L.make_token(o.id)).status_code == 404


class TestLists:
    def test_lead_list_shows_logo_link_without_loading_bytes(self, client, db_session, brand,
                                                            manager, manager_headers):
        o = _lead(db_session, brand, manager)
        L.store(o, L.normalize(base64.b64decode(_png())), "rough", datetime.utcnow())
        db_session.commit()
        rows = client.get("/sales/evosys/leads", headers=manager_headers).json()["leads"]
        row = next(x for x in rows if x["id"] == o.id)
        assert row["logo_url"].startswith("https://api.example.test/evosys-sales-public/logo/")
        assert row["logo_quality"] == "rough" and row["logo_in_email"] is False
