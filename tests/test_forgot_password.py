"""Self-service password reset: one-time link to the account's own inbox,
same answer whether or not the account exists, nothing else changes."""
from unittest.mock import patch

from app.models.models import User
from app.services.auth_service import verify_password


def test_reset_link_goes_only_to_the_accounts_inbox_and_works_once(client, db_session, sample_advisor, monkeypatch):
    monkeypatch.setenv("FRONTEND_URL", "https://example.test")
    sent = []
    with patch("app.services.email_service.send_email_via_provider",
               side_effect=lambda to, subj, html, **kw: sent.append((to, html)) or {"success": True}):
        r = client.post("/auth/forgot-password", json={"email": "ADVISOR1@restland.com", "next": "/program/command"})
        r2 = client.post("/auth/forgot-password", json={"email": "nobody@example.com"})
    assert r.status_code == 200 and r.json() == r2.json()            # no account discovery
    assert len(sent) == 1 and sent[0][0] == sample_advisor.email
    assert "token" not in r.text                                      # never in the response
    token = sent[0][1].split("token=")[1].split("&")[0].split('"')[0]
    role, org = sample_advisor.role, sample_advisor.organization_id
    ok = client.post("/auth/staff-activation/accept", json={"token": token, "new_password": "BrandNewPass123!"})
    assert ok.status_code == 200
    again = client.post("/auth/staff-activation/accept", json={"token": token, "new_password": "Another123456!"})
    assert again.status_code >= 400                                   # one use only
    db_session.expire_all()
    u = db_session.query(User).filter_by(id=sample_advisor.id).one()
    assert verify_password("BrandNewPass123!", u.password_hash)
    assert (u.role, u.organization_id) == (role, org)                 # nothing else changed
    assert "next=/program/command" in sent[0][1] or "next=%2Fprogram%2Fcommand" in sent[0][1]


def test_four_characters_is_the_minimum(monkeypatch):
    from app.services import password_policy as pp
    monkeypatch.delenv("PASSWORD_MIN_LENGTH", raising=False)
    assert pp.min_length() == 4
    monkeypatch.setenv("PASSWORD_MIN_LENGTH", "12")
    assert pp.min_length() == 12
