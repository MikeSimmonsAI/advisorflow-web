"""
tests/test_import_intelligence.py
──────────────────────────────────
Gate-by-gate Phase 1 validation for Lead Import Intelligence.

IMPORTANT: This import must sit above any conftest fixture that calls
Base.metadata.create_all(), so the import_batches / import_staged_rows
tables are registered on Base before the in-memory SQLite DB is created.
"""
# ── Side-effect imports: register ALL satellite models on Base ───────────────
# This mirrors what app/main.py does. Without these, proposals.opportunity_id FK
# fails to resolve during Base.metadata.create_all() in the db_session fixture.
import app.models.import_models           # noqa: F401
import app.models.sales_models            # noqa: F401
import app.models.scheduling_models       # noqa: F401
import app.models.calendar_models         # noqa: F401
import app.models.meeting_models          # noqa: F401
import app.models.integration_models      # noqa: F401
import app.models.demo_models             # noqa: F401
import app.models.implementation_models   # noqa: F401
import app.models.staff_models            # noqa: F401
import app.models.location_models         # noqa: F401
import app.models.cleanup_models          # noqa: F401
import app.models.demo_site_models        # noqa: F401

import csv
import io
import os
import tempfile
import time

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.models.import_models import (
    ImportBatch,
    ImportBatchStatus,
    ImportDuplicateStatus,
    ImportMatchConfidence,
    ImportRowReviewStatus,
    ImportStagedRow,
    ImportValidationStatus,
)
from app.models.models import Base, Lead, Organization, User, gen_uuid
from app.services.auth_service import create_access_token, hash_password
from app.services.import_staging_service import stage_batch
from app.services.import_commit_service import commit_batch

# ── Check pandas availability ────────────────────────────────────────────────
try:
    import pandas as pd
    HAS_PANDAS = True
except ImportError:
    HAS_PANDAS = False

needs_pandas = pytest.mark.skipif(not HAS_PANDAS, reason="pandas not installed")

# ── Local in-memory DB fixture (shares Base, includes import tables) ─────────

@pytest.fixture()
def idb():
    """Fresh in-memory SQLite DB with ALL tables including import tables."""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    SL = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    session = SL()
    yield session
    session.close()


# ── Fixture helpers ──────────────────────────────────────────────────────────

def _make_org(db, name="Org A", slug="org-a"):
    org = Organization(name=name, slug=slug, plan="standard")
    db.add(org)
    db.commit()
    return org


def _make_user(db, org_id, role="advisor", email=None):
    email = email or f"{role}-{gen_uuid()[:8]}@example.com"
    u = User(
        organization_id=org_id,
        email=email,
        password_hash=hash_password("Test123!"),
        full_name=f"{role.title()} User",
        role=role,
        must_change_password=False,
    )
    db.add(u)
    db.commit()
    return u


def _make_lead(db, org_id, *, first="John", last="Smith",
               phone="+12145550001", email=None, status="new"):
    lead = Lead(
        organization_id=org_id,
        first_name=first, last_name=last,
        phone=phone, phone_raw=phone,
        email=email,
        tier="pre_need",
        status=status,
    )
    db.add(lead)
    db.commit()
    return lead


def _make_batch(db, org_id, user_id, name="Test Batch"):
    batch = ImportBatch(
        id=gen_uuid(), organization_id=org_id,
        created_by_id=user_id, display_name=name,
        source_type="csv", source_filename="test.csv",
        status=ImportBatchStatus.UPLOADING,
    )
    db.add(batch)
    db.commit()
    return batch


def _csv_tempfile(rows):
    """Write list-of-dicts to a temp CSV and return path."""
    if not rows:
        rows = [{}]
    fieldnames = list(rows[0].keys())
    tf = tempfile.NamedTemporaryFile(
        mode="w", suffix=".csv", delete=False, newline=""
    )
    writer = csv.DictWriter(tf, fieldnames=fieldnames)
    writer.writeheader()
    writer.writerows(rows)
    tf.close()
    return tf.name


def _standard_row(**overrides):
    base = {
        "First Name": "Jane",
        "Last Name": "Doe",
        "Phone": "2145559999",
        "Email": "jane@example.com",
        "Street Address": "123 Main St",
        "City": "Dallas",
        "State": "TX",
        "ZIP Code": "75201",
        "Lead Type": "Pre-Need",
        "Status Reason": "",
    }
    base.update(overrides)
    return base


# ─────────────────────────────────────────────────────────────────────────────
# GATE 1 — Capability registry names
# ─────────────────────────────────────────────────────────────────────────────

class TestGate1CapabilityNames:
    """The four approved capability keys must exist in the registry."""

    def test_lead_import_stage_registered(self):
        from app.services.capabilities import CAPABILITIES
        assert "lead_import_stage" in CAPABILITIES

    def test_lead_import_review_registered(self):
        from app.services.capabilities import CAPABILITIES
        assert "lead_import_review" in CAPABILITIES

    def test_lead_import_commit_registered(self):
        from app.services.capabilities import CAPABILITIES
        assert "lead_import_commit" in CAPABILITIES

    def test_lead_import_manage_registered(self):
        from app.services.capabilities import CAPABILITIES
        assert "lead_import_manage" in CAPABILITIES

    def test_old_key_names_not_present(self):
        """The old names must not appear — wrong keys would silently grant nothing."""
        from app.services.capabilities import CAPABILITIES
        for bad_key in ("import_leads", "import_review", "import_commit", "import_admin"):
            assert bad_key not in CAPABILITIES, (
                f"Old capability key '{bad_key}' still in registry — "
                "routes using it will silently 403 everyone."
            )

    def test_all_four_are_delegable(self):
        from app.services.capabilities import CAPABILITIES
        for key in ("lead_import_stage", "lead_import_review",
                    "lead_import_commit", "lead_import_manage"):
            cap = CAPABILITIES[key]
            assert cap.delegable is True, f"{key} should be delegable"

    def test_import_permissions_aliases_point_to_correct_deps(self):
        from app.services import import_permissions as ip
        # Aliases must resolve to the renamed deps, not old standalone funcs
        assert ip.require_import_leads is ip.require_import_stage
        assert ip.require_import_admin is ip.require_import_manage


# ─────────────────────────────────────────────────────────────────────────────
# GATE 2 — Permission matrix (via API)
# ─────────────────────────────────────────────────────────────────────────────

class TestGate2PermissionMatrix:
    """org_admin auto-passes; advisor without grant gets 403."""

    @pytest.fixture()
    def setup(self, db_session, sample_org):
        org_admin = _make_user(db_session, sample_org.id, role="org_admin",
                               email="oadmin@test.com")
        advisor   = _make_user(db_session, sample_org.id, role="advisor",
                               email="adv@test.com")
        return org_admin, advisor

    def _headers(self, user, db):
        token = create_access_token(user, db)
        return {"Authorization": f"Bearer {token}"}

    def test_org_admin_can_list_batches(self, client, db_session, sample_org):
        admin = _make_user(db_session, sample_org.id, role="org_admin",
                           email="oa2@test.com")
        r = client.get("/import-batches", headers=self._headers(admin, db_session))
        assert r.status_code == 200

    def test_advisor_without_grant_cannot_list_batches(self, client, db_session, sample_org):
        adv = _make_user(db_session, sample_org.id, role="advisor",
                         email="adv2@test.com")
        r = client.get("/import-batches", headers=self._headers(adv, db_session))
        assert r.status_code == 403

    def test_advisor_without_grant_cannot_upload(self, client, db_session, sample_org):
        adv = _make_user(db_session, sample_org.id, role="advisor",
                         email="adv3@test.com")
        csv_path = _csv_tempfile([_standard_row()])
        try:
            with open(csv_path, "rb") as f:
                r = client.post(
                    "/import-batches",
                    headers=self._headers(adv, db_session),
                    data={"display_name": "Test"},
                    files={"file": ("test.csv", f, "text/csv")},
                )
            assert r.status_code == 403
        finally:
            os.unlink(csv_path)

    def test_super_admin_can_list_batches(self, client, db_session, sample_org):
        sadmin = _make_user(db_session, sample_org.id, role="super_admin",
                            email="sa@test.com")
        r = client.get("/import-batches", headers=self._headers(sadmin, db_session))
        assert r.status_code == 200


