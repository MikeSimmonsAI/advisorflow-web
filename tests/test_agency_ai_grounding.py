# -*- coding: utf-8 -*-
"""Grounded AI layer (S14): the model may only REPHRASE computed facts; the
verifier rejects anything new and the rules output stands. No real AI call:
every test injects a fake client through grounding._get_client."""
from types import SimpleNamespace

import pytest

from agency_support import agency_feature, h, lead, reply, world  # noqa: F401
from app.services import ai_gateway
from app.services.agency import grounding as G


class FakeClient:
    def __init__(self, text=None, exc=None):
        self.text, self.exc, self.calls = text, exc, []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kw):
        self.calls.append(kw)
        if self.exc:
            raise self.exc
        text = self.text(kw) if callable(self.text) else self.text
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=text))], usage=None)


@pytest.fixture()
def fake(monkeypatch):
    monkeypatch.setenv("AI_MANUAL_ACTIONS_ENABLED", "true")
    holder = {}

    def install(client):
        holder["c"] = client
        monkeypatch.setattr(G, "_get_client", lambda: client)
        return client
    return install


# ── verifier unit tests ─────────────────────────────────────────────────────

SRC = ["Name: Rita Retire", "State: TX", "Need categories: Retirement Planning",
       "Recommended next action: Make first contact"]
DRAFT = "Rita Retire in TX (source: website). Recorded needs: Retirement Planning. Recommended next action: Make first contact."


def test_verifier_accepts_faithful_rephrase():
    ok, v = G.verify("Rita Retire lives in TX and is interested in Retirement Planning. "
                     "Next step: make first contact.", SRC + [DRAFT], claim_sources=[DRAFT])
    assert ok, v


@pytest.mark.parametrize("bad,why", [
    ("Rita Retire in TX should get $500,000 of coverage.", "number"),
    ("Rita Retire in TX has 3 children.", "number"),
    ("Rita Retire in TX has two children.", "number"),
    ("Rita Retire and her husband Carlos live in TX.", "name"),
    ("Rita Retire in TX would be a good fit for an IUL.", "product"),
    ("Rita Retire in TX likely qualifies for preferred rates.", "claim"),
    ("Rita Retire in TX is eligible and will be approved.", "claim"),
    ("Rita Retire in TX has a health condition to discuss.", "claim"),
    ("Rita Retire in TX: whole life is the best option for you.", "product"),
    ("Contact Rita at rita@example.com in TX.", "email"),
    ("Call Rita Retire in TX at 214-555-0199.", "number"),
])
def test_verifier_rejects_hallucinations(bad, why):
    ok, v = G.verify(bad, SRC + [DRAFT], claim_sources=[DRAFT])
    assert not ok and v, (bad, why)


def test_prospect_quote_does_not_license_medical_claims():
    facts = ["Detected concern: Health-related question (quote: Will my diabetes medication be a problem?)"]
    draft = "Thanks for sharing that. A licensed agent can walk you through how the carrier process works."
    ok, v = G.verify("Your medication should not be a problem.", facts + [draft], claim_sources=[draft])
    assert not ok and any("medicat" in x for x in v)


# ── endpoint paths ──────────────────────────────────────────────────────────

def _brief(client, db, u, lid, assist=True):
    url = "/agency/prospects/%s/brief%s" % (lid, "?assist=ai" if assist else "")
    r = client.get(url, headers=h(db, u))
    assert r.status_code == 200, r.text
    return r.json()


def test_brief_default_is_rules_and_never_calls_ai(client, world, fake):
    c = fake(FakeClient("should never be used"))
    db = world["db"]
    l = lead(db, world["org"], "Rita", "Retire", state="TX", needs=["retirement"], intent="high")
    b = _brief(client, db, world["mgr"], l.id, assist=False)
    assert b["generated_by"] == "rules" and b["ai"]["status"] == "not_requested"
    assert b["narrative"] == b["rules_narrative"] and "Rita Retire in TX" in b["narrative"]
    assert c.calls == []


