"""Hardening of the public proposal-file route (GET /proposals/files/{id}).

The route is intentionally unauthenticated (the client portal has no login and
the id is an unguessable UUID). What these tests pin:
  * a file whose proposal was deleted is no longer served (revocation),
  * shared caches may not keep a copy, and the browser may not sniff the type,
  * a hostile filename cannot inject into the Content-Disposition header.
"""
from datetime import datetime

from app.models.models import Proposal, ProposalFile


def _file(db, owner, *, deleted=False, filename="deck.pdf"):
    p = Proposal(organization_id=owner.organization_id, created_by_id=owner.id,
                 title="Plan", status="draft",
                 deleted_at=datetime.utcnow() if deleted else None)
    db.add(p)
    db.commit()
    pf = ProposalFile(organization_id=owner.organization_id, proposal_id=p.id,
                      filename=filename, content_type="application/pdf",
                      file_size=4, file_data=b"%PDF")
    db.add(pf)
    db.commit()
    return pf


def test_live_proposal_file_is_served_with_safe_headers(client, db_session, sample_advisor):
    pf = _file(db_session, sample_advisor)
    r = client.get("/proposals/files/%s" % pf.id)
    assert r.status_code == 200
    assert r.content == b"%PDF"
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["cache-control"].startswith("private")


def test_file_of_a_deleted_proposal_is_not_served(client, db_session, sample_advisor):
    pf = _file(db_session, sample_advisor, deleted=True)
    assert client.get("/proposals/files/%s" % pf.id).status_code == 404


def test_unknown_file_is_404(client):
    assert client.get("/proposals/files/does-not-exist").status_code == 404


def test_hostile_filename_cannot_break_the_header(client, db_session, sample_advisor):
    pf = _file(db_session, sample_advisor, filename='a"; x=1\r\nSet-Cookie: s=1.pdf')
    r = client.get("/proposals/files/%s" % pf.id)
    assert r.status_code == 200
    cd = r.headers["content-disposition"]
    assert cd.count('"') == 2
    assert "\r" not in cd and "\n" not in cd
    assert "set-cookie" not in {k.lower() for k in r.headers.keys()}
