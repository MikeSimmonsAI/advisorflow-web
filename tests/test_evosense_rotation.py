"""Bulk-file discovery rotates: each day starts a different window, so a daily
hunt reaches new accounts instead of re-reading the same first page."""
from datetime import datetime

from app.services.evosense import providers as PV


def test_rotation_moves_each_day_and_stays_inside_the_pool():
    a = PV.rotation_skip(100, 9000, today=datetime(2026, 10, 11))
    b = PV.rotation_skip(100, 9000, today=datetime(2026, 10, 12))
    assert a != b and 0 <= a < 9000 and 0 <= b < 9000
    assert b - a == 100 or b < a                                      # next window, or wrapped
    assert PV.rotation_skip(0, 0) == PV.rotation_skip(1, 1) == 0


def test_collin_and_denton_skip_parcels_without_a_street_number(monkeypatch):
    from app.services.evosense.sources import base as B
    from app.services.evosense.sources.collin import CollinReader
    from app.services.evosense.sources.denton import DentonReader
    seen = []

    def fake(url, params=None):
        seen.append(params.get("$where") or params.get("where"))
        return [{"count": "0"}] if "data.texas.gov" in url else {"count": 0, "features": []}
    monkeypatch.setattr(B, "get_json", fake)
    CollinReader(dataset="5tkr-3759").discover(limit=5, rotate=0)
    DentonReader().discover(limit=5, rotate=0)
    assert any("situsbldgnum IS NOT NULL" in (w or "") for w in seen)
    assert any("situs_num IS NOT NULL" in (w or "") for w in seen)
