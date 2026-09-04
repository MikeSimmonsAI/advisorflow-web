"""
Tests for app/services/email_tracking_service.py - real open/click
engagement tracking for email, per Mike's explicit request for a
signal text never gives him.
"""

from app.services.email_tracking_service import inject_tracking

# THE ORGANIZATION'S OWN PUBLIC HOST, which is what production passes.
#
# inject_tracking takes public_base_url from app.services.public_identity and
# returns the body UNTOUCHED when there is no host - deliberately, because a
# rewritten link with no host resolves nowhere and a broken link in a family's
# email costs more than the tracking is worth. These tests used to call it with
# two arguments and assert against the module-level TRACKING_BASE_URL, which is
# "" whenever the env var is unset - so they were asserting that rewriting
# happened on a code path that correctly does not rewrite.
BASE = "https://mail.restland.example"


def test_inject_tracking_appends_a_pixel():
    result = inject_tracking("<p>Hello there</p>", "msg-123", BASE)

    assert f"{BASE}/email-tracking/open/msg-123" in result
    assert "<img" in result
    assert 'width="1"' in result
    assert 'height="1"' in result


def test_inject_tracking_rewrites_a_link_to_point_at_click_endpoint():
    original = '<a href="https://example.com/book">Book now</a>'
    result = inject_tracking(original, "msg-456", BASE)

    assert f"{BASE}/email-tracking/click/msg-456?url=https://example.com/book" in result


def test_inject_tracking_preserves_original_url_as_query_param():
    original = '<a href="https://example.com/book?token=abc">Book</a>'
    result = inject_tracking(original, "msg-789", BASE)

    assert "url=https://example.com/book?token=abc" in result


def test_inject_tracking_rewrites_multiple_links_independently():
    original = '<a href="https://example.com/a">A</a> <a href="https://example.com/b">B</a>'
    result = inject_tracking(original, "msg-multi", BASE)

    assert "url=https://example.com/a" in result
    assert "url=https://example.com/b" in result


def test_inject_tracking_does_not_touch_non_link_content():
    original = "<p>Just plain text, no links here at all.</p>"
    result = inject_tracking(original, "msg-plain", BASE)

    assert "<p>Just plain text, no links here at all.</p>" in result


def test_inject_tracking_handles_single_quoted_href():
    original = "<a href='https://example.com/single'>Link</a>"
    result = inject_tracking(original, "msg-single-quote", BASE)

    assert "url=https://example.com/single" in result


def test_inject_tracking_does_not_rewrite_non_http_links():
    """mailto: and tel: links should pass through untouched - they're not trackable the same way."""
    original = '<a href="mailto:someone@example.com">Email us</a>'
    result = inject_tracking(original, "msg-mailto", BASE)

    assert "mailto:someone@example.com" in result
    assert "email-tracking/click" not in result


def test_inject_tracking_returns_the_body_untouched_without_a_host():
    """No host, no rewrite. Tracking is worth less than a working link, so the
    body must come back byte-for-byte rather than carrying a relative or
    infrastructure URL into a family's inbox."""
    original = '<a href="https://example.com/book">Book now</a>'

    assert inject_tracking(original, "msg-nohost", "") == original
    assert inject_tracking(original, "msg-nohost", None) == original
