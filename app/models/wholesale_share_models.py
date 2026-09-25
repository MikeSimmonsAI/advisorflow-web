# -*- coding: utf-8 -*-
"""External access to one deal, for one audience, on one revocable link.

WHY THIS IS A SEPARATE TABLE AND NOT A FLAG ON THE DEAL.
An investor and a seller both need to see *something* about a deal without
having a login in this workspace, and they must not see the same something. A
boolean on the deal could only say "shared"; it could not say WITH WHOM, AS
WHICH AUDIENCE, UNTIL WHEN, or BE TAKEN BACK. Each of those is a row here.

WHY IT LOOKS LIKE `ProposalToken`.
Because it is the same problem the platform already solved once, and the
instruction is to reuse rather than invent authentication. The shape is
deliberately the same: an opaque token in the URL, an optional expiry, a
first-redemption stamp, a revocation stamp, and a view table beside it. What is
different is the `audience` column, because this link grants sight of a
DIFFERENT PAYLOAD depending on who it was minted for, and the payload is built
by a whitelist serializer rather than by hiding fields in the browser.

WHAT THE TOKEN IS NOT.
It is not an identity and it is not a session. It authorises exactly one deal,
in exactly one direction, and the endpoints that accept it never widen from it:
there is no "list my deals", no organization switch, and no write that is not
one of the actions this audience is allowed to take.
"""
from datetime import datetime

from sqlalchemy import (Boolean, Column, DateTime, ForeignKey, Index, Integer,
                        Numeric, String, Text)

from app.models.models import Base, gen_uuid

# Who a link was minted for. The payload builder switches on this, and a token
# minted for one audience can never render the other's page.
AUDIENCE_BUYER = "buyer"
AUDIENCE_SELLER = "seller"
AUDIENCES = (AUDIENCE_BUYER, AUDIENCE_SELLER)

# What an investor did from their own room. Every one of these maps onto a
# record the operator already reads; none of them is a new kind of truth.
BUYER_ACTION_INTERESTED = "interested"
BUYER_ACTION_OFFER = "offer"
BUYER_ACTION_WALKTHROUGH = "walkthrough"
BUYER_ACTION_QUESTION = "question"
BUYER_ACTION_PASS = "pass"
BUYER_ACTIONS = (BUYER_ACTION_INTERESTED, BUYER_ACTION_OFFER,
                 BUYER_ACTION_WALKTHROUGH, BUYER_ACTION_QUESTION,
                 BUYER_ACTION_PASS)


class WholesaleShareLink(Base):
    """One revocable link to one deal, for one audience."""

    __tablename__ = "wholesale_share_links"

    id = Column(String, primary_key=True, default=gen_uuid)
    organization_id = Column(String, ForeignKey("organizations.id"), nullable=False)
    deal_id = Column(String, ForeignKey("wholesale_deals.id", ondelete="CASCADE"),
                     nullable=False)

    # buyer | seller. Not a permission level — a different page entirely.
    audience = Column(String, nullable=False, default=AUDIENCE_BUYER)

    # For a buyer link: which buyer, and which outreach row their actions land
    # on. Both nullable so a seller link does not carry meaningless columns.
    buyer_id = Column(String, ForeignKey("wholesale_buyers.id", ondelete="CASCADE"),
                      nullable=True)
    outreach_id = Column(String,
                         ForeignKey("wholesale_buyer_outreach.id", ondelete="SET NULL"),
                         nullable=True)

    # The URL segment. Long, random, unique, and never a database id: a
    # sequential id in a URL is a guess away from somebody else's deal.
    token = Column(String, unique=True, nullable=False, index=True)

    # Display only. Nothing authenticates against these.
    recipient_name = Column(String, nullable=True)
    recipient_email = Column(String, nullable=True)

    # NULL means it does not expire on its own. It can still be revoked.
    expires_at = Column(DateTime, nullable=True)
    first_viewed_at = Column(DateTime, nullable=True)
    last_viewed_at = Column(DateTime, nullable=True)
    view_count = Column(Integer, nullable=False, default=0)

    # Set to a datetime to hard-block the link from the next request onward.
    revoked_at = Column(DateTime, nullable=True)
    revoked_by_id = Column(String, ForeignKey("users.id"), nullable=True)

    created_by_id = Column(String, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        Index("ix_wsshare_org_deal", "organization_id", "deal_id"),
        Index("ix_wsshare_audience", "organization_id", "audience"),
    )


class WholesaleShareView(Base):
    """Every time a link was opened, and what was done from it.

    The operator asked for one thing a magic link normally cannot answer: did
    they even look. This is that answer, and it is also the audit trail for any
    action taken from outside the workspace.
    """

    __tablename__ = "wholesale_share_views"

    id = Column(String, primary_key=True, default=gen_uuid)
    organization_id = Column(String, ForeignKey("organizations.id"), nullable=False)
    share_link_id = Column(String,
                           ForeignKey("wholesale_share_links.id", ondelete="CASCADE"),
                           nullable=False)
    deal_id = Column(String, ForeignKey("wholesale_deals.id", ondelete="CASCADE"),
                     nullable=False)

    # "view" for an open, otherwise the action name.
    action = Column(String, nullable=False, default="view")
    detail = Column(Text, nullable=True)
    amount = Column(Numeric(14, 2), nullable=True)

    # Kept short and coarse on purpose: enough to tell two devices apart in a
    # dispute, not a tracking profile.
    ip_prefix = Column(String, nullable=True)
    user_agent = Column(String, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        Index("ix_wsshareview_link", "share_link_id", "created_at"),
        Index("ix_wsshareview_deal", "organization_id", "deal_id"),
    )
