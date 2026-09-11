"""THE KNOWLEDGE BASE — approved content, and a learning loop that asks first.

WHAT ASK [BRAND] IS ALLOWED TO ANSWER FROM
-------------------------------------------
Published articles. Not the model's memory of the internet, and not other
customers' conversations. `search()` is what grounds an answer, and an answer
that cites nothing is an answer the customer can see was not grounded.

THE LEARNING LOOP DOES NOT TRAIN ON CUSTOMERS
----------------------------------------------
No conversation text is fed into a model. No article publishes itself. No new
remediation registers itself. What the platform does instead is NOTICE — this
signature has come up eleven times and the same explanation resolved it — and
raise a CANDIDATE for a person to accept or reject. That is the entire loop,
and its deliberate slowness is the feature: an automatically published wrong
answer is served to every customer who asks, forever, in the brand's own
voice.

WHY THE SEARCH IS KEYWORD SCORING AND NOT AN EMBEDDING INDEX
-------------------------------------------------------------
An embedding index needs a vector store, a backfill, a re-embed on every edit
and a second failure mode nobody watches. This platform has none of those and
a help centre measured in dozens of articles, where a weighted keyword match
over title, keywords and summary is genuinely good enough. When the corpus is
large enough for that to stop being true, the seam is this one function.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.models.support_models import (
    SupportKnowledgeArticle, SupportKnowledgeCandidate,
)

log = logging.getLogger(__name__)

# Words that match everything and therefore rank nothing.
_STOPWORDS = frozenset("""
a an and are as at be but by can cant do does doesnt for from had has have how
i im in is it its me my not of on or our so than that the their them then there
these they this to too until up was we were what when where which who why will
with wont would you your
""".split())

# Field weights. Title beats keywords beats summary beats body: an article
# ABOUT a thing should outrank one that merely mentions it, and the body
# mentions everything.
_WEIGHTS = (("title", 6), ("keywords", 4), ("summary", 2), ("body", 1))

# A brand's own article outranks the platform-wide one on the same subject.
# Not because it is better, but because it is theirs — it may name their own
# settings screen, their own support hours, their own product decisions.
_BRAND_BONUS = 5


def _terms(text: Optional[str]) -> List[str]:
    if not text:
        return []
    words = re.findall(r"[a-z0-9]+", text.lower())
    return [w for w in words if len(w) > 2 and w not in _STOPWORDS]


def search(db: Session, *, platform_id: Optional[str], query: str,
           limit: int = 5) -> List[SupportKnowledgeArticle]:
    """Published articles for this brand plus the platform-wide ones, ranked.

    A brand sees its own articles AND platform-wide ones (platform_id NULL),
    never another brand's. That filter is in the query rather than in the
    ranking, so a scoring change can never turn into a disclosure.
    """
    terms = _terms(query)
    q = (db.query(SupportKnowledgeArticle)
         .filter(SupportKnowledgeArticle.is_published.is_(True)))
    if platform_id:
        q = q.filter(or_(SupportKnowledgeArticle.platform_id == platform_id,
                         SupportKnowledgeArticle.platform_id.is_(None)))
    else:
        q = q.filter(SupportKnowledgeArticle.platform_id.is_(None))

    articles = q.limit(500).all()
    if not terms:
        # No usable query: the most-read articles are a better answer than an
        # arbitrary alphabetical slice.
        return sorted(articles, key=lambda a: -(a.view_count or 0))[:limit]

    scored = []
    for article in articles:
        score = 0
        for field, weight in _WEIGHTS:
            haystack = (getattr(article, field, None) or "").lower()
            if not haystack:
                continue
            for term in terms:
                if term in haystack:
                    score += weight
        if platform_id and article.platform_id == platform_id and score:
            score += _BRAND_BONUS
        if score:
            scored.append((score, article))

    scored.sort(key=lambda pair: (-pair[0], pair[1].title))
    return [article for _, article in scored[:limit]]


def article_view(article: SupportKnowledgeArticle, *, full: bool = True) -> Dict[str, Any]:
    out = {
        "id": article.id,
        "slug": article.slug,
        "title": article.title,
        "summary": article.summary,
        "category": article.category,
        "updated_at": article.updated_at,
        "helpful_count": article.helpful_count,
        "unhelpful_count": article.unhelpful_count,
    }
    if full:
        out["body"] = article.body
    return out


def list_articles(db: Session, *, platform_id: Optional[str],
                  include_unpublished: bool = False,
                  category: Optional[str] = None) -> List[SupportKnowledgeArticle]:
    q = db.query(SupportKnowledgeArticle)
    if not include_unpublished:
        q = q.filter(SupportKnowledgeArticle.is_published.is_(True))
    if platform_id:
        q = q.filter(or_(SupportKnowledgeArticle.platform_id == platform_id,
                         SupportKnowledgeArticle.platform_id.is_(None)))
    if category:
        q = q.filter(SupportKnowledgeArticle.category == category)
    return q.order_by(SupportKnowledgeArticle.title.asc()).all()


def get_by_slug(db: Session, *, platform_id: Optional[str],
                slug: str) -> Optional[SupportKnowledgeArticle]:
    """One article, resolved with the SAME brand filter the search uses.

    A slug arrives in a URL, which means it arrives from the customer. Looking
    it up without the brand filter would make every other brand's help content
    readable by guessing a slug — the cheapest possible cross-tenant read.
    """
    q = (db.query(SupportKnowledgeArticle)
         .filter(SupportKnowledgeArticle.slug == slug,
                 SupportKnowledgeArticle.is_published.is_(True)))
    if platform_id:
        q = q.filter(or_(SupportKnowledgeArticle.platform_id == platform_id,
                         SupportKnowledgeArticle.platform_id.is_(None)))
    else:
        q = q.filter(SupportKnowledgeArticle.platform_id.is_(None))
    # A brand's own article wins over the platform-wide one with the same slug.
    rows = q.limit(5).all()
    for row in rows:
        if platform_id and row.platform_id == platform_id:
            return row
    return rows[0] if rows else None


def record_view(db: Session, article: SupportKnowledgeArticle) -> None:
    article.view_count = int(article.view_count or 0) + 1
    db.flush()


def record_feedback(db: Session, article: SupportKnowledgeArticle,
                    helpful: bool) -> None:
    """Was this any use? The one signal the learning loop trusts from a customer.

    A vote, not a rewrite. It cannot change an article's content and it cannot
    unpublish one; it raises or lowers a number that a person reads when
    deciding what to fix.
    """
    if helpful:
        article.helpful_count = int(article.helpful_count or 0) + 1
    else:
        article.unhelpful_count = int(article.unhelpful_count or 0) + 1
    db.flush()


def upsert_article(db: Session, *, platform_id: Optional[str], slug: str,
                   values: Dict[str, Any], actor_id: Optional[str] = None
                   ) -> SupportKnowledgeArticle:
    """Write an article. PUBLICATION IS A SEPARATE, EXPLICIT FIELD.

    A new article is created unpublished unless the caller says otherwise, so
    the act of writing and the act of putting it in front of customers are two
    decisions rather than one.
    """
    slug = (slug or "").strip().lower()[:120]
    row = (db.query(SupportKnowledgeArticle)
           .filter(SupportKnowledgeArticle.slug == slug,
                   SupportKnowledgeArticle.platform_id == platform_id).first())
    if row is None:
        row = SupportKnowledgeArticle(
            platform_id=platform_id, slug=slug,
            title=values.get("title") or slug,
            body=values.get("body") or "", created_by=actor_id,
            is_published=bool(values.get("is_published", False)))
        db.add(row)

    for field in ("title", "summary", "body", "category", "keywords",
                  "related_services", "is_published"):
        if field in values:
            setattr(row, field, values[field])
    db.flush()
    return row


# ══════════════════════════════════════════════════════════════════════════
# THE LEARNING LOOP — proposals, never publications
# ══════════════════════════════════════════════════════════════════════════

def propose(db: Session, *, kind: str, title: str,
            platform_id: Optional[str] = None, signature: Optional[str] = None,
            rationale: Optional[str] = None,
            evidence: Optional[Dict[str, Any]] = None,
            occurrence_count: int = 1,
            organizations_affected: int = 1) -> SupportKnowledgeCandidate:
    """Raise (or reinforce) a proposal for a person to decide on.

    A repeat proposal for the same signature and kind UPDATES the existing one
    rather than creating a second: the useful fact is "this has now happened
    forty times", not forty identical cards on a screen.
    """
    row = None
    if signature:
        row = (db.query(SupportKnowledgeCandidate)
               .filter(SupportKnowledgeCandidate.signature == signature,
                       SupportKnowledgeCandidate.kind == kind,
                       SupportKnowledgeCandidate.status == "proposed").first())
    if row is None:
        row = SupportKnowledgeCandidate(
            platform_id=platform_id, kind=kind, signature=signature,
            title=title, rationale=rationale,
            occurrence_count=occurrence_count,
            organizations_affected=organizations_affected)
        db.add(row)
    else:
        row.occurrence_count = max(int(row.occurrence_count or 0), occurrence_count)
        row.organizations_affected = max(int(row.organizations_affected or 0),
                                         organizations_affected)
        row.rationale = rationale or row.rationale
        row.title = title or row.title

    if evidence is not None:
        row.evidence_json = json.dumps(evidence, default=str)
    db.flush()
    return row


def decide(db: Session, candidate: SupportKnowledgeCandidate, *, accept: bool,
           actor_id: Optional[str], note: Optional[str] = None
           ) -> SupportKnowledgeCandidate:
    """A person accepts or rejects. ACCEPTING DOES NOT PUBLISH ANYTHING.

    An accepted knowledge candidate means "yes, write this article" — it
    records the decision and hands the author a starting point. An accepted
    auto-fix candidate means "yes, this is worth a registered remediation",
    which is an engineering change: a remediation is Python written by a
    person, and nothing here can conjure one.
    """
    candidate.status = "accepted" if accept else "rejected"
    candidate.decided_by = actor_id
    candidate.decided_at = datetime.utcnow()
    candidate.decision_note = note
    db.flush()
    return candidate


def scan_for_candidates(db: Session, *, days: int = 7) -> Dict[str, Any]:
    """Turn recurring evidence into proposals. Called by the daily brief.

    THREE THINGS ARE WORTH A PERSON'S ATTENTION:

      * a signature that keeps appearing with NO registered repair — either
        the help centre should answer it or the product should stop causing it
      * a signature whose registered repair KEEPS WORKING and whose condition
        KEEPS RETURNING — the repair treats a symptom, and a standing symptom
        treatment is a decision somebody should make on purpose
      * a signature whose repair KEEPS FAILING — the repair is wrong, and a
        failing automatic repair is worse than none because it looks handled
    """
    from app.models.support_models import SupportIssueSignature
    from datetime import timedelta

    since = datetime.utcnow() - timedelta(days=days)
    rows = (db.query(SupportIssueSignature)
            .filter(SupportIssueSignature.last_seen_at >= since,
                    SupportIssueSignature.occurrence_count >= 3).all())

    raised = []
    for row in rows:
        evidence = {
            "signature": row.signature,
            "occurrences": row.occurrence_count,
            "organizations": row.organizations_affected,
            "auto_fixed": row.auto_fixed_count,
            "auto_fix_failed": row.auto_fix_failed_count,
        }
        if not row.known_remediation_key:
            candidate = propose(
                db, kind="knowledge_article", signature=row.signature,
                title="Help centre answer for: %s" % row.title,
                rationale=("Seen %d time(s) across %d organization(s) with no "
                           "registered repair. Either the help centre should "
                           "answer it or the product should stop causing it."
                           % (row.occurrence_count, row.organizations_affected)),
                evidence=evidence, occurrence_count=row.occurrence_count,
                organizations_affected=row.organizations_affected)
            raised.append(candidate.id)
        elif row.symptom_only_remediation:
            candidate = propose(
                db, kind="engineering_defect", signature=row.signature,
                title="Permanent fix for: %s" % row.title,
                rationale=("The registered repair resolves this %d time(s) and "
                           "the condition keeps returning. That is a symptom "
                           "treatment, and it is worth deciding to keep it on "
                           "purpose rather than by default."
                           % row.auto_fixed_count),
                evidence=evidence, occurrence_count=row.occurrence_count,
                organizations_affected=row.organizations_affected)
            raised.append(candidate.id)
        elif row.auto_fix_failed_count >= 3:
            candidate = propose(
                db, kind="auto_fix_registration", signature=row.signature,
                title="Repair is failing for: %s" % row.title,
                rationale=("The registered repair %s failed %d time(s). A "
                           "failing automatic repair is worse than none, "
                           "because the problem looks handled."
                           % (row.known_remediation_key, row.auto_fix_failed_count)),
                evidence=evidence, occurrence_count=row.occurrence_count,
                organizations_affected=row.organizations_affected)
            raised.append(candidate.id)

    db.flush()
    return {"days": days, "signatures_examined": len(rows),
            "candidates": raised}


def open_candidates(db: Session, *, platform_id: Optional[str] = None
                    ) -> List[SupportKnowledgeCandidate]:
    q = (db.query(SupportKnowledgeCandidate)
         .filter(SupportKnowledgeCandidate.status == "proposed"))
    if platform_id:
        q = q.filter(or_(SupportKnowledgeCandidate.platform_id == platform_id,
                         SupportKnowledgeCandidate.platform_id.is_(None)))
    return q.order_by(SupportKnowledgeCandidate.occurrence_count.desc()).all()


# ══════════════════════════════════════════════════════════════════════════
# STARTER CONTENT
# ══════════════════════════════════════════════════════════════════════════
#
# PLATFORM-WIDE (platform_id NULL) AND FACTUALLY TIED TO WHAT THE CODE DOES.
# Each of these answers a question one of the registered diagnostic checks can
# actually detect, in the words the check uses. Content invented to fill a
# help centre is content that contradicts the product within a month, so there
# is deliberately very little of it and no marketing copy at all.
#
# SEEDING IS AN EXPLICIT GOD ACTION, never a startup side effect: a help
# centre that populates itself on deploy would overwrite a brand's edits every
# time the service restarted.

STARTER_ARTICLES = [
    {
        "slug": "reconnect-your-calendar",
        "title": "Reconnecting your calendar",
        "category": "Calendar",
        "keywords": "calendar,google,microsoft,outlook,sync,reconnect,booking,"
                    "availability,appointments",
        "related_services": "calendar",
        "summary": "What it means when a calendar shows as needing reconnection, "
                   "and how to fix it.",
        "body": (
            "Calendar providers expire the permission you granted us, usually "
            "after a password change or a security policy update on your side. "
            "When that happens we stop retrying and mark the connection as "
            "needing reconnection, so you see an action rather than silence.\n\n"
            "To reconnect: open Settings, find the calendar under Integrations, "
            "and choose Reconnect. You'll be sent to your provider to approve "
            "access again. Bookings made while the connection was down are still "
            "in the system — they just weren't written to your calendar, and "
            "they will sync on the next availability check.\n\n"
            "If you connected Microsoft 365 for email only, the calendar "
            "permission may never have been granted. Reconnecting and approving "
            "the calendar scope fixes that too."),
    },
    {
        "slug": "text-messages-not-sending",
        "title": "Text messages aren't sending",
        "category": "Messaging",
        "keywords": "sms,text,message,twilio,send,failed,undelivered,number",
        "related_services": "sms",
        "summary": "The three things a working text message needs, and how to "
                   "tell which one is missing.",
        "body": (
            "Sending a text needs three things configured: an account, an "
            "authorization token, and a sending phone number. Your workspace's "
            "settings are used first, and an individual user's own settings only "
            "as a fallback — so a message can fail for one person and work for "
            "another.\n\n"
            "Ask your assistant to check messaging and it will tell you exactly "
            "which of the three is missing, without showing any credential.\n\n"
            "If everything is configured and messages are still failing, the "
            "carrier is refusing them. The most common reasons are an "
            "unregistered sending number and a recipient who has replied STOP. "
            "Both show up as delivery failures rather than as configuration "
            "problems."),
    },
    {
        "slug": "how-support-priorities-work",
        "title": "How we prioritise support requests",
        "category": "Support",
        "keywords": "severity,priority,sla,response,critical,urgent,queue,"
                    "escalate,emergency",
        "related_services": "support",
        "summary": "What Critical, High, Normal and Question mean, and how "
                   "response targets are measured.",
        "body": (
            "Requests are prioritised by impact, not by wording:\n\n"
            "Critical — a business-critical part of the service is unavailable "
            "and there is no reasonable workaround.\n"
            "High — a major capability is materially impaired.\n"
            "Normal — something is wrong, but the system is working or there is "
            "a way around it.\n"
            "Question or request — a how-to, a configuration request, training, "
            "or work you'd like us to do.\n\n"
            "Response targets are measured in BUSINESS hours, using the support "
            "hours shown on your Support Plan page. A request raised at 4:45pm "
            "on a Friday starts its clock when support opens again, so the "
            "target means the same thing whenever you raise it.\n\n"
            "A confirmed outage goes to the emergency queue regardless of which "
            "package you're on. Your plan affects normal priority; it does not "
            "put a working service ahead of a broken one.\n\n"
            "The targets we publish are FIRST RESPONSE targets — when you will "
            "hear from a person. We don't publish resolution times, because a "
            "resolution time we haven't committed to isn't a promise worth "
            "making."),
    },
    {
        "slug": "whats-included-in-support",
        "title": "What's included in your support",
        "category": "Support",
        "keywords": "included,billable,consulting,assistance,minutes,allowance,"
                    "services,charges,cost",
        "related_services": "support",
        "summary": "The difference between technical support, assistance, and "
                   "professional services.",
        "body": (
            "There are three different things, and only some of them are "
            "billable.\n\n"
            "TECHNICAL PRODUCT SUPPORT — the product isn't doing what it's "
            "supposed to do. This is included in every paying package, always. "
            "You are never charged because our software is broken, and time we "
            "spend on it never comes out of your included assistance minutes.\n\n"
            "CUSTOMER ASSISTANCE — help using or configuring a working product. "
            "Your package may include some scheduled live assistance each month; "
            "anything beyond that is billable.\n\n"
            "PROFESSIONAL SERVICES — our people doing work for your business: "
            "building campaigns, migrating data, custom integrations, training. "
            "This is billable work and is quoted before it starts.\n\n"
            "Your Support Plan page shows your included minutes, how many you've "
            "used this billing period, and how many remain. Unused minutes do "
            "not roll over."),
    },
    {
        "slug": "why-a-feature-is-switched-off",
        "title": "Why a feature is switched off",
        "category": "Account",
        "keywords": "feature,disabled,switched off,not available,payment,plan,"
                    "limit,upgrade,402",
        "related_services": "billing",
        "summary": "Why a screen or action might be unavailable, and what to do.",
        "body": (
            "A feature can be unavailable for three quite different reasons, and "
            "your assistant can tell you which one applies:\n\n"
            "It isn't part of your plan. Features are enabled per workspace; the "
            "ones you have are listed on your Support Plan page.\n\n"
            "You've reached a limit. Plans include a number of users and a "
            "number of leads. Existing records keep working when you reach a "
            "limit — it's the next addition that's stopped.\n\n"
            "There's a payment issue. If a payment has failed, access can be "
            "limited until it's resolved. Your billing page will say so.\n\n"
            "None of these is a fault, and all three look identical from the "
            "outside — which is why it's worth asking rather than assuming "
            "something is broken."),
    },
]


def seed_starter_articles(db: Session, *, actor_id: Optional[str] = None,
                          publish: bool = True) -> Dict[str, Any]:
    """Install the platform-wide starter articles. Idempotent by slug.

    Existing articles are UPDATED, not duplicated — but an article a brand has
    edited keeps its edits for every field the starter set does not carry, and
    a re-seed is a deliberate act somebody performs rather than something a
    deploy does to them.
    """
    created, updated = [], []
    for spec in STARTER_ARTICLES:
        existing = (db.query(SupportKnowledgeArticle)
                    .filter(SupportKnowledgeArticle.slug == spec["slug"],
                            SupportKnowledgeArticle.platform_id.is_(None)).first())
        values = dict(spec)
        values.pop("slug")
        values["is_published"] = publish
        upsert_article(db, platform_id=None, slug=spec["slug"], values=values,
                       actor_id=actor_id)
        (updated if existing is not None else created).append(spec["slug"])
    db.flush()
    return {"created": created, "updated": updated}
