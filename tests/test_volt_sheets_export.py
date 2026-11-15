"""Unit coverage for Google Sheets projection; no real Google API or PII."""
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app.services.volt import sheets_export as s


def test_formula_injection_is_literal():
    for value in ("=IMPORTXML(1,2)", "+SUM(1,2)", "-1+3", "@cmd"):
        assert s._safe(value).startswith("'")
    assert s._safe("Normal") == "Normal"


def test_business_separation_and_test_exclusion():
    lead = SimpleNamespace(id="lead-1", is_test=True)
    with pytest.raises(ValueError, match="Test leads"):
        s.consumer_row(lead)
    rep = SimpleNamespace(is_test=True)
    with pytest.raises(ValueError, match="Test applicants"):
        s.recruiting_row(SimpleNamespace(), rep)


def test_consumer_mapping_preserves_source_and_attribution():
    lead = SimpleNamespace(
        id="lead-1", is_test=False, created_at=datetime(2026, 11, 15, tzinfo=timezone.utc),
        first_name="Alex", last_name="Person", phone="5550100",
        email="test@example.invalid", state="TX", zip_code="76101",
        source="facebook", utm_campaign="aca_nov", campaign="fallback",
        status="new", consent_at=None, updated_at=None, next_follow_up_at=None,
    )
    row = s.consumer_row(lead)
    assert len(row) == len(s.SCHEMAS["consumer"])
    assert row[0] == "lead-1"
    assert row[7:10] == ["facebook", "aca_nov", "new"]


class FakeCall:
    def __init__(self, result):
        self.result = result

    def execute(self):
        return self.result


class FakeValues:
    def __init__(self, ids):
        self.ids = ids
        self.writes = []

    def get(self, **kwargs):
        return FakeCall({"values": [[x] for x in self.ids]})

    def update(self, **kwargs):
        self.writes.append(("update", kwargs))
        return FakeCall({})

    def append(self, **kwargs):
        self.writes.append(("append", kwargs))
        return FakeCall({})


class FakeSheets:
    def __init__(self, ids):
        self.v = FakeValues(ids)

    def spreadsheets(self):
        return self

    def values(self):
        return self.v


def test_idempotent_update_by_stable_id(monkeypatch):
    monkeypatch.setenv("VOLT_CONSUMER_SHEETS_ID", "sheet-for-consumer")
    api = FakeSheets(["old-lead", "lead-1"])
    row = ["lead-1"] + [""] * (len(s.SCHEMAS["consumer"]) - 1)
    out = s.sync_row("consumer", row, api=api)
    assert out == {"action": "updated", "row": 3}
    assert api.v.writes[0][0] == "update"
    assert "A3:N3" in api.v.writes[0][1]["range"]


def test_new_record_appends(monkeypatch):
    monkeypatch.setenv("VOLT_RECRUITING_SHEETS_ID", "sheet-for-recruitment")
    api = FakeSheets([])
    row = ["application-1"] + [""] * (len(s.SCHEMAS["recruiting"]) - 1)
    out = s.sync_row("recruiting", row, api=api)
    assert out == {"action": "inserted"}
    assert api.v.writes[0][0] == "append"


def test_business_key_guard():
    with pytest.raises(ValueError):
        s.sync_row("hip", ["x"])
