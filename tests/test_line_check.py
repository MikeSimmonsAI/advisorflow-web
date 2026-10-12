"""Line-type lookup: once per number, landlines get no texts."""
from app.services.programs import line_check, send_approval
from tests.test_outreach_program import _lead_for, program  # noqa: F401


class FakeAdapter:
    def __init__(self, line):
        self.line, self.calls = line, 0

    def validate_phone(self, number):
        self.calls += 1
        return {"line_type": self.line, "validation": "valid"}


def test_checked_once_and_landline_blocked_without_email(program):
    db, org, admin = program["db"], program["org"], program["admin"]
    lead = _lead_for(db, org, admin, "L007", phone="2145550107", first="Rosa", email="")
    a = FakeAdapter("landline")
    assert line_check.check_lead(db, lead, a) == "landline"
    assert line_check.check_lead(db, lead, a) == "landline" and a.calls == 1
    assert send_approval.blocker(db, lead) == "Landline - call instead"


def test_run_for_org_counts(program):
    db, org, admin = program["db"], program["org"], program["admin"]
    _lead_for(db, org, admin, "L007", phone="2145550107", first="Rosa", email="rd@example.com")
    out = line_check.run_for_org(db, org.id, limit=50, adapter=FakeAdapter("mobile"))
    assert out["checked"] >= 1 and out["results"].get("mobile", 0) >= 1
    again = line_check.run_for_org(db, org.id, limit=50, adapter=FakeAdapter("mobile"))
    assert again["checked"] == 0