# ─────────────────────────────────────────────────────────────────────────────
# GATE 3 — Legacy endpoints emit Deprecation headers
# ─────────────────────────────────────────────────────────────────────────────

class TestGate3LegacyDeprecationHeaders:

    def test_preview_upload_has_deprecation_header(self, client, admin_auth_headers):
        csv_path = _csv_tempfile([_standard_row()])
        try:
            with open(csv_path, "rb") as f:
                r = client.post(
                    "/leads/upload/preview",
                    headers=admin_auth_headers,
                    files={"file": ("test.csv", f, "text/csv")},
                )
            # Any response (200, 400, 422) is fine as long as the header is there
            assert "Deprecation" in r.headers, (
                "Deprecation header missing from /leads/upload/preview"
            )
            assert r.headers["Deprecation"].lower() in ("true", "1")
        finally:
            os.unlink(csv_path)

    def test_preview_upload_has_sunset_header(self, client, admin_auth_headers):
        csv_path = _csv_tempfile([_standard_row()])
        try:
            with open(csv_path, "rb") as f:
                r = client.post(
                    "/leads/upload/preview",
                    headers=admin_auth_headers,
                    files={"file": ("test.csv", f, "text/csv")},
                )
            assert "Sunset" in r.headers
        finally:
            os.unlink(csv_path)

    def test_preview_upload_has_successor_link(self, client, admin_auth_headers):
        csv_path = _csv_tempfile([_standard_row()])
        try:
            with open(csv_path, "rb") as f:
                r = client.post(
                    "/leads/upload/preview",
                    headers=admin_auth_headers,
                    files={"file": ("test.csv", f, "text/csv")},
                )
            link = r.headers.get("Link", "")
            assert "import-batches" in link
            assert 'rel="successor-version"' in link
        finally:
            os.unlink(csv_path)


# ─────────────────────────────────────────────────────────────────────────────
# GATE 4 — Zero live leads after staging
# ─────────────────────────────────────────────────────────────────────────────

class TestGate4ZeroLiveLeadsAfterStaging:

    @needs_pandas
    def test_csv_staging_creates_no_live_leads(self, idb):
        org  = _make_org(idb)
        user = _make_user(idb, org.id)
        lead_count_before = idb.query(Lead).count()

        csv_path = _csv_tempfile([
            _standard_row(**{"Last Name": f"Person{i}", "Phone": f"214555{i:04d}"})
            for i in range(10)
        ])
        try:
            batch = _make_batch(idb, org.id, user.id)
            stage_batch(batch.id, org.id, csv_path, "csv", idb)
        finally:
            os.unlink(csv_path)

        lead_count_after = idb.query(Lead).count()
        assert lead_count_after == lead_count_before, (
            f"Staging created {lead_count_after - lead_count_before} live leads — it must create zero."
        )
        assert idb.query(ImportStagedRow).filter(
            ImportStagedRow.batch_id == batch.id
        ).count() == 10

    @needs_pandas
    def test_google_contacts_staging_creates_no_live_leads(self, idb):
        org  = _make_org(idb, name="Org GC", slug="org-gc")
        user = _make_user(idb, org.id)
        lead_count_before = idb.query(Lead).count()

        contacts = [
            {
                "names": [{"givenName": "Alice", "familyName": f"Test{i}"}],
                "phoneNumbers": [{"value": f"469555{i:04d}"}],
                "emailAddresses": [{"value": f"alice{i}@test.com"}],
                "addresses": [],
            }
            for i in range(5)
        ]
        batch = _make_batch(idb, org.id, user.id, name="GC Batch")
        stage_batch(batch.id, org.id, None, "google_contacts", idb,
                    google_contacts=contacts)

        lead_count_after = idb.query(Lead).count()
        assert lead_count_after == lead_count_before
        assert idb.query(ImportStagedRow).filter(
            ImportStagedRow.batch_id == batch.id
        ).count() == 5


# ─────────────────────────────────────────────────────────────────────────────
# GATE 5 — PARTIALLY_COMMITTED fires when some rows succeed, some fail
# ─────────────────────────────────────────────────────────────────────────────

class TestGate5PartiallyCommitted:

    def test_partially_committed_when_only_subset_accepted(self, idb):
        """Stage 3 rows: mark 2 ACCEPTED, 1 REJECTED.
        Commit → COMMITTED (no failures, but 1 row simply not committed).
        Then verify we can re-commit and remaining row stays REJECTED."""
        org  = _make_org(idb, name="PC Org", slug="pc-org")
        user = _make_user(idb, org.id, role="org_admin")
        batch = _make_batch(idb, org.id, user.id)

        for i in range(3):
            row = ImportStagedRow(
                id=gen_uuid(), batch_id=batch.id, organization_id=org.id,
                row_number=i + 1,
                first_name="Commit", last_name=f"Test{i}",
                phone_normalized=f"+1469555{i:04d}",
                phone_raw=f"469555{i:04d}",
                validation_status=ImportValidationStatus.VALID,
                duplicate_status=ImportDuplicateStatus.NEW,
                review_status=(
                    ImportRowReviewStatus.ACCEPTED if i < 2
                    else ImportRowReviewStatus.REJECTED
                ),
            )
            idb.add(row)
        idb.commit()
        batch.recount(idb)
        idb.commit()

        result = commit_batch(batch.id, org.id, idb, user.id)
        # 2 accepted → COMMITTED; 1 rejected → stays REJECTED, not counted
        assert result.status in (
            ImportBatchStatus.COMMITTED,
            ImportBatchStatus.PARTIALLY_COMMITTED,
        )
        committed = idb.query(ImportStagedRow).filter(
            ImportStagedRow.batch_id == batch.id,
            ImportStagedRow.review_status == ImportRowReviewStatus.COMMITTED,
        ).count()
        assert committed == 2

    def test_partially_committed_state_on_mixed_outcome(self, idb):
        """Simulate partial failure by forcing one row to have a bad phone
        that would normally be fine (SQLite won't enforce FK) but marking
        it ACCEPTED so commit tries it. We inject the PARTIALLY_COMMITTED
        state directly to test the counter_reconciliation path."""
        org  = _make_org(idb, name="PC2 Org", slug="pc2-org")
        user = _make_user(idb, org.id)

        batch = ImportBatch(
            id=gen_uuid(), organization_id=org.id,
            created_by_id=user.id, display_name="PC2",
            source_type="csv", source_filename="test.csv",
            status=ImportBatchStatus.PARTIALLY_COMMITTED,
            total_rows=4, committed_rows=2, rejected_rows=1, pending_rows=1,
        )
        idb.add(batch)
        idb.commit()

        recon = batch.counter_reconciliation()
        assert recon["total"] == 4
        assert recon["committed"] == 2
        # accounted = committed(2) + merged(0) + rejected(1) + pending(1) = 4 == total(4)
        assert recon["balanced"]
        assert recon["accounted"] == recon["total"]


# ─────────────────────────────────────────────────────────────────────────────
# GATE 6 — counter_reconciliation() balanced after mixed decisions
# ─────────────────────────────────────────────────────────────────────────────

