"""CUSTOMER KNOWLEDGE — tenant-isolated, source-attributed, and allowed to
say it does not know.

WHAT THIS IS NOT. It is not a vector store, and it is deliberately not the
platform's Support Intelligence knowledge base. `support_knowledge_articles` is
PLATFORM-scoped (`platform_id`, no organization column) and is about how the
PRODUCT works — the right source for an AI Support Specialist answering "why
won't my import run", and exactly the wrong source for "what does this funeral
home charge for a graveside service". Pointing an employee at it would answer a
customer's question with another company's documentation. Section 44 asks for
tenant isolation; using a platform-scoped table for tenant facts fails that on
the first question.

WHAT IT IS. A source-grounded read over the facts THIS organization has
actually configured about itself, every one of which already carries
`organization_id`:

    the organization profile   name, address, phone, hours, review links
    appointment types          what they will actually book
    tier definitions           how they describe each kind of enquiry, and the
                               tone context an operator wrote for it
    message templates          the approved wording for each track and channel

EVERY PASSAGE CARRIES ITS SOURCE. An answer that cannot be checked is an answer
nobody should repeat to a family, so `search` returns {source, title, text} and
the runtime is instructed to cite or to decline.

NOTHING A LEAD SAYS BECOMES KNOWLEDGE. Section 44: "Do not allow a random lead
message to become permanent authoritative customer knowledge." Lead-stated
facts live in `ai_employee_memory` with source=`stated_by_contact`, and
`memory.recall` labels them as reported rather than known. There is no write
path from a conversation into this module, by construction — this module has no
write path at all.
"""

import json
import logging
import re
from typing import Dict, List, Optional

from sqlalchemy.orm import Session

from app.models.models import MessageTemplate, Organization, TierDefinition

_log = logging.getLogger(__name__)

MAX_PASSAGE_CHARS = 600
DEFAULT_LIMIT = 5

_WORD_RE = re.compile(r"[a-z0-9']+")


def _tokens(text: str) -> List[str]:
    return [t for t in _WORD_RE.findall((text or "").lower()) if len(t) > 2]


def _score(query_tokens: List[str], text: str) -> int:
    """A transparent term-overlap score.

    NOT a model, and not a similarity nobody can check. An operator asking why
    a passage was returned gets an answer they can verify by reading it. When
    this is eventually replaced by embeddings, the SOURCE ATTRIBUTION contract
    below is what must survive, not the scoring.
    """
    if not query_tokens:
        return 0
    hay = (text or "").lower()
    return sum(1 for t in set(query_tokens) if t in hay)


def _org_profile_passages(org: Organization) -> List[Dict]:
    out = []
    name = org.brand_name or org.name
    bits = []
    if org.org_address:
        bits.append("Address: %s" % org.org_address)
    if org.org_phone:
        bits.append("Phone: %s" % org.org_phone)
    if org.support_email:
        bits.append("Support email: %s" % org.support_email)
    if org.industry:
        bits.append("Industry: %s" % org.industry)
    if bits:
        out.append({"source": "organization_profile",
                    "title": "%s — business details" % name,
                    "text": "\n".join(bits)})
    try:
        types = json.loads(org.appointment_types or "[]")
    except (ValueError, TypeError):
        types = []
    if isinstance(types, list) and types:
        labels = [str(t.get("label") if isinstance(t, dict) else t)
                  for t in types][:20]
        out.append({"source": "appointment_types",
                    "title": "%s — appointment types" % name,
                    "text": "Appointments this business offers: %s"
                            % ", ".join(labels)})
    return out


def _tier_passages(db: Session, organization_id: str) -> List[Dict]:
    rows = (db.query(TierDefinition)
            .filter(TierDefinition.organization_id == organization_id,
                    TierDefinition.is_active.is_(True))
            .order_by(TierDefinition.sort_order.asc()).all())
    out = []
    for r in rows:
        text = "%s (%s). Track: %s." % (r.tier_label, r.tier_key, r.track_label)
        if r.ai_tone_context:
            text += "\nHow to talk about it: %s" % r.ai_tone_context
        out.append({"source": "tier_definition:%s" % r.tier_key,
                    "title": r.tier_label, "text": text})
    return out


