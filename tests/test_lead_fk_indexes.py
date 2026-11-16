"""Deleting leads must not scan child tables once per deleted lead.

Overnight audit #650 (2026-11-15): one `delete from leads where organization_id=...`
of 10,576 leads ran 1,173 s in production. Every foreign key into leads that has
no index makes Postgres scan that child table for each deleted row. While the
statement held the database, every program screen and sign-in returned 500 for
about five minutes. Same cause and cure as tests/test_staged_row_self_fk_index.py.
"""
import re

from app.auto_migrate import INDEXES_TO_CREATE
from app.models.models import ContactRegistry, Lead
from app.models.program_models import ProgramSourceRecord

# Every foreign key into leads that production showed with no supporting index.
LEAD_FK_COLUMNS = [
    ("leads", "duplicate_of_lead_id"),
    ("program_source_records", "lead_id"),
    ("contact_registry", "first_seen_lead_id"),
    ("sms_consent_records", "lead_id"),
    ("notifications", "lead_id"),
    ("lead_outcomes", "lead_id"),
    ("conversation_states", "lead_id"),
    ("wholesale_deals", "seller_lead_id"),
    ("wholesale_seller_profiles", "lead_id"),
    ("wholesale_field_appointments", "lead_id"),
    ("wholesale_enrichment_requests", "lead_id"),
    ("wholesale_seller_callbacks", "lead_id"),
    ("wholesale_conversation_controls", "lead_id"),
    ("wholesale_temperature_overrides", "lead_id"),
    ("jewelry_ai_exchanges", "lead_id"),
    ("program_lead_files", "lead_id"),
    ("auto_send_candidates", "lead_id"),
    ("auto_send_queue", "lead_id"),
    ("auto_sent_log", "lead_id"),
    ("lead_referrals", "source_lead_id"),
    ("lead_referrals", "referred_lead_id"),
]


def _leading_column(sql: str):
    m = re.search(r"\bON\s+(\w+)\s*\(\s*(\w+)", sql, re.I)
    return (m.group(1), m.group(2)) if m else None


def test_auto_migrate_indexes_every_lead_foreign_key():
    covered = {_leading_column(s) for s in INDEXES_TO_CREATE}
    missing = [fk for fk in LEAD_FK_COLUMNS if fk not in covered]
    assert not missing, f"lead FKs with no leading-column index: {missing}"


def test_lead_fk_index_names_are_unique():
    names = [re.search(r"EXISTS\s+(\w+)", s).group(1) for s in INDEXES_TO_CREATE
             if re.search(r"EXISTS\s+(\w+)", s)]
    assert len(names) == len(set(names))


def test_models_index_the_largest_lead_foreign_keys():
    """Fresh databases (create_all) get the three biggest ones from the model."""
    for model, col in [(Lead, "duplicate_of_lead_id"),
                       (ProgramSourceRecord, "lead_id"),
                       (ContactRegistry, "first_seen_lead_id")]:
        cols = [tuple(c.name for c in ix.columns) for ix in model.__table__.indexes]
        assert (col,) in cols, f"{model.__tablename__}.{col} not indexed"