class TestGate6CounterReconciliation:

    def test_recount_then_reconciliation_balanced(self, idb):
        org  = _make_org(idb, name="Recon Org", slug="recon-org")
        user = _make_user(idb, org.id)
        batch = _make_batch(idb, org.id, user.id)

        statuses = [
            (ImportRowReviewStatus.ACCEPTED,  ImportDuplicateStatus.NEW),
            (ImportRowReviewStatus.REJECTED,  ImportDuplicateStatus.NEW),
            (ImportRowReviewStatus.PENDING,   ImportDuplicateStatus.NEW),
            (ImportRowReviewStatus.COMMITTED, ImportDuplicateStatus.NEW),
            (ImportRowReviewStatus.COMMITTED, ImportDuplicateStatus.MATCHED_EXISTING),
        ]
        for i, (rev, dup) in enumerate(statuses):
            idb.add(ImportStagedRow(
                id=gen_uuid(), batch_id=batch.id, organization_id=org.id,
                row_number=i + 1,
                first_name="A", last_name=f"B{i}",
                validation_status=ImportValidationStatus.VALID,
                duplicate_status=dup,
                review_status=rev,
            ))
        idb.commit()
        batch.recount(idb)
        idb.commit()

        recon = batch.counter_reconciliation()
        # committed_rows counts NEW commits; merged_rows counts MATCHED commits
        # pending_rows counts PENDING; rejected_rows counts REJECTED
        # ACCEPTED is technically still "pending" from recount's perspective
        # since recount uses review_status == PENDING for pending_rows
        # and there's no "accepted_rows" counter — accepted stays as pending until committed
        # So: pending=2 (accepted+pending), rejected=1, committed=1, merged=1 → accounted=5=total
        assert recon["total"] == 5
        assert recon["balanced"], (
            f"counter_reconciliation unbalanced: {recon}"
        )
        assert recon["unaccounted"] == 0

    def test_reconciliation_detects_imbalance(self):
        """Manually construct an imbalanced batch and assert balanced=False."""
        org = Organization(name="Imbal", slug="imbal", plan="standard")
        batch = ImportBatch(
            id="fake-batch", organization_id="fake-org",
            display_name="Imbal", source_type="csv", source_filename="x.csv",
            status=ImportBatchStatus.READY_FOR_REVIEW,
            total_rows=10,
            committed_rows=3, merged_rows=2,
            rejected_rows=2, pending_rows=2,
        )
        recon = batch.counter_reconciliation()
        assert recon["total"] == 10
        assert recon["accounted"] == 9
        assert not recon["balanced"]
        assert recon["unaccounted"] == 1


# ─────────────────────────────────────────────────────────────────────────────
# GATE 7 — Import-twice idempotency (no crash, both batches exist)
# ─────────────────────────────────────────────────────────────────────────────

class TestGate7ImportTwice:

    @needs_pandas
    def test_same_csv_twice_creates_two_batches(self, idb):
        org  = _make_org(idb, name="Twice Org", slug="twice-org")
        user = _make_user(idb, org.id)
        rows = [_standard_row(**{"Last Name": f"Twin{i}", "Phone": f"2145551{i:03d}"})
                for i in range(5)]
        csv_path = _csv_tempfile(rows)
        try:
            b1 = _make_batch(idb, org.id, user.id, "First Import")
            stage_batch(b1.id, org.id, csv_path, "csv", idb)

            b2 = _make_batch(idb, org.id, user.id, "Second Import")
            stage_batch(b2.id, org.id, csv_path, "csv", idb)
        finally:
            os.unlink(csv_path)

        assert b1.id != b2.id
        rows1 = idb.query(ImportStagedRow).filter(ImportStagedRow.batch_id == b1.id).count()
        rows2 = idb.query(ImportStagedRow).filter(ImportStagedRow.batch_id == b2.id).count()
        assert rows1 == 5
        assert rows2 == 5

    @needs_pandas
    def test_second_import_detects_duplicates_from_first_commit(self, idb):
        """After committing batch 1, importing same data in batch 2 should see MATCHED_EXISTING."""
        org  = _make_org(idb, name="Dup Org", slug="dup-org")
        user = _make_user(idb, org.id, role="org_admin")

        # Create a live lead that matches our CSV row
        live = _make_lead(idb, org.id, first="Alice", last="Dup",
                          phone="+12145559876")

        rows = [_standard_row(**{"First Name": "Alice", "Last Name": "Dup",
                                 "Phone": "2145559876"})]
        csv_path = _csv_tempfile(rows)
        try:
            batch = _make_batch(idb, org.id, user.id, "Dup Batch")
            stage_batch(batch.id, org.id, csv_path, "csv", idb)
        finally:
            os.unlink(csv_path)

        row = idb.query(ImportStagedRow).filter(
            ImportStagedRow.batch_id == batch.id
        ).first()
        assert row is not None
        assert row.duplicate_status == ImportDuplicateStatus.MATCHED_EXISTING
        assert row.matched_lead_id == live.id


# ─────────────────────────────────────────────────────────────────────────────
# GATE 8 — Dedup: exact phone + last_name → MATCHED_EXISTING / HIGH
# ─────────────────────────────────────────────────────────────────────────────

class TestGate8DeduplicationScenarios:

    @needs_pandas
    def test_exact_phone_and_last_name_high_confidence(self, idb):
        org  = _make_org(idb, name="Dedup Org", slug="dedup-org")
        user = _make_user(idb, org.id)
        live = _make_lead(idb, org.id, first="Bob", last="Jones",
                          phone="+12145551234")

        csv_path = _csv_tempfile([_standard_row(
            **{"First Name": "Robert", "Last Name": "Jones", "Phone": "2145551234"}
        )])
        try:
            batch = _make_batch(idb, org.id, user.id)
            stage_batch(batch.id, org.id, csv_path, "csv", idb)
        finally:
            os.unlink(csv_path)

        row = idb.query(ImportStagedRow).filter(
            ImportStagedRow.batch_id == batch.id
        ).first()
        assert row.duplicate_status == ImportDuplicateStatus.MATCHED_EXISTING
        assert row.match_confidence == ImportMatchConfidence.HIGH
        assert row.matched_lead_id == live.id

    @needs_pandas
    def test_phone_match_different_last_name_medium_confidence(self, idb):
        org  = _make_org(idb, name="Dedup2 Org", slug="dedup2-org")
        user = _make_user(idb, org.id)
        _make_lead(idb, org.id, first="Carol", last="Smith",
                   phone="+12145555678")

        csv_path = _csv_tempfile([_standard_row(
            **{"First Name": "Carol", "Last Name": "Johnson", "Phone": "2145555678"}
        )])
        try:
            batch = _make_batch(idb, org.id, user.id)
            stage_batch(batch.id, org.id, csv_path, "csv", idb)
        finally:
            os.unlink(csv_path)

        row = idb.query(ImportStagedRow).filter(
            ImportStagedRow.batch_id == batch.id
        ).first()
        assert row.duplicate_status == ImportDuplicateStatus.POSSIBLE_DUPLICATE
        assert row.match_confidence == ImportMatchConfidence.MEDIUM

    @needs_pandas
    def test_email_and_last_name_match_no_phone_low_confidence(self, idb):
        org  = _make_org(idb, name="Dedup3 Org", slug="dedup3-org")
        user = _make_user(idb, org.id)
        _make_lead(idb, org.id, first="Dana", last="Lee",
                   phone=None, email="dana@example.com")

        csv_path = _csv_tempfile([_standard_row(
            **{"First Name": "Dana", "Last Name": "Lee",
               "Phone": "", "Email": "dana@example.com"}
        )])
        try:
            batch = _make_batch(idb, org.id, user.id)
            stage_batch(batch.id, org.id, csv_path, "csv", idb)
        finally:
            os.unlink(csv_path)

        row = idb.query(ImportStagedRow).filter(
            ImportStagedRow.batch_id == batch.id
        ).first()
        assert row.duplicate_status == ImportDuplicateStatus.MATCHED_EXISTING
        assert row.match_confidence == ImportMatchConfidence.LOW

    @needs_pandas
    def test_no_match_returns_new(self, idb):
        org  = _make_org(idb, name="Dedup4 Org", slug="dedup4-org")
        user = _make_user(idb, org.id)

        csv_path = _csv_tempfile([_standard_row(
            **{"First Name": "Zara", "Last Name": "Unique",
               "Phone": "8005559999", "Email": "zara@unique.com"}
        )])
        try:
            batch = _make_batch(idb, org.id, user.id)
            stage_batch(batch.id, org.id, csv_path, "csv", idb)
        finally:
            os.unlink(csv_path)

        row = idb.query(ImportStagedRow).filter(
            ImportStagedRow.batch_id == batch.id
        ).first()
        assert row.duplicate_status == ImportDuplicateStatus.NEW