def _template_passages(db: Session, organization_id: str) -> List[Dict]:
    rows = (db.query(MessageTemplate)
            .filter(MessageTemplate.organization_id == organization_id)
            .limit(60).all())
    out = []
    for r in rows:
        out.append({"source": "message_template:%s:%s" % (r.message_track,
                                                          r.channel),
                    "title": "Approved %s wording — %s" % (r.channel,
                                                           r.message_track),
                    "text": (r.email_subject_template + "\n"
                             if r.email_subject_template else "")
                            + (r.body_template or "")})
    return out


def corpus(db: Session, organization_id: str,
           binding: Optional[Dict] = None) -> List[Dict]:
    """Everything this employee is bound to, and nothing else.

    `binding` is the employee's `knowledge_binding` — a list of source kinds
    the customer chose. Absent, every kind is included; present, it NARROWS.
    It cannot widen: there is no source outside this organization's own rows
    for it to name, because the queries above take organization_id and nothing
    else does.
    """
    org = (db.query(Organization)
           .filter(Organization.id == organization_id).first())
    if org is None:
        return []
    kinds = None
    if isinstance(binding, dict):
        raw = binding.get("kinds")
        if isinstance(raw, list) and raw:
            kinds = {str(k) for k in raw}

    out: List[Dict] = []
    if kinds is None or "organization_profile" in kinds:
        out.extend(_org_profile_passages(org))
    if kinds is None or "tier_definitions" in kinds:
        out.extend(_tier_passages(db, organization_id))
    if kinds is None or "message_templates" in kinds:
        out.extend(_template_passages(db, organization_id))
    for item in out:
        item["organization_id"] = organization_id
    return out


def search(db: Session, organization_id: str, query: str, *,
           limit: int = DEFAULT_LIMIT,
           binding: Optional[Dict] = None) -> Dict:
    """Source-grounded retrieval inside ONE organization.

    Returns `{"query", "results": [...], "found": bool}`. An empty result is a
    NORMAL, EXPECTED answer and the runtime is told to say "I don't know" on
    it — section 15: the AI should be able to say it does not know rather than
    fabricate customer policy.
    """
    q = _tokens(query)
    scored = []
    for passage in corpus(db, organization_id, binding):
        s = _score(q, passage["title"] + "\n" + passage["text"])
        if s > 0:
            scored.append((s, passage))
    scored.sort(key=lambda pair: -pair[0])
    results = []
    for s, passage in scored[:max(1, int(limit or DEFAULT_LIMIT))]:
        results.append({
            "source": passage["source"],
            "title": passage["title"],
            "text": (passage["text"] or "")[:MAX_PASSAGE_CHARS],
            "match_score": s,
        })
    return {"query": (query or "")[:200], "results": results,
            "found": bool(results),
            "note": ("Answer only from these passages and name the source. "
                     "If they do not answer the question, say you do not know "
                     "and offer to have a person follow up.")}


def describe_binding(db: Session, organization_id: str,
                     binding: Optional[Dict] = None) -> Dict:
    """What knowledge is this employee using? A question a customer may ask.

    Section 44: "Customer should be able to understand what knowledge an
    employee is using." This is what the employee detail screen renders.
    """
    items = corpus(db, organization_id, binding)
    kinds: Dict[str, int] = {}
    for item in items:
        kind = item["source"].split(":")[0]
        kinds[kind] = kinds.get(kind, 0) + 1
    return {
        "organization_id": organization_id,
        "passage_count": len(items),
        "sources": [{"kind": k, "count": v} for k, v in sorted(kinds.items())],
        "bound_kinds": (binding or {}).get("kinds") if isinstance(binding, dict)
        else None,
    }
