"""Photo-assisted repairs: local quality checks, an AI draft priced from the DFW table
(never by the model), hidden damage named as unknown, nothing saved without a person,
and an inspection estimate never silently replaced. The vision call is a fake client."""
import io
import json
import random

import pytest

from app.models.wholesale_models import WholesaleDeal, WholesaleEvent
from app.services import wholesale_photo_repairs as PR

ES = "/wholesale/evosense"


def ok(r, what=""):
    assert r.status_code in (200, 201), "%s -> %s %s" % (what or r.url, r.status_code, r.text[:400])
    return r.json()


def jpeg(w=900, h=700, kind="good", seed=1):
    from PIL import Image, ImageDraw
    rnd = random.Random(seed)
    if kind == "dark":
        im = Image.new("RGB", (w, h), (8, 8, 8))
    elif kind == "flat":
        im = Image.new("RGB", (w, h), (128, 128, 128))
    else:
        im = Image.new("RGB", (w, h), (180, 170, 160))
        d = ImageDraw.Draw(im)
        for _ in range(400):
            x, y = rnd.randint(0, w), rnd.randint(0, h)
            d.rectangle([x, y, x + rnd.randint(5, 60), y + rnd.randint(5, 60)],
                        outline=(rnd.randint(0, 255),) * 3, width=2)
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=90)
    return buf.getvalue()


def test_quality_checks_are_local_and_explain_themselves():
    assert PR.quality(jpeg())["ok"] is True
    assert "Too small" in PR.quality(jpeg(200, 150))["problems"][0]
    assert any("dark" in p for p in PR.quality(jpeg(kind="dark"))["problems"])
    assert any("Blurry" in p for p in PR.quality(jpeg(kind="flat"))["problems"])
    assert PR.quality(b"not an image")["ok"] is False


class FakeClient:
    def __init__(self, payload):
        self.payload, self.calls = payload, []

        class _C:
            def __init__(s, outer):
                s.outer = outer

            def create(s, **kw):
                s.outer.calls.append(kw)

                class R:
                    class _Ch:
                        class message:
                            content = json.dumps(self.payload)
                    choices = [_Ch]
                    usage = None
                return R
        self.chat = type("Chat", (), {})()
        self.chat.completions = _C(self)


AI = {"photos": [
    {"index": 0, "area": "roof", "observations": ["Missing shingles on the front slope."],
     "repairs": [{"item": "roof_replace", "quantity": 18, "confidence": "medium", "evidence": "bare decking patches"}]},
    {"index": 1, "area": "kitchen", "observations": ["Original cabinets, laminate counters."],
     "repairs": [{"item": "kitchen_full", "quantity": 1, "confidence": "high", "evidence": "dated cabinets"},
                 {"item": "exterior_paint", "quantity": 1, "confidence": "low", "evidence": "peeling trim"}]},
    {"index": 0, "area": "roof", "observations": [],
     "repairs": [{"item": "roof_replace", "quantity": 12, "confidence": "low", "evidence": "same roof"}]}]}


def _deal_with_photos(client, H, db_session, n=2, extra_bad=True):
    pid = ok(client.post(ES + "/properties", headers=H, json={
        "street_address": "77 Photo Test Ln", "city": "Dallas", "state": "TX", "zip_code": "75216",
        "county": "Dallas", "property_type": "single_family", "square_feet": 1400, "is_test": True}))["property_id"]
    ok(client.post(ES + "/properties/%s/owner" % pid, headers=H, json={"name": "Photo Test Owner"}))
    deal_id = ok(client.post(ES + "/properties/%s/promote" % pid, headers=H, json={}))["deal_id"]
    d = db_session.query(WholesaleDeal).filter(WholesaleDeal.id == deal_id).first()
    imgs = [jpeg(seed=i + 1) for i in range(n)] + ([jpeg(200, 150, seed=9)] if extra_bad else [])
    for i, data in enumerate(imgs):
        ok(client.post("/wholesale/properties/%s/photos" % d.property_id, headers=H,
                       files={"file": ("p%d.jpg" % i, io.BytesIO(data), "image/jpeg")}, data={"category": "other"}),
           "photo upload")
    return deal_id