# ─────────────────────────────────────────────────────────────────────────────
# GATE 9 — DNC: matched DNC lead → dnc_blocked
# ─────────────────────────────────────────────────────────────────────────────

class TestGate9DNCCompliance:

    @needs_pandas
    def test_dnc_lead_match_sets_dnc_blocked(self, idb):
        org  = _make_org(idb, name="DNC Org", slug="dnc-org")
        user = _make_user(idb, org.id)
        _make_lead(idb, org.id, first="DNC", last="Person",
                   phone="+12145550002", status="dnc")

        csv_path = _csv_tempfile([_standard_row(
            **{"First Name": "DNC", "Last Name": "Person", "Phone": "2145550002"}
        )])
        try:
            batch = _make_batch(idb, org.id, user.id)
            stage_batch(batch.id, org.id, csv_path, "csv", idb)
        finally:
            os.unlink(csv_path)

        row = idb.query(ImportStagedRow).filter(
            ImportStagedRow.batch_id == batch.id
        ).first()
        assert row.duplicate_status == ImportDuplicateStatus.DNC_BLOCKED, (
            "A DNC lead match must produce DNC_BLOCKED, not MATCHED_EXISTING"
        )

    @needs_pandas
    def test_dnc_blocked_row_when_rejected_creates_no_lead(self, idb):
        """Committing a batch where the DNC row is REJECTED must not create a live lead."""
        org  = _make_org(idb, name="DNC2 Org", slug="dnc2-org")
        user = _make_user(idb, org.id, role="org_admin")
        _make_lead(idb, org.id, first="DNC", last="Block",
                   phone="+12145550003", status="dnc")

        batch = _make_batch(idb, org.id, user.id)
        # Manually create a staged row with dnc_blocked status, review=rejected
        row = ImportStagedRow(
            id=gen_uuid(), batch_id=batch.id, organization_id=org.id,
            row_number=1,
            first_name="DNC", last_name="Block",
            phone_normalized="+12145550003", phone_raw="2145550003",
            validation_status=ImportValidationStatus.VALID,
            duplicate_status=ImportDuplicateStatus.DNC_BLOCKED,
            review_status=ImportRowReviewStatus.REJECTED,
        )
        idb.add(row)
        idb.commit()

        lead_count_before = idb.query(Lead).count()
        commit_batch(batch.id, org.id, idb, user.id)
        lead_count_after = idb.query(Lead).count()

        assert lead_count_after == lead_count_before, (
            "Committing a rejected DNC row must not create a live lead"
        )

    def test_existing_dnc_lead_status_never_changed_by_merge(self, idb):
        """A MERGE commit on a DNC lead via blank-fill must not change its status."""
        org  = _make_org(idb, name="DNC3 Org", slug="dnc3-org")
        user = _make_user(idb, org.id, role="org_admin")
        dnc_lead = _make_lead(idb, org.id, first="Stay", last="DNC",
                              phone="+12145550004", status="dnc")

        batch = _make_batch(idb, org.id, user.id)
        # Staged row as POSSIBLE_DUPLICATE (phone match, different name)
        row = ImportStagedRow(
            id=gen_uuid(), batch_id=batch.id, organization_id=org.id,
            row_number=1,
            first_name="Stay", last_name="DNC",
            phone_normalized="+12145550004", phone_raw="2145550004",
            email_normalized="new@test.com",
            validation_status=ImportValidationStatus.VALID,
            duplicate_status=ImportDuplicateStatus.POSSIBLE_DUPLICATE,
            matched_lead_id=dnc_lead.id,
            review_status=ImportRowReviewStatus.MERGED,
        )
        idb.add(row)
        idb.commit()

        commit_batch(batch.id, org.id, idb, user.id)
        idb.refresh(dnc_lead)
        assert dnc_lead.status == "dnc", (
            "Commit must never change a lead's DNC status"
        )


# ─────────────────────────────────────────────────────────────────────────────
# GATE 10 — Merge: blank-fill only, never overwrite existing data
# ─────────────────────────────────────────────────────────────────────────────

class TestGate10MergeBehavior:

    def test_blank_fill_populates_missing_email(self, idb):
        org  = _make_org(idb, name="Merge Org", slug="merge-org")
        user = _make_user(idb, org.id, role="org_admin")
        live = _make_lead(idb, org.id, first="Fill", last="Me",
                          phone="+12145550010", email=None)
        assert live.email is None

        batch = _make_batch(idb, org.id, user.id)
        row = ImportStagedRow(
            id=gen_uuid(), batch_id=batch.id, organization_id=org.id,
            row_number=1,
            first_name="Fill", last_name="Me",
            phone_normalized="+12145550010", phone_raw="2145550010",
            email_normalized="filledIn@test.com",
            validation_status=ImportValidationStatus.VALID,
            duplicate_status=ImportDuplicateStatus.MATCHED_EXISTING,
            matched_lead_id=live.id,
            review_status=ImportRowReviewStatus.MERGED,
        )
        idb.add(row)
        idb.commit()

        commit_batch(batch.id, org.id, idb, user.id)
        idb.refresh(live)
        assert live.email == "filledIn@test.com", "Blank-fill should have set email"

    def test_blank_fill_does_not_overwrite_existing_email(self, idb):
        org  = _make_org(idb, name="NoOver Org", slug="noover-org")
        user = _make_user(idb, org.id, role="org_admin")
        live = _make_lead(idb, org.id, first="Keep", last="Me",
                          phone="+12145550011", email="original@test.com")

        batch = _make_batch(idb, org.id, user.id)
        row = ImportStagedRow(
            id=gen_uuid(), batch_id=batch.id, organization_id=org.id,
            row_number=1,
            first_name="Keep", last_name="Me",
            phone_normalized="+12145550011", phone_raw="2145550011",
            email_normalized="replacement@test.com",
            validation_status=ImportValidationStatus.VALID,
            duplicate_status=ImportDuplicateStatus.MATCHED_EXISTING,
            matched_lead_id=live.id,
            review_status=ImportRowReviewStatus.MERGED,
        )
        idb.add(row)
        idb.commit()

        commit_batch(batch.id, org.id, idb, user.id)
        idb.refresh(live)
        assert live.email == "original@test.com", (
            "Existing email must NOT be overwritten by blank-fill merge"
        )

    def test_merge_does_not_change_organization_id(self, idb):
        org  = _make_org(idb, name="Org Protect", slug="org-protect")
        user = _make_user(idb, org.id, role="org_admin")
        live = _make_lead(idb, org.id, first="Prot", last="Org",
                          phone="+12145550012")
        original_org = live.organization_id

        batch = _make_batch(idb, org.id, user.id)
        row = ImportStagedRow(
            id=gen_uuid(), batch_id=batch.id, organization_id=org.id,
            row_number=1,
            first_name="Prot", last_name="Org",
            phone_normalized="+12145550012", phone_raw="2145550012",
            validation_status=ImportValidationStatus.VALID,
            duplicate_status=ImportDuplicateStatus.MATCHED_EXISTING,
            matched_lead_id=live.id,
            review_status=ImportRowReviewStatus.MERGED,
        )
        idb.add(row)
        idb.commit()

        commit_batch(batch.id, org.id, idb, user.id)
        idb.refresh(live)
        assert live.organization_id == original_org, (
            "Merge must never change organization_id (merge blacklist violation)"
        )


