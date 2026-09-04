"""
Model registry - the one place every SQLAlchemy model module is imported.

WHY THIS MODULE EXISTS
----------------------
`Base.metadata.create_all()` only creates tables whose module has been
imported. Declaring a model is not enough; something has to import the file.
That list of side-effect imports lived in app/main.py and nowhere else, so
only a process that imported main.py had a complete Base.metadata.

tests/conftest.py does not import main.py - it imports app.models.models and
calls create_all() directly. Base.metadata therefore held only the tables
declared in models.py, and every model module main.py pulls in was missing.
The visible symptom was a NoReferencedTableError during fixture setup:
`proposals` (declared in models.py) has foreign keys to `opportunities` and
`brand_sales_orgs`, both declared in sales_models.py, which nothing in the
test process had imported.

So this is not a new architecture. It is the registry main.py already was,
moved into app/models/ where anything that needs a complete metadata can
import it, with the original comments kept verbatim. main.py imports this
module instead of keeping its own copy, so there is exactly ONE list. A
second copy is how the old one drifted: the comments below record two
occasions when a merge silently deleted a line here and the only evidence
was a table that never got created.

ADDING A MODEL MODULE: add the import here. Nowhere else.
"""

from app.models.models import Base  # noqa: F401  (re-exported for callers)

# Sales Workspace models register themselves on the SAME Base. This import is
# REQUIRED and is not decorative: Base.metadata.create_all() below only creates
# tables whose module has been imported, so dropping this line makes every
# sales table silently never appear. See claude/SALES_WORKSPACE_ARCHITECTURE.md.
import app.models.sales_models  # noqa: F401  (imported for side effects)
# Scheduling models register on the SAME Base for the same reason. Dropping this
# line makes availability_profiles / sales_appointments / participants silently
# never appear, and the sales workspace loses scheduling with no error.
import app.models.scheduling_models  # noqa: F401  (imported for side effects)
# Calendar sync models — same Base, same reason. Without this import the
# calendar_connections / external_busy_blocks / confirmation-token tables are
# never created and external sync silently has nowhere to write.
import app.models.calendar_models  # noqa: F401  (imported for side effects)
# Video meeting models (Checkpoint 4) — same Base, same reason. Without this
# import appointment_meetings / meeting_provider_configs are never created and
# Zoom provisioning silently has nowhere to write.
import app.models.meeting_models  # noqa: F401  (imported for side effects)
# Integration credentials (Retell bridges) and demo scenario state — same Base,
# same reason. The demo tables are created everywhere, including production,
# and that is deliberate: the demo service runs the SAME image rather than a
# fork that could drift. Nothing in production ever writes to them, and every
# route that reads them 404s outside APP_ENV=demo.
import app.models.integration_models  # noqa: F401  (imported for side effects)
import app.models.demo_models  # noqa: F401  (imported for side effects)
# Implementation / provisioning models (Checkpoint 6) - same Base, same reason.
# Without this import implementations / implementation_milestones /
# customer_activations are never created and Won -> Customer provisioning has
# nowhere to write. These tables plus Opportunity.customer_organization_id are
# the ONLY places the brand-sales tree and the customer-tenant tree meet.
import app.models.implementation_models  # noqa: F401  (imported for side effects)
# Staff/brand-sales access activation (staff_activations) - same Base, same
# reason. A control-plane identity has organization_id = NULL, so it cannot
# use the customer activation table, whose organization_id is NOT NULL.
import app.models.staff_models  # noqa: F401  (imported for side effects)
# Customer locations (locations / user_locations) - same Base, same reason.
# Without this import a customer's physical sites are never created and
# booking has nothing to route to. `create_all` only builds tables whose
# module has been imported, and a missing table here fails silently.
import app.models.location_models  # noqa: F401  (imported for side effects)
# Cleanup receipts (cleanup_executions) - same Base, same reason. Without this
# import a deletion plan has nowhere durable to live, and the manifest exists
# only in whatever browser tab asked for it.
import app.models.cleanup_models  # noqa: F401  (imported for side effects)
import app.models.demo_site_models  # noqa: F401  (imported for side effects)
# Lead Import Intelligence staging tables (import_batches / import_staged_rows).
# Same Base, same reason. Without this import those tables are never created and
# every upload silently has nowhere to write.
import app.models.import_models  # noqa: F401  (imported for side effects)
# Staged historical evidence (source_records / source_opportunities). Separate
# module from import_models on purpose - a merge on that filename already
# deleted these once, and Base.metadata.create_all only sees what is imported.
import app.models.source_records  # noqa: F401  (imported for side effects)
# Organization-defined qualification rules (qualification_rules). RESTORED: the
# feature/lead-import-intelligence merge dropped this line, and without it the
# table is never created - which the engine reads as "this organization has
# defined no rules" rather than as an error. A quiet wrong answer instead of a
# loud one, which is the worst shape a missing migration can take.
import app.models.qualification_models  # noqa: F401  (imported for side effects)
# Billing mirror (invoices / invoice_line_items / payments /
# stripe_webhook_events). Same Base, same reason. Without this import the
# webhook has nowhere to record that it has already seen an event, and the
# idempotency guarantee - the thing that stops a redelivered refund being
# applied twice - silently does not exist.
import app.models.billing_models  # noqa: F401  (imported for side effects)
# P1 billing entity layer - merchant_entities. Same Base, same reason: the
# table is only created by create_all() if this module has been imported.
import app.models.billing_entity_models  # noqa: F401  (imported for side effects)
# P2 executable billing relationship - billing_agreements. New table, so
# create_all() builds it; nothing goes in auto_migrate for it.
import app.models.billing_agreement_models  # noqa: F401  (imported for side effects)
