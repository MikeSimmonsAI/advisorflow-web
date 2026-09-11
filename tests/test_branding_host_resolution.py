"""FOUND LIVE — /branding ANSWERED WITH THE BACKEND'S OWN BRAND.

THE OBSERVATION (production, 2026-09-11)

    GET https://advisorflow-backend.onrender.com/branding
    -> {"brand":"advisorflow","displayName":"AdvisorFlow",
        "supportEmail":"mike@simmonsstrong.com","accentColor":"#f59e0b", ...}

The frontend is a static site on the brand's own domain and calls the API on
`advisorflow-backend.onrender.com`, so the `Host` header this endpoint read
was always the BACKEND's. "advisorflow" is a substring of it, so it matched
the AdvisorFlow platform row — for every brand's customer. `theme.js` caches
the answer in localStorage and applies it synchronously on the next load, so
an EvoSys Pro customer's app chrome took AdvisorFlow's name, accent and
support address from their second page load onward.

AdvisorFlow is the engine underneath. The brand owns the face.

THE FIX: resolve from `Origin` (which the BROWSER sets, from the page's real
address, and page script cannot forge), then `Referer`, then `Host`. A
candidate that only matched the frozen fallback is not accepted while another
candidate might match a real platform row.
"""

import itertools

import pytest

from app.models.models import Platform
from app.services import brand_config

_SEQ = itertools.count(1)


@pytest.fixture()
def brands(db_session):
    rows = {}
    for name, slug, domain, accent, email in (
        ("EvoSys Pro", "evosyspro", "app.evosyspro.live", "#087cff",
         "support@evosyspro.live"),
        ("BookaBoost", "bookaboost", "app.bookaboost.live", "#c9973d",
         "support@bookaboost.live"),
        ("AdvisorFlow", "advisorflow", None, "#f59e0b",
         "mike@simmonsstrong.com"),
    ):
        row = Platform(name=name, slug=slug, domain=domain,
                       accent_color=accent, support_email=email,
                       is_active=True)
        db_session.add(row)
        rows[slug] = row
    db_session.commit()
    return rows


def test_the_browsers_origin_decides_not_the_api_hostname(client, brands):
    """THE EXACT PRODUCTION SHAPE: a request to the backend host, from a page
    on the brand's own domain."""
    resp = client.get("/branding",
                      headers={"origin": "https://app.evosyspro.live",
                               "host": "advisorflow-backend.onrender.com"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["brand"] == "evosyspro"
    assert body["displayName"] == "EvoSys Pro"
    assert body["supportEmail"] == "support@evosyspro.live"
    assert body["accentColor"] == "#087cff"


def test_the_other_brand_gets_its_own(client, brands):
    resp = client.get("/branding",
                      headers={"origin": "https://app.bookaboost.live",
                               "host": "advisorflow-backend.onrender.com"})
    body = resp.json()
    assert body["brand"] == "bookaboost"
    assert body["displayName"] == "BookaBoost"
    assert body["supportEmail"] == "support@bookaboost.live"


def test_advisorflow_is_not_served_to_a_branded_customer(client, brands):
    """THE REGRESSION THIS FILE EXISTS FOR."""
    for origin in ("https://app.evosyspro.live", "https://app.bookaboost.live"):
        body = client.get("/branding",
                          headers={"origin": origin,
                                   "host": "advisorflow-backend.onrender.com"}
                          ).json()
        assert body["brand"] != "advisorflow"
        assert body["displayName"] != "AdvisorFlow"
        assert body["supportEmail"] != "mike@simmonsstrong.com"


def test_referer_answers_when_there_is_no_origin(client, brands):
    """A plain navigation rather than a fetch."""
    body = client.get("/branding",
                      headers={"referer": "https://app.evosyspro.live/billing",
                               "host": "advisorflow-backend.onrender.com"}
                      ).json()
    assert body["brand"] == "evosyspro"


def test_host_still_answers_for_a_same_origin_deployment(client, brands):
    """UNCHANGED BEHAVIOUR where the app and the API share a hostname."""
    body = client.get("/branding",
                      headers={"host": "app.bookaboost.live"}).json()
    assert body["brand"] == "bookaboost"


def test_an_unrecognised_host_gets_nobodys_brand(db_session, brands):
    """`config_for_host` used to end in `config_for_slug(db, "bookaboost")` —
    handing a real brand's name, colours, website and support address to any
    host it did not recognise."""
    cfg = brand_config.config_for_host(db_session, "totally-unknown.example")
    assert cfg.get("slug") is None
    assert cfg.get("support_email") is None
    assert cfg.get("website_url") is None
    assert cfg.get("display_name") != "BookaBoost"


def test_an_empty_host_gets_nobodys_brand(db_session, brands):
    cfg = brand_config.config_for_host(db_session, "")
    assert cfg.get("support_email") is None
    assert cfg.get("display_name") != "BookaBoost"