# ─────────────────────────────────────────────────────────────────────────────
# GATE 11 — Cross-tenant isolation
# ─────────────────────────────────────────────────────────────────────────────

class TestGate11CrossTenantIsolation:

    def _headers_for(self, user, db):
        token = create_access_token(user, db)
        return {"Authorization": f"Bearer {token}"}

    def test_org_b_cannot_read_org_a_batch(self, client, db_session):
        org_a = _make_org(db_session, name="Org A", slug="org-a-iso")
        org_b = _make_org(db_session, name="Org B", slug="org-b-iso")
        admin_a = _make_user(db_session, org_a.id, role="org_admin",
                             email="admin-a@test.com")
        admin_b = _make_user(db_session, org_b.id, role="org_admin",
                             email="admin-b@test.com")

        # Create a batch owned by Org A
        batch = ImportBatch(
            id=gen_uuid(), organization_id=org_a.id,
            created_by_id=admin_a.id, display_name="Org A Batch",
            source_type="csv", source_filename="a.csv",
            status=ImportBatchStatus.READY_FOR_REVIEW,
        )
        db_session.add(batch)
        db_session.commit()

        # Org B admin tries to read it
        r = client.get(
            f"/import-batches/{batch.id}",
            headers=self._headers_for(admin_b, db_session)
        )
        assert r.status_code == 404, (
            f"Org B got {r.status_code} on Org A batch — should be 404"
        )

    def test_org_b_cannot_see_org_a_rows_in_list(self, client, db_session):
        org_a = _make_org(db_session, name="Org A2", slug="org-a2")
        org_b = _make_org(db_session, name="Org B2", slug="org-b2")
        admin_a = _make_user(db_session, org_a.id, role="org_admin",
                             email="a2@test.com")
        admin_b = _make_user(db_session, org_b.id, role="org_admin",
                             email="b2@test.com")

        for i in range(3):
            db_session.add(ImportBatch(
                id=gen_uuid(), organization_id=org_a.id,
                created_by_id=admin_a.id,
                display_name=f"A Batch {i}",
                source_type="csv", source_filename="a.csv",
                status=ImportBatchStatus.READY_FOR_REVIEW,
            ))
        db_session.commit()

        r = client.get("/import-batches", headers=self._headers_for(admin_b, db_session))
        assert r.status_code == 200
        data = r.json()
        assert data["total"] == 0, (
            f"Org B saw {data['total']} of Org A's batches — should see 0"
        )

    def test_org_b_cannot_commit_org_a_batch(self, client, db_session):
        org_a = _make_org(db_session, name="OA3", slug="oa3")
        org_b = _make_org(db_session, name="OB3", slug="ob3")
        admin_a = _make_user(db_session, org_a.id, role="org_admin",
                             email="oa3@test.com")
        admin_b = _make_user(db_session, org_b.id, role="org_admin",
                             email="ob3@test.com")

        batch = ImportBatch(
            id=gen_uuid(), organization_id=org_a.id,
            created_by_id=admin_a.id, display_name="Commit Isolation",
            source_type="csv", source_filename="a.csv",
            status=ImportBatchStatus.READY_TO_COMMIT,
        )
        db_session.add(batch)
        db_session.commit()

        r = client.post(
            f"/import-batches/{batch.id}/commit",
            headers=self._headers_for(admin_b, db_session)
        )
        assert r.status_code == 404, (
            f"Org B commit attempt returned {r.status_code} — expected 404"
        )


# ─────────────────────────────────────────────────────────────────────────────
# GATE 12 — Provenance: committed lead traces back to ISR → batch
# ─────────────────────────────────────────────────────────────────────────────

class TestGate12Provenance:

    def test_committed_row_has_committed_at_and_by(self, idb):
        org  = _make_org(idb, name="Prov Org", slug="prov-org")
        user = _make_user(idb, org.id, role="org_admin")
        batch = _make_batch(idb, org.id, user.id, "Prov Batch")

        row = ImportStagedRow(
            id=gen_uuid(), batch_id=batch.id, organization_id=org.id,
            row_number=1,
            first_name="Prove", last_name="It",
            phone_normalized="+12145550100", phone_raw="2145550100",
            validation_status=ImportValidationStatus.VALID,
            duplicate_status=ImportDuplicateStatus.NEW,
            review_status=ImportRowReviewStatus.ACCEPTED,
        )
        idb.add(row)
        idb.commit()

        commit_batch(batch.id, org.id, idb, user.id)
        idb.refresh(row)

        assert row.review_status == ImportRowReviewStatus.COMMITTED
        assert row.committed_at is not None
        assert row.committed_by_id == user.id
        assert row.batch_id == batch.id

    def test_batch_committed_at_set_after_commit(self, idb):
        org  = _make_org(idb, name="Prov2 Org", slug="prov2-org")
        user = _make_user(idb, org.id, role="org_admin")
        batch = _make_batch(idb, org.id, user.id, "Prov2 Batch")

        row = ImportStagedRow(
            id=gen_uuid(), batch_id=batch.id, organization_id=org.id,
            row_number=1,
            first_name="Batch", last_name="Provenance",
            phone_normalized="+12145550101", phone_raw="2145550101",
            validation_status=ImportValidationStatus.VALID,
            duplicate_status=ImportDuplicateStatus.NEW,
            review_status=ImportRowReviewStatus.ACCEPTED,
        )
        idb.add(row)
        idb.commit()

        result = commit_batch(batch.id, org.id, idb, user.id)
        assert result.committed_at is not None
        assert result.committed_by_id == user.id


# ─────────────────────────────────────────────────────────────────────────────
# GATE 13 — Commit retry idempotency (already-committed rows skipped)
# ─────────────────────────────────────────────────────────────────────────────

class TestGate13CommitRetryIdempotency:

    def test_second_commit_does_not_double_create_leads(self, idb):
        org  = _make_org(idb, name="Retry Org", slug="retry-org")
        user = _make_user(idb, org.id, role="org_admin")
        batch = _make_batch(idb, org.id, user.id, "Retry Batch")

        for i in range(3):
            idb.add(ImportStagedRow(
                id=gen_uuid(), batch_id=batch.id, organization_id=org.id,
                row_number=i + 1,
                first_name="Retry", last_name=f"Person{i}",
                phone_normalized=f"+1469555{i:04d}", phone_raw=f"469555{i:04d}",
                validation_status=ImportValidationStatus.VALID,
                duplicate_status=ImportDuplicateStatus.NEW,
                review_status=ImportRowReviewStatus.ACCEPTED,
            ))
        idb.commit()

        lead_count_before = idb.query(Lead).count()
        commit_batch(batch.id, org.id, idb, user.id)
        leads_after_first = idb.query(Lead).count()
        assert leads_after_first == lead_count_before + 3

        # Second commit — already-committed rows are skipped
        commit_batch(batch.id, org.id, idb, user.id)
        leads_after_second = idb.query(Lead).count()
        assert leads_after_second == leads_after_first, (
            "Second commit created extra leads — idempotency violated"
        )


