"""The 'One lead' form sends a one-row file through the existing importer;
this holds that the form's column names map and the lead is created."""
import io

from app.models.sales_models import Opportunity
from tests.test_evosys_sales_walkthrough import world, sales, _h  # noqa: F401


def test_one_lead_form_row_imports(world):
    p = world
    c, b, H, db = p["c"], p["brand"].id, p["H"], p["db"]
    csv = ('"company","contact","phone","email","city","state","industry","source ID","notes"\n'
           '"Sparkle Pros","Ana Ruiz","214-555-0177","","Dallas","TX","Commercial cleaning","ONE-ABC123",'
           '"Offices and homes. Spanish only - write to them in Spanish"\n')
    r = c.post("/intake/sales/%s/batches" % b, headers=H, data={"source_system": "facebook_ads"},
               files={"file": ("one-lead.csv", io.BytesIO(csv.encode()), "text/csv")})
    assert r.status_code == 200, r.text
    bid = r.json()["id"]
    a = c.post("/intake/sales/%s/batches/%s/analyze" % (b, bid), headers=H)
    assert a.status_code == 200 and (a.json().get("counts") or {}).get("ready") == 1, a.text
    rep = c.post("/intake/sales/%s/batches/%s/commit" % (b, bid), headers=H, json={"confirm_target_id": b}).json()
    assert rep["created"] == 1
    o = db.query(Opportunity).filter(Opportunity.source_record_id == "ONE-ABC123").one()
    assert (o.company_name, o.contact_name, o.phone) == ("Sparkle Pros", "Ana Ruiz", "214-555-0177")
