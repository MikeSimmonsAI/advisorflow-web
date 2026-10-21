# -*- coding: utf-8 -*-
"""Owner decision 2026-10-21: EvoSys's own operator accounts do not count
against a customer's user limit. Customer staff and outside people still do."""
import json
import uuid

from app.models.models import Organization, Platform, User
from app.models.sales_models import Membership
from app.services import plan_limits as PL
from app.services.auth_service import hash_password


def _org(db, slug, name):
    o = Organization(name=name, slug=slug, plan="professional", industry="funeral", is_active=True,
                     enabled_features=json.dumps(["leads", "users"]))
    db.add(o)
    db.commit()
    return o


def _u(db, org, tag):
    u = User(organization_id=org.id if org else None, email="%s-%s@example.com" % (tag, uuid.uuid4().hex[:6]),
             full_name=tag, role="org_admin", password_hash=hash_password("Pass12345!"), is_active=True)
    db.add(u)
    db.commit()
    return u


def _m(db, u, st, sid, role="org_admin"):
    db.add(Membership(user_id=u.id, scope_type=st, scope_id=sid, role=role, is_active=True))
    db.commit()


def test_evosys_operators_are_not_customer_seats(db_session):
    db = db_session
    evo = _org(db, "evo-integrated-solutions-llc", "EVO Integrated Solutions LLC")
    cust = _org(db, "cust-%s" % uuid.uuid4().hex[:6], "Customer")
    other = _org(db, "other-%s" % uuid.uuid4().hex[:6], "Other Customer")
    plat = Platform(name="EvoSys Pro", slug="evosys-%s" % uuid.uuid4().hex[:4])
    db.add(plat)
    db.commit()
    staff = _u(db, cust, "staff")                         # customer's own: counts
    wholesale = _u(db, evo, "wholesale")                  # EvoSys internal org: free
    _m(db, wholesale, "customer_org", cust.id)
    michael = _u(db, None, "michael")                     # brand member, no home: free
    _m(db, michael, "platform", plat.id, "brand_executive")
    _m(db, michael, "customer_org", cust.id)
    outsider = _u(db, other, "outsider")                  # another customer's person: counts
    _m(db, outsider, "customer_org", cust.id)
    homeless = _u(db, None, "homeless")                   # no home, no brand seat: counts
    _m(db, homeless, "customer_org", cust.id)
    assert PL.usage_for(db, cust, PL.LIMIT_USERS) == 3    # staff, outsider, homeless
    # EvoSys's own org still counts the people homed there
    assert PL.usage_for(db, evo, PL.LIMIT_USERS) == 1