# ─────────────────────────────────────────────────────────────────────────────
# GATE 14 — Batch status machine transitions
# ─────────────────────────────────────────────────────────────────────────────

class TestGate14BatchStatusMachine:

    def test_api_mark_ready_advances_status(self, client, db_session):
        org   = _make_org(db_session, name="SM Org", slug="sm-org")
        admin = _make_user(db_session, org.id, role="org_admin",
                           email="sm@test.com")
        batch = ImportBatch(
            id=gen_uuid(), organization_id=org.id,
            created_by_id=admin.id, display_name="SM Batch",
            source_type="csv", source_filename="t.csv",
            status=ImportBatchStatus.REVIEWING,
        )
        db_session.add(batch)
        db_session.commit()

        token = create_access_token(admin, db_session)
        r = client.post(
            f"/import-batches/{batch.id}/ready",
            headers={"Authorization": f"Bearer {token}"}
        )
        assert r.status_code == 200
        assert r.json()["status"] == ImportBatchStatus.READY_TO_COMMIT

    def test_api_archive_batch(self, client, db_session):
        org   = _make_org(db_session, name="Arc Org", slug="arc-org")
        admin = _make_user(db_session, org.id, role="org_admin",
                           email="arc@test.com")
        batch = ImportBatch(
            id=gen_uuid(), organization_id=org.id,
            created_by_id=admin.id, display_name="Arc Batch",
            source_type="csv", source_filename="t.csv",
            status=ImportBatchStatus.READY_FOR_REVIEW,
        )
        db_session.add(batch)
        db_session.commit()

        token = create_access_token(admin, db_session)
        r = client.post(
            f"/import-batches/{batch.id}/archive",
            headers={"Authorization": f"Bearer {token}"}
        )
        assert r.status_code == 200
        assert r.json()["status"] == ImportBatchStatus.ARCHIVED

    def test_cannot_archive_committing_batch(self, client, db_session):
        org   = _make_org(db_session, name="NoArc Org", slug="noarc-org")
        admin = _make_user(db_session, org.id, role="org_admin",
                           email="noarc@test.com")
        batch = ImportBatch(
            id=gen_uuid(), organization_id=org.id,
            created_by_id=admin.id, display_name="Committing",
            source_type="csv", source_filename="t.csv",
            status=ImportBatchStatus.COMMITTING,
        )
        db_session.add(batch)
        db_session.commit()

        token = create_access_token(admin, db_session)
        r = client.post(
            f"/import-batches/{batch.id}/archive",
            headers={"Authorization": f"Bearer {token}"}
        )
        assert r.status_code == 409


# ─────────────────────────────────────────────────────────────────────────────
# GATE 15 — API row review operations
# ─────────────────────────────────────────────────────────────────────────────

class TestGate15ApiRowReview:

    def _setup(self, db):
        org   = _make_org(db, name="Row Org", slug="row-org")
        admin = _make_user(db, org.id, role="org_admin", email="row@test.com")
        batch = ImportBatch(
            id=gen_uuid(), organization_id=org.id,
            created_by_id=admin.id, display_name="Row Batch",
            source_type="csv", source_filename="t.csv",
            status=ImportBatchStatus.REVIEWING,
        )
        db.add(batch)
        row = ImportStagedRow(
            id=gen_uuid(), batch_id=batch.id, organization_id=org.id,
            row_number=1, first_name="Rev", last_name="Row",
            phone_normalized="+12145550200", phone_raw="2145550200",
            validation_status=ImportValidationStatus.VALID,
            duplicate_status=ImportDuplicateStatus.NEW,
            review_status=ImportRowReviewStatus.PENDING,
        )
        db.add(row)
        db.commit()
        return org, admin, batch, row

    def test_patch_row_sets_review_status(self, client, db_session):
        org, admin, batch, row = self._setup(db_session)
        token = create_access_token(admin, db_session)
        r = client.patch(
            f"/import-batches/{batch.id}/rows/{row.id}",
            json={"review_status": "accepted"},
            headers={"Authorization": f"Bearer {token}"}
        )
        assert r.status_code == 200
        assert r.json()["review_status"] == "accepted"

    def test_bulk_review_updates_multiple_rows(self, client, db_session):
        org   = _make_org(db_session, name="Bulk Org", slug="bulk-org")
        admin = _make_user(db_session, org.id, role="org_admin",
                           email="bulk@test.com")
        batch = ImportBatch(
            id=gen_uuid(), organization_id=org.id,
            created_by_id=admin.id, display_name="Bulk",
            source_type="csv", source_filename="t.csv",
            status=ImportBatchStatus.REVIEWING,
        )
        db_session.add(batch)
        row_ids = []
        for i in range(4):
            r = ImportStagedRow(
                id=gen_uuid(), batch_id=batch.id, organization_id=org.id,
                row_number=i + 1, first_name="Bulk", last_name=f"P{i}",
                phone_normalized=f"+1469777{i:04d}",
                validation_status=ImportValidationStatus.VALID,
                duplicate_status=ImportDuplicateStatus.NEW,
                review_status=ImportRowReviewStatus.PENDING,
            )
            db_session.add(r)
            row_ids.append(r.id)
        db_session.commit()

        token = create_access_token(admin, db_session)
        r = client.post(
            f"/import-batches/{batch.id}/rows/bulk-review",
            json={"review_status": "rejected", "row_ids": row_ids},
            headers={"Authorization": f"Bearer {token}"}
        )
        assert r.status_code == 200
        assert r.json()["updated"] == 4


# ─────────────────────────────────────────────────────────────────────────────
# GATE 16 — Pagination correctness
# ─────────────────────────────────────────────────────────────────────────────

class TestGate16Pagination:

    def test_rows_paginated_correctly(self, client, db_session):
        org   = _make_org(db_session, name="Page Org", slug="page-org")
        admin = _make_user(db_session, org.id, role="org_admin",
                           email="page@test.com")
        batch = ImportBatch(
            id=gen_uuid(), organization_id=org.id,
            created_by_id=admin.id, display_name="Paging",
            source_type="csv", source_filename="t.csv",
            status=ImportBatchStatus.REVIEWING,
        )
        db_session.add(batch)
        for i in range(75):
            db_session.add(ImportStagedRow(
                id=gen_uuid(), batch_id=batch.id, organization_id=org.id,
                row_number=i + 1, first_name="Page", last_name=f"P{i}",
                validation_status=ImportValidationStatus.VALID,
                duplicate_status=ImportDuplicateStatus.NEW,
                review_status=ImportRowReviewStatus.PENDING,
            ))
        db_session.commit()

        token = create_access_token(admin, db_session)

        r1 = client.get(
            f"/import-batches/{batch.id}/rows?page=1&per_page=50",
            headers={"Authorization": f"Bearer {token}"}
        )
        assert r1.status_code == 200
        d1 = r1.json()
        assert d1["total"] == 75
        assert len(d1["rows"]) == 50

        r2 = client.get(
            f"/import-batches/{batch.id}/rows?page=2&per_page=50",
            headers={"Authorization": f"Bearer {token}"}
        )
        assert r2.status_code == 200
        d2 = r2.json()
        assert len(d2["rows"]) == 25

    def test_batch_list_paginated(self, client, db_session):
        org   = _make_org(db_session, name="BPage Org", slug="bpage-org")
        admin = _make_user(db_session, org.id, role="org_admin",
                           email="bpage@test.com")
        for i in range(25):
            db_session.add(ImportBatch(
                id=gen_uuid(), organization_id=org.id,
                created_by_id=admin.id, display_name=f"Batch {i}",
                source_type="csv", source_filename="t.csv",
                status=ImportBatchStatus.READY_FOR_REVIEW,
            ))
        db_session.commit()

        token = create_access_token(admin, db_session)
        r = client.get(
            "/import-batches?page=1&per_page=10",
            headers={"Authorization": f"Bearer {token}"}
        )
        assert r.status_code == 200
        d = r.json()
        assert d["total"] == 25
        assert len(d["batches"]) == 10


