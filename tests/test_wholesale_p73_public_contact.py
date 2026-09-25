"""Phase 7.3 — a wholesale public page never shows the PLATFORM's phone.

The Investor Deal Room and the Seller Portal used to fall back to the platform
brand's support line (for EvoSys Pro, 469-553-7417) when the operator had no
contact of their own. That is the software vendor's number, answered by a
different business — an investor who calls it reaches the wrong company. No
wholesale-specific public phone is configured anywhere yet, so the honest
answer is none.
"""
from types import SimpleNamespace

from app.models.models import Platform
from app.services import wholesale_publication


def test_branding_never_falls_back_to_the_platform_phone(db_session, sample_org):
    platform = Platform(name="EvoSys Pro", slug="evosyspro")
    db_session.add(platform)
    db_session.flush()
    sample_org.platform_id = platform.id
    db_session.commit()

    brand = wholesale_publication.branding(
        db_session, SimpleNamespace(organization_id=sample_org.id))

    assert brand["support_phone"] is None
    assert "469-553-7417" not in repr(brand)
    # Everything else still resolves from the platform as before.
    assert set(brand) == {"name", "logo_url", "accent", "support_email",
                          "support_phone", "website"}
