"""The profile photo must not ride along on every authenticated request.

Audit #650 (2026-11-15): production stores profile photos as data URLs in
users.profile_photo_url - up to 2.48 MB in one row, 11 MB across 10 users.
get_current_user loads the User for every API call, so the photo was read from
the database on each request. The column is deferred: loaded only when read.
"""
from sqlalchemy import event, inspect

from app.models.models import Organization, User


def _make_user(db, photo):
    org = Organization(name="Photo Org", slug="photo-org")
    db.add(org)
    db.flush()
    u = User(organization_id=org.id, email="photo@example.test", password_hash="x",
             full_name="Photo User", profile_photo_url=photo)
    db.add(u)
    db.commit()
    return u.id


def test_profile_photo_is_deferred_on_the_model():
    assert inspect(User).attrs["profile_photo_url"].deferred is True


def test_loading_a_user_does_not_select_the_photo(db_session):
    db = db_session
    photo = "data:image/jpeg;base64," + "A" * 200_000
    uid = _make_user(db, photo)
    db.expunge_all()

    statements = []

    @event.listens_for(db.get_bind(), "before_cursor_execute")
    def _capture(conn, cursor, statement, *a):
        statements.append(statement)

    try:
        user = db.query(User).filter(User.id == uid).first()
        first_select = statements[-1]
        assert "profile_photo_url" not in first_select
        # Reading it still returns the stored value, in the same session.
        assert user.profile_photo_url == photo
        assert any("profile_photo_url" in s for s in statements[1:])
    finally:
        event.remove(db.get_bind(), "before_cursor_execute", _capture)