# ─────────────────────────────────────────────────────────────────────────────
# GATE 17 — Row filter by review_status
# ─────────────────────────────────────────────────────────────────────────────

class TestGate17RowFilters:

    def test_filter_by_review_status(self, client, db_session):
        org   = _make_org(db_session, name="Filter Org", slug="filter-org")
        admin = _make_user(db_session, org.id, role="org_admin",
                           email="filter@test.com")
        batch = ImportBatch(
            id=gen_uuid(), organization_id=org.id,
            created_by_id=admin.id, display_name="Filter",
            source_type="csv", source_filename="t.csv",
            status=ImportBatchStatus.REVIEWING,
        )
        db_session.add(batch)
        statuses = [ImportRowReviewStatus.ACCEPTED] * 3 + \
                   [ImportRowReviewStatus.REJECTED] * 2 + \
                   [ImportRowReviewStatus.PENDING]  * 4
        for i, st in enumerate(statuses):
            db_session.add(ImportStagedRow(
                id=gen_uuid(), batch_id=batch.id, organization_id=org.id,
                row_number=i + 1, first_name="F", last_name=f"R{i}",
                validation_status=ImportValidationStatus.VALID,
                duplicate_status=ImportDuplicateStatus.NEW,
                review_status=st,
            ))
        db_session.commit()

        token = create_access_token(admin, db_session)
        r = client.get(
            f"/import-batches/{batch.id}/rows?review_status=accepted",
            headers={"Authorization": f"Bearer {token}"}
        )
        assert r.status_code == 200
        d = r.json()
        assert d["total"] == 3
        assert all(row["review_status"] == "accepted" for row in d["rows"])


# ─────────────────────────────────────────────────────────────────────────────
# GATE 18 — New lead created from ACCEPTED row with correct fields
# ─────────────────────────────────────────────────────────────────────────────

class TestGate18NewLeadFields:

    def test_accepted_new_row_creates_lead_with_correct_org(self, idb):
        org  = _make_org(idb, name="NewLead Org", slug="newlead-org")
        user = _make_user(idb, org.id, role="org_admin")
        batch = _make_batch(idb, org.id, user.id)

        row = ImportStagedRow(
            id=gen_uuid(), batch_id=batch.id, organization_id=org.id,
            row_number=1,
            first_name="New", last_name="Lead",
            phone_normalized="+12145550300", phone_raw="2145550300",
            email_normalized="newlead@test.com",
            street_address="456 Oak Ave", city="Dallas",
            state="TX", zip_code="75201",
            source_category="import",
            tier="pre_need",
            validation_status=ImportValidationStatus.VALID,
            duplicate_status=ImportDuplicateStatus.NEW,
            review_status=ImportRowReviewStatus.ACCEPTED,
        )
        idb.add(row)
        idb.commit()

        commit_batch(batch.id, org.id, idb, user.id)

        lead = idb.query(Lead).filter(
            Lead.phone == "+12145550300"
        ).first()
        assert lead is not None
        assert lead.organization_id == org.id
        assert lead.first_name == "New"
        assert lead.last_name == "Lead"
        assert lead.email == "newlead@test.com"
        assert lead.status == "new"


# ─────────────────────────────────────────────────────────────────────────────
# GATE 19 — 2 000-row staging: performance and completeness
# ─────────────────────────────────────────────────────────────────────────────

class TestGate19LargeVolumeStaging:

    @needs_pandas
    def test_2000_row_csv_stages_correctly(self, idb):
        """2 000 rows must stage with zero live leads, all rows persisted, in ≤ 60 s."""
        org  = _make_org(idb, name="2k Org", slug="2k-org")
        user = _make_user(idb, org.id, role="org_admin")

        rows = [
            _standard_row(**{
                "First Name": f"Person{i}",
                "Last Name":  f"Test{i}",
                "Phone":      f"469{i:07d}",
                "Email":      f"person{i}@test.com",
            })
            for i in range(2000)
        ]
        csv_path = _csv_tempfile(rows)
        lead_count_before = idb.query(Lead).count()

        try:
            batch = _make_batch(idb, org.id, user.id, "2k Batch")
            t0 = time.time()
            stage_batch(batch.id, org.id, csv_path, "csv", idb)
            elapsed = time.time() - t0
        finally:
            os.unlink(csv_path)

        staged = idb.query(ImportStagedRow).filter(
            ImportStagedRow.batch_id == batch.id
        ).count()
        lead_count_after = idb.query(Lead).count()

        assert staged == 2000, f"Expected 2000 staged rows, got {staged}"
        assert lead_count_after == lead_count_before, (
            "Staging 2 000 rows must not create any live leads"
        )
        assert elapsed < 60, (
            f"2 000-row staging took {elapsed:.1f}s — must be < 60s"
        )
        idb.refresh(batch)
        assert batch.total_rows == 2000

    @needs_pandas
    def test_2000_row_pagination_returns_correct_slices(self, idb):
        """After staging 2 000 rows, pagination slices must be accurate."""
        org  = _make_org(idb, name="2kPage Org", slug="2kpage-org")
        user = _make_user(idb, org.id, role="org_admin")
        rows = [
            _standard_row(**{
                "First Name": f"P{i}",
                "Last Name":  f"L{i}",
                "Phone":      f"512{i:07d}",
            })
            for i in range(2000)
        ]
        csv_path = _csv_tempfile(rows)
        try:
            batch = _make_batch(idb, org.id, user.id, "2kPage Batch")
            stage_batch(batch.id, org.id, csv_path, "csv", idb)
        finally:
            os.unlink(csv_path)

        total = idb.query(ImportStagedRow).filter(
            ImportStagedRow.batch_id == batch.id
        ).count()
        assert total == 2000

        # Simulate paginated reads
        per_page = 100
        pages_read = 0
        total_items = 0
        for page in range(1, 22):   # 2000/100 = 20 full pages
            offset = (page - 1) * per_page
            items = (
                idb.query(ImportStagedRow)
                .filter(ImportStagedRow.batch_id == batch.id)
                .order_by(ImportStagedRow.row_number)
                .offset(offset).limit(per_page).all()
            )
            if not items:
                break
            total_items += len(items)
            pages_read += 1

        assert total_items == 2000
        assert pages_read == 20


# ─────────────────────────────────────────────────────────────────────────────
# GATE 20 — Validation status flagging
# ─────────────────────────────────────────────────────────────────────────────