def test_without_a_vision_provider_the_app_says_so_and_saves_nothing(client, auth_headers, db_session, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    deal_id = _deal_with_photos(client, auth_headers, db_session)
    st = ok(client.get("/wholesale/deals/%s/repairs/photo-ai" % deal_id, headers=auth_headers))
    assert st["available"] is False and "OPENAI_API_KEY" in st["providers"][0]["why"] and st["photos"] == 3
    out = ok(client.post("/wholesale/deals/%s/repairs/photo-analysis" % deal_id, headers=auth_headers, json={}))
    assert out["ai"]["used"] is False and "OPENAI_API_KEY" in out["ai"]["why"] and out["lines"] == []
    assert sum(1 for p in out["photos"] if not p["ok"]) == 1
    assert out["not_visible"] and "manual" in out


def test_ai_draft_is_priced_from_the_dfw_table_and_never_saved_by_itself(client, auth_headers, db_session,
                                                                         monkeypatch):
    from app.routers import wholesale_underwriting_router as R
    monkeypatch.setenv("OPENAI_API_KEY", "test-key-not-used")
    fake = FakeClient(AI)
    monkeypatch.setattr(R, "_vision_client", lambda: fake)
    H = auth_headers
    deal_id = _deal_with_photos(client, H, db_session)
    out = ok(client.post("/wholesale/deals/%s/repairs/photo-analysis" % deal_id, headers=H, json={}))
    assert out["ai"]["used"] is True and len(fake.calls) == 1
    sent = fake.calls[0]["messages"][0]["content"]
    assert sum(1 for c in sent if c["type"] == "image_url") == 2            # the tiny photo was not sent
    assert "Do not give prices" in sent[0]["text"]
    lines = {ln["item_key"]: ln for ln in out["lines"]}
    roof = lines["roof_replace"]
    assert roof["quantity"] == 18 and roof["unit_cost"] == PR.DFW_COSTS["roof_replace"][4]   # merged, priced by table
    assert roof["cost_source"] == PR.COST_SOURCE and roof["cost_as_of"] == PR.COST_AS_OF
    assert lines["exterior_paint"]["quantity"] == 1400                    # per sq ft of living area
    assert out["totals"]["low"] < out["totals"]["base"] < out["totals"]["high"]
    assert {x["category"] for x in out["not_visible"]} >= {"Foundation", "Plumbing", "Electrical"}
    d = db_session.query(WholesaleDeal).filter(WholesaleDeal.id == deal_id).first()
    db_session.refresh(d)
    assert d.repair_estimate is None                                       # a draft saves nothing
    assert db_session.query(WholesaleEvent).filter(WholesaleEvent.deal_id == deal_id,
                                                   WholesaleEvent.action == "repairs.photo_analyzed").count() == 1

    # the person saves the reviewed draft -> MANUAL estimate, MAO recalculated
    items = [{"category": l["category"], "item": l["item"], "quantity": l["quantity"], "unit": l["unit"],
              "unit_cost": l["unit_cost"]} for l in out["lines"]]
    saved = ok(client.post("/wholesale/deals/%s/repairs/itemized" % deal_id, headers=H,
                           json={"items": items, "basis": "photo", "contingency_pct": 15}))
    db_session.refresh(d)
    assert d.repair_status == "MANUAL_ESTIMATE" and float(d.repair_estimate) == pytest.approx(saved["estimate"]["base"])

    # a contractor's numbers become an INSPECTION estimate; a later manual save must confirm
    assert client.post("/wholesale/deals/%s/repairs/itemized" % deal_id, headers=H,
                       json={"items": items, "basis": "contractor"}).status_code == 422
    ok(client.post("/wholesale/deals/%s/repairs/itemized" % deal_id, headers=H,
                   json={"items": [{"item": "Full rehab bid", "quantity": 1, "unit_cost": 41000}],
                         "basis": "contractor", "contractor": "Synthetic Builders LLC", "contingency_pct": 5}))
    db_session.refresh(d)
    assert d.repair_status == "INSPECTION_ESTIMATE"
    r = client.post("/wholesale/deals/%s/repairs/itemized" % deal_id, headers=H, json={"items": items, "basis": "photo"})
    assert r.status_code == 409 and "replace" in r.json()["detail"]
    ok(client.post("/wholesale/deals/%s/repairs/itemized" % deal_id, headers=H,
                   json={"items": items, "basis": "photo", "confirm_replace": True}))


def test_the_daily_cap_stops_further_ai_calls(client, auth_headers, db_session, monkeypatch):
    from app.routers import wholesale_underwriting_router as R
    monkeypatch.setenv("OPENAI_API_KEY", "test-key-not-used")
    monkeypatch.setenv(PR.DAILY_CAP_ENV, "1")
    fake = FakeClient(AI)
    monkeypatch.setattr(R, "_vision_client", lambda: fake)
    deal_id = _deal_with_photos(client, auth_headers, db_session, extra_bad=False)
    ok(client.post("/wholesale/deals/%s/repairs/photo-analysis" % deal_id, headers=auth_headers, json={}))
    out = ok(client.post("/wholesale/deals/%s/repairs/photo-analysis" % deal_id, headers=auth_headers, json={}))
    assert out["ai"]["used"] is False and "limit" in out["ai"]["why"] and len(fake.calls) == 1
