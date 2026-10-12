"""A location program's cadence shows in the Cadence Builder list."""
from app.models.models import CadenceTemplate
from tests.test_outreach_program import _h, program  # noqa: F401


def test_program_cadence_is_listed(client, program):
    db, org = program["db"], program["org"]
    db.add(CadenceTemplate(organization_id=org.id, name="SCI 10-Day Aggressive", industry="program",
                           is_default=False, is_active=True))
    db.commit()
    r = client.get("/cadence-templates/", headers=_h(db, program["admin"]))
    assert r.status_code == 200, r.text
    assert "SCI 10-Day Aggressive" in [t["name"] for t in r.json()]
