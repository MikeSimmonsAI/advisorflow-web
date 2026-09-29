"""User-visible strings must not carry double-encoded UTF-8 (mojibake).

Found in visual QA: the God Organization Control Center > Administration tab
rendered a capability description as "housekeeping Ã¢â‚¬â€ archive", and the
booking-settings validation errors read "5â€“480". These come straight from
backend strings, so the check lives here.
"""
from app.services import capabilities

_MARKERS = ("Ã", "â€")  # "Ã" and "â€" - the signature of UTF-8 read as cp1252


def _clean(text):
    return not any(m in (text or "") for m in _MARKERS)


def test_capability_labels_and_reasons_have_no_mojibake():
    for key, cap in capabilities.CAPABILITIES.items():
        for field in ("label", "why"):
            value = getattr(cap, field, None)
            if isinstance(value, str):
                assert _clean(value), f"{key}.{field}: {value!r}"


def test_booking_settings_range_errors_are_readable(client, auth_headers):
    r = client.patch("/settings/booking-settings", headers=auth_headers,
                     json={"appt_duration_minutes": 1})
    assert r.status_code == 400
    detail = r.json()["detail"]
    assert "5–480" in detail and _clean(detail)
