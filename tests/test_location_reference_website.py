"""A location with no website entered shows its official public page from
scripts/sci_campuses.csv, flagged as a reference; an entered website wins and
nothing is ever written to the profile by the fallback."""
from app.models.program_models import LocationProfile
from app.routers import program_router as pr
from app.services.programs import campuses


def _prof(name, website=None, bucket=False):
    return LocationProfile(official_name=name, website=website, is_review_bucket=bucket)


def test_reference_website_comes_from_the_campus_file():
    row = next(r for r in campuses.load_grouping() if r["Location"] == "Pine Crest Cemetery West")
    assert row["Website"].startswith("https://www.dignitymemorial.com/")
    assert pr._reference_website(_prof("Pine Crest Cemetery West")) == row["Website"]


def test_held_and_bucket_locations_get_no_reference():
    assert pr._reference_website(_prof("Oaklawn Central Care Center")) is None
    assert pr._reference_website(_prof("Unassigned / Location Review", bucket=True)) is None


def test_every_mapped_location_except_the_held_one_has_a_website():
    rows = campuses.load_grouping()
    missing = [r["Location"] for r in rows if not (r.get("Website") or "").strip()]
    assert missing == ["Oaklawn Central Care Center"]
