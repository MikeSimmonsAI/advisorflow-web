"""Dependency-free proof of the seller-reply reader (SYNTHETIC LOGIC).

Runs the REAL app/services/wholesale_ai.py rules and extract_from_message with
a stubbed AI gateway. No network, no DB, no sends.
Run: python3 scripts/wholesale_ai_comms_proof.py
"""
import json
import os
import sys
import types

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from app.services import wholesale_ai as W  # noqa: E402

passed = failed = 0


def check(name, cond):
    global passed, failed
    if cond:
        passed += 1
    else:
        failed += 1
        print("FAIL:", name)


def intent(t):
    return W._deterministic_read(t)["intent"]


# STOP / opt-out must be recognised
for t in ["STOP", "stop", "Stop texting me", "unsubscribe", "Please remove me",
          "do not contact me", "don't text me again", "END", "Quit.",
          "quit texting me", "cancel", "opt out", "STOPALL"]:
    check("optout: " + t, intent(t) == W.INTENT_DO_NOT_CONTACT)
# ordinary words must NOT write a DNC suppression
for t in ["We can end up closing in 30 days", "In the end I would sell for $150k",
          "I quit my job so I need to sell"]:
    check("no false optout: " + t, intent(t) != W.INTENT_DO_NOT_CONTACT)
# "who is this" routes to a person, not wrong-person (which kills the deal)
r = W._deterministic_read("Who is this?")
check("who is this -> needs_human", r["intent"] == W.INTENT_NEEDS_HUMAN and r["needs_human"])
for t in ["wrong number", "You have the wrong person"]:
    check("wrong person: " + t, intent(t) == W.INTENT_WRONG_PERSON)
check("already sold", intent("We sold it last year") == W.INTENT_ALREADY_SOLD)
check("not interested", intent("not interested") == W.INTENT_NOT_INTERESTED)
check("price", W._deterministic_read("Yes, $150k, asap")["asking_price"] == 150000.0)
check("timeline", W._deterministic_read("Yes, $150k, asap")["timeline"] == "asap")


# ---- stubbed gateway: response-shape handling ----
def run(content=None, exc=None, text="Maybe, tell me more"):
    gw = types.ModuleType("app.services.ai_gateway")

    def chat_completion(**kw):
        if exc:
            raise exc
        msg = types.SimpleNamespace(content=content)
        return types.SimpleNamespace(choices=[types.SimpleNamespace(message=msg)])

    gw.chat_completion = chat_completion
    import app.services as pkg
    old = sys.modules.get("app.services.ai_gateway")
    oldattr = getattr(pkg, "ai_gateway", None)
    sys.modules["app.services.ai_gateway"] = gw
    pkg.ai_gateway = gw
    try:
        return W.extract_from_message(text, org_id="orgA")
    finally:
        if old is not None:
            sys.modules["app.services.ai_gateway"] = old
        else:
            sys.modules.pop("app.services.ai_gateway", None)
        if oldattr is not None:
            pkg.ai_gateway = oldattr
        elif hasattr(pkg, "ai_gateway"):
            del pkg.ai_gateway


good = json.dumps({"intent": "interested", "asking_price": "$200,000",
                   "confidence": 90, "summary": "ok"})
r = run(good)
check("ai good", r["source"] == "ai" and r["asking_price"] == 200000.0 and not r["needs_human"])
r = run("```json\n" + good + "\n```")
check("ai fenced json", r["source"] == "ai")
r = run(None)
check("ai None content -> rules+human", r["source"] == "rules" and r["needs_human"])
r = run("not json")
check("ai garbage -> rules+human", r["source"] == "rules" and r["needs_human"])
r = run(json.dumps({"intent": "bogus", "timeline": "never", "confidence": 90}))
check("ai bad enums dropped", r["intent"] is None and r["timeline"] is None)
r = run(json.dumps({"intent": "interested", "confidence": "high"}))
check("ai bad confidence -> human", r["needs_human"])
r = run(json.dumps({"intent": "interested", "confidence": 30}))
check("ai low confidence -> human", r["needs_human"])
r = run(exc=RuntimeError("provider down"))
check("ai exception never raises",
      r["source"] == "rules" and "RuntimeError" in r["ai_unavailable_reason"])
r = run(good, text="STOP")
check("STOP bypasses AI", r["intent"] == W.INTENT_DO_NOT_CONTACT and r["source"] == "rules")
r = run(good, text="   ")
check("empty message -> human", r["needs_human"] and r["intent"] is None)

print("wholesale_ai_comms_proof: %d/%d passed (SYNTHETIC LOGIC)" % (passed, passed + failed))
sys.exit(1 if failed else 0)