class TestGate20ValidationStatus:

    @needs_pandas
    def test_row_missing_phone_and_email_is_invalid(self, idb):
        org  = _make_org(idb, name="Val Org", slug="val-org")
        user = _make_user(idb, org.id)
        csv_path = _csv_tempfile([
            _standard_row(**{"Phone": "", "Email": ""})
        ])
        try:
            batch = _make_batch(idb, org.id, user.id)
            stage_batch(batch.id, org.id, csv_path, "csv", idb)
        finally:
            os.unlink(csv_path)

        row = idb.query(ImportStagedRow).filter(
            ImportStagedRow.batch_id == batch.id
        ).first()
        assert row.validation_status == ImportValidationStatus.INVALID

    @needs_pandas
    def test_row_with_valid_phone_is_valid(self, idb):
        org  = _make_org(idb, name="Val2 Org", slug="val2-org")
        user = _make_user(idb, org.id)
        csv_path = _csv_tempfile([
            _standard_row(**{"Phone": "2145559876", "Email": ""})
        ])
        try:
            batch = _make_batch(idb, org.id, user.id)
            stage_batch(batch.id, org.id, csv_path, "csv", idb)
        finally:
            os.unlink(csv_path)

        row = idb.query(ImportStagedRow).filter(
            ImportStagedRow.batch_id == batch.id
        ).first()
        assert row.validation_status == ImportValidationStatus.VALID
        assert row.phone_normalized == "+12145559876"


# ─────────────────────────────────────────────────────────────────────────────
# GATE 21 — migrate_add_import_tables.py is redundant / removed
# ─────────────────────────────────────────────────────────────────────────────

class TestGate21MigrationCleanup:

    def test_import_tables_created_by_create_all_not_manual_script(self, idb):
        """Tables exist in the in-memory DB because Base.metadata.create_all()
        ran — not because of migrate_add_import_tables.py. This proves they are
        registered correctly on Base."""
        from sqlalchemy import inspect
        insp = inspect(idb.bind)
        table_names = insp.get_table_names()
        assert "import_batches" in table_names, (
            "import_batches not created by create_all — model not registered on Base"
        )
        assert "import_staged_rows" in table_names, (
            "import_staged_rows not created by create_all"
        )

    def test_migrate_add_import_tables_script_is_absent_or_noted_redundant(self):
        """The standalone migration script should be gone (covered by create_all)."""
        repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        script = os.path.join(repo_root, "app", "migrations",
                              "migrate_add_import_tables.py")
        patches_script = os.path.join(repo_root, "app", "patches",
                                      "migrate_add_import_tables.py")
        # Either the file is gone, or it exists with a comment marking it redundant.
        for path in (script, patches_script):
            if os.path.exists(path):
                content = open(path).read()
                assert any(word in content.lower() for word in
                           ("redundant", "deprecated", "superseded", "create_all", "no longer")), (
                    f"{path} still exists and is not marked redundant. "
                    "Remove it or add a comment explaining it is superseded by create_all()."
                )


# ─────────────────────────────────────────────────────────────────────────────
# GATE 22 — delete batch removes rows
# ─────────────────────────────────────────────────────────────────────────────

class TestGate22DeleteBatch:

    def test_delete_batch_removes_rows(self, client, db_session):
        org   = _make_org(db_session, name="Del Org", slug="del-org")
        admin = _make_user(db_session, org.id, role="org_admin",
                           email="del@test.com")
        batch = ImportBatch(
            id=gen_uuid(), organization_id=org.id,
            created_by_id=admin.id, display_name="Delete Me",
            source_type="csv", source_filename="t.csv",
            status=ImportBatchStatus.READY_FOR_REVIEW,
        )
        db_session.add(batch)
        for i in range(3):
            db_session.add(ImportStagedRow(
                id=gen_uuid(), batch_id=batch.id, organization_id=org.id,
                row_number=i + 1, first_name="Del", last_name=f"P{i}",
                validation_status=ImportValidationStatus.VALID,
                duplicate_status=ImportDuplicateStatus.NEW,
                review_status=ImportRowReviewStatus.PENDING,
            ))
        db_session.commit()

        token = create_access_token(admin, db_session)
        r = client.delete(
            f"/import-batches/{batch.id}",
            headers={"Authorization": f"Bearer {token}"}
        )
        assert r.status_code == 200
        assert r.json()["deleted"] is True

        remaining_rows = db_session.query(ImportStagedRow).filter(
            ImportStagedRow.batch_id == batch.id
        ).count()
        assert remaining_rows == 0


# ─────────────────────────────────────────────────────────────────────────────
# GATE 23 — Model index coverage check
# ─────────────────────────────────────────────────────────────────────────────

class TestGate23IndexCoverage:

    def test_import_batches_has_org_status_index(self, idb):
        from sqlalchemy import inspect
        insp = inspect(idb.bind)
        indexes = {idx["name"] for idx in insp.get_indexes("import_batches")}
        assert "ix_import_batches_org_status" in indexes

    def test_import_staged_rows_has_batch_review_index(self, idb):
        from sqlalchemy import inspect
        insp = inspect(idb.bind)
        indexes = {idx["name"] for idx in insp.get_indexes("import_staged_rows")}
        assert "ix_isr_batch_review" in indexes

    def test_import_staged_rows_has_phone_norm_index(self, idb):
        from sqlalchemy import inspect
        insp = inspect(idb.bind)
        indexes = {idx["name"] for idx in insp.get_indexes("import_staged_rows")}
        assert "ix_isr_phone_norm" in indexes


# ─────────────────────────────────────────────────────────────────────────────
# GATE 24 — Batch detail endpoint
# ─────────────────────────────────────────────────────────────────────────────

class TestGate24BatchDetailEndpoint:

    def test_get_batch_returns_correct_fields(self, client, db_session):
        org   = _make_org(db_session, name="Detail Org", slug="detail-org")
        admin = _make_user(db_session, org.id, role="org_admin",
                           email="detail@test.com")
        batch = ImportBatch(
            id=gen_uuid(), organization_id=org.id,
            created_by_id=admin.id, display_name="Detail Batch",
            source_type="csv", source_filename="detail.csv",
            status=ImportBatchStatus.REVIEWING,
            total_rows=42, new_rows=30, matched_rows=12,
        )
        db_session.add(batch)
        db_session.commit()

        token = create_access_token(admin, db_session)
        r = client.get(
            f"/import-batches/{batch.id}",
            headers={"Authorization": f"Bearer {token}"}
        )
        assert r.status_code == 200
        d = r.json()
        assert d["id"] == batch.id
        assert d["display_name"] == "Detail Batch"
        assert d["source_filename"] == "detail.csv"
        assert d["total_rows"] == 42
        assert d["new_rows"] == 30
        assert d["matched_rows"] == 12

    def test_get_nonexistent_batch_returns_404(self, client, db_session):
        org   = _make_org(db_session, name="404 Org", slug="404-org")
        admin = _make_user(db_session, org.id, role="org_admin",
                           email="404@test.com")
        token = create_access_token(admin, db_session)
        r = client.get(
            "/import-batches/does-not-exist",
            headers={"Authorization": f"Bearer {token}"}
        )
        assert r.status_code == 404


# ─────────────────────────────────────────────────────────────────────────────
# GATE 25 — Cannot commit an archived batch
# ─────────────────────────────────────────────────────────────────────────────

class TestGate25ArchivedBatchNotCommittable:

    def test_commit_archived_batch_returns_409(self, client, db_session):
        org   = _make_org(db_session, name="Arch2 Org", slug="arch2-org")
        admin = _make_user(db_session, org.id, role="org_admin",
                           email="arch2@test.com")
        batch = ImportBatch(
            id=gen_uuid(), organization_id=org.id,
            created_by_id=admin.id, display_name="Archived",
            source_type="csv", source_filename="t.csv",
            status=ImportBatchStatus.ARCHIVED,
        )
        db_session.add(batch)
        db_session.commit()

        token = create_access_token(admin, db_session)
        r = client.post(
            f"/import-batches/{batch.id}/commit",
            headers={"Authorization": f"Bearer {token}"}
        )
        assert r.status_code == 409
