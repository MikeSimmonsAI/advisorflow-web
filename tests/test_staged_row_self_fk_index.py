"""Re-analysing a large import batch deletes its staged rows first. Each delete
makes Postgres look up rows whose duplicate_of_staged_row_id points at it (the
self-FK is ON DELETE SET NULL); without an index that is a full scan per row,
and re-analysing the 14,712-row Atlantis batch took ~10 minutes (2026-09-28)."""
from app.auto_migrate import INDEXES_TO_CREATE
from app.models.import_models import ImportStagedRow


def test_model_indexes_the_self_foreign_key():
    cols = [tuple(c.name for c in ix.columns) for ix in ImportStagedRow.__table__.indexes]
    assert ("duplicate_of_staged_row_id",) in cols


def test_auto_migrate_creates_the_index_on_existing_databases():
    assert any("ix_isr_duplicate_of" in s and "duplicate_of_staged_row_id" in s
               for s in INDEXES_TO_CREATE)