def test_brief_verified_rephrase_is_used(client, world, fake):
    db = world["db"]
    l = lead(db, world["org"], "Rita", "Retire", state="TX", needs=["retirement"], intent="high")
    c = fake(FakeClient("Rita Retire, based in TX, has a recorded interest in Retirement Planning. "
                        "Recommended next action: assign an agent."))
    b = _brief(client, db, world["mgr"], l.id)
    assert len(c.calls) == 1
    assert c.calls[0]["model"] == ai_gateway.resolve_model(G.CAP_BRIEF)
    assert b["generated_by"] == "ai" and b["ai"]["status"] == "verified"
    assert b["narrative"].startswith("Rita Retire, based in TX")
    assert b["rules_narrative"] != b["narrative"]
    # facts / inferences are untouched by AI
    assert all(f["source_field"] for f in b["facts"])


def test_brief_hallucinating_model_is_rejected(client, world, fake):
    db = world["db"]
    l = lead(db, world["org"], "Rita", "Retire", state="TX", needs=["retirement"], intent="high")
    fake(FakeClient("Rita Retire in TX qualifies for a $250,000 whole life policy from Acme Mutual "
                    "and her two kids are covered."))
    b = _brief(client, db, world["mgr"], l.id)
    assert b["generated_by"] == "rules" and b["ai"]["status"] == "rejected"
    assert b["narrative"] == b["rules_narrative"]
    text = " ".join(b["ai"]["violations"])
    assert "250000" in text and "Acme" in text and "qualif" in text


def test_fallbacks_when_ai_unavailable_or_failing(client, world, fake, monkeypatch):
    db = world["db"]
    l = lead(db, world["org"], "Rita", "Retire", state="TX")
    # provider error
    fake(FakeClient(exc=RuntimeError("boom")))
    b = _brief(client, db, world["mgr"], l.id)
    assert b["generated_by"] == "rules" and b["ai"]["status"] == "error"
    # manual AI switched off: gateway refuses before the client is reached
    c = fake(FakeClient("Rita Retire in TX."))
    monkeypatch.setenv("AI_MANUAL_ACTIONS_ENABLED", "false")
    b = _brief(client, db, world["mgr"], l.id)
    assert b["generated_by"] == "rules" and b["ai"]["status"] == "unavailable" and c.calls == []
    # no client and no key -> unavailable, nothing attempted
    monkeypatch.setenv("AI_MANUAL_ACTIONS_ENABLED", "true")
    monkeypatch.setattr(G, "_get_client", lambda: None)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    b = _brief(client, db, world["mgr"], l.id)
    assert b["generated_by"] == "rules" and b["ai"]["status"] == "unavailable"
    # empty model output
    fake(FakeClient(""))
    assert _brief(client, db, world["mgr"], l.id)["ai"]["status"] == "rejected"


def test_rephrase_requires_actor():
    r = G.rephrase(kind="brief", draft="x", facts=[], actor_id=None, org_id=None)
    assert r["generated_by"] == "rules" and r["ai"]["status"] == "unavailable"


def test_copilot_rephrase_verified_and_hallucination_rejected(client, world, fake):
    db = world["db"]
    l = lead(db, world["org"], "Obi", "Jones", owner=world["maya"])
    reply(db, l, "I already have life insurance through work. Why would I need anything else?")
    url = "/agency/prospects/%s/copilot?assist=ai" % l.id
    rules = client.get("/agency/prospects/%s/copilot" % l.id, headers=h(db, world["maya"])).json()
    assert rules["generated_by"] == "rules" and rules["ai"]["status"] == "not_requested"

    fake(FakeClient("Having coverage through work is a great start. It can help to understand how much "
                    "it pays and whether it stays with you if you change jobs. Would you be open to a few "
                    "quick questions so we can look at it together?"))
    c = client.get(url, headers=h(db, world["maya"])).json()
    assert c["generated_by"] == "ai" and c["ai"]["status"] == "verified", c["ai"]
    assert c["rules_suggested_reply"] == rules["suggested_reply"]
    assert c["detected"] == rules["detected"]

    fake(FakeClient("Obi, your employer plan only pays 1x salary; you need a 20-year term life policy "
                    "and you'll easily qualify."))
    c = client.get(url, headers=h(db, world["maya"])).json()
    assert c["generated_by"] == "rules" and c["ai"]["status"] == "rejected"
    assert c["suggested_reply"] == rules["suggested_reply"]


def test_assist_param_cannot_reach_foreign_record(client, world, fake):
    c = fake(FakeClient("x"))
    db = world["db"]
    l = lead(db, world["org"], "Rita", "Retire", state="TX")
    r = client.get("/agency/prospects/%s/brief?assist=ai" % l.id, headers=h(db, world["fmgr"]))
    assert r.status_code == 404 and c.calls == []
