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
# Launch Engine intake - the customer's ANSWERS, hanging off the
# implementation above. NOTE: this file contains the whole module TWICE (a
# merge artifact); this import is added to BOTH blocks on purpose, because an
# import added to only one copy looks correct in a diff and half-works.
import app.models.launch_intake_models  # noqa: F401  (imported for side effects)
# Launch Engine DELIVERY (implementation_integrations / _checks / _training /
# _blockers / _approvals / launch_templates) - same Base, same reason, and the
# consequence of dropping it is the worst on this list: with no tables, the
# go-live gate reads every requirement as satisfied, because an empty checklist
# is indistinguishable from a finished one - and a customer could be marked
# Live with nothing verified. Added to BOTH blocks below, per the note above.
import app.models.launch_delivery_models  # noqa: F401  (imported for side effects)
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

# Per-deal pricing authority (pricing_policies). Same Base, same reason. Without
# this import the table is never created, `resolve_policy` finds no rows, and
# the engine falls back to "a rep may discount nothing" - which is safe but is
# also silently NOT the policy anybody configured.
import app.models.pricing_policy_models  # noqa: F401  (imported for side effects)
# Sales compensation (compensation_plans / _rules / _package_caps /
# compensation_entries / stage_probabilities). Same Base, same reason. A missing
# compensation_entries table would make `earn()` raise on a real collected
# payment, and a missing plans table reads as "no plan configured" - the same
# quiet wrong answer the qualification_rules line above records.
import app.models.compensation_models  # noqa: F401  (imported for side effects)
# Customer lifecycle history (customer_lifecycle_events). Same Base, same
# reason. Without this import the table is never created and a cancellation has
# nowhere to record WHY a customer left — the current-state columns on
# `organizations` would still write, so the loss would be silent and would look
# like a customer who cancelled for no reason.
import app.models.customer_lifecycle_models  # noqa: F401  (imported for side effects)
# Customer SaaS billing (brand_billing_plans / brand_billing_configs /
# billing_events / billing_invoices / billing_payments). Same Base, same
# reason. Two of these carry consequences worse than a missing screen if the
# import is ever dropped: billing_events is the webhook idempotency ledger, so
# without it every Stripe retry re-runs its writes and one payment can earn
# commission twice; and billing_payments is the only local record that money
# moved, so a missing table would make revenue reporting quietly read zero
# rather than fail. Do not remove this line.
import app.models.billing_models  # noqa: F401  (imported for side effects)
# The brand CATALOGUE - recurring add-ons and one-time products/services, the
# things a brand sells that are not the base subscription. Same Base, same
# reason. Without this import brand_catalog_items is never created, and every
# catalogue listing returns empty - which is indistinguishable from a brand
# that has configured nothing, so the failure looks like normal emptiness.
# Added to BOTH blocks in this file, per the merge-artifact note above.
import app.models.catalog_models  # noqa: F401  (imported for side effects)
# What customers have BOUGHT from that catalogue - catalog_purchases. Same
# Base, same reason, and the consequence of dropping it is money-shaped: the
# add-on and one-time purchase rows are the only local record that a customer
# owes or has paid for something beyond their plan, and a missing table would
# make every purchase listing read empty rather than fail.
import app.models.purchase_models  # noqa: F401  (imported for side effects)
# Executive Workspace deal-room tables (exec_workspace_items / _files / _versions).
# Same Base, same reason. Without this import those three tables are never created
# and every workspace endpoint fails with a missing-table error on first use.
import app.models.exec_workspace_models  # noqa: F401  (imported for side effects)
# Support Intelligence (support_tickets / _messages / _events / _attachments,
# support_conversations / _turns, support_diagnostic_runs, support_fix_runs,
# support_fix_policies, support_issue_signatures, support_incidents / _links,
# support_entitlement_configs, support_brand_settings, support_service_offerings,
# support_assistance_entries, support_knowledge_articles / _candidates,
# support_daily_briefs). Same Base, same reason, and this module is the ONLY
# owner of those tables - none of them appear in auto_migrate's
# TABLES_TO_CREATE, because two owners is how crm_contacts drifted. Dropping
# this line makes every support table silently never appear, which the support
# product would report as "no tickets" rather than as an error.
import app.models.support_models  # noqa: F401  (imported for side effects)
# OAuth authorization transactions (oauth_auth_transactions). Same Base, same
# reason, and the consequence of dropping this line is a security one rather
# than a cosmetic one: without the table, `oauth_state_service.issue_state`
# raises on the first connect attempt and the Google/Microsoft flows stop
# working entirely. That is loud, which is the right failure — the shape to
# NEVER accept is anyone "fixing" it by reading the user id out of the raw
# `state` again. Added to BOTH blocks in this file, per the merge-artifact
# note above.
import app.models.oauth_models  # noqa: F401  (imported for side effects)
# AI Workforce (ai_employee_templates / ai_brand_offerings / ai_employees /
# ai_employee_authorities / ai_work_items / ai_work_item_events /
# ai_employee_runs / ai_tool_executions / ai_eligibility_results /
# ai_employee_memory / ai_handoffs / ai_performance_entries /
# ai_supervisor_events / ai_workforce_activations /
# ai_shadow_recommendations / ai_evaluation_runs / ai_evaluation_results).
# Same Base, same reason as every line above, and this module is the ONLY
# owner of those tables - none of them appear in auto_migrate's
# TABLES_TO_CREATE, because two owners is how crm_contacts drifted.
#
# Dropping this line has a specific and bad consequence: `ai_workforce_
# activations` is where the platform's OFF state is stored, and
# activation.resolve() treats a missing row as OFF. With no table at all the
# read raises instead, so every workforce route fails loudly rather than
# silently running - which is the correct direction, but it is an outage on a
# screen rather than the quiet nothing the other modules degrade to. Added to
# BOTH blocks in this file, per the merge-artifact note above.
import app.models.workforce_models  # noqa: F401  (imported for side effects)
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
# Launch Engine intake - the customer's ANSWERS, hanging off the
# implementation above. NOTE: this file contains the whole module TWICE (a
# merge artifact); this import is added to BOTH blocks on purpose, because an
# import added to only one copy looks correct in a diff and half-works.
import app.models.launch_intake_models  # noqa: F401  (imported for side effects)
# Launch Engine DELIVERY (implementation_integrations / _checks / _training /
# _blockers / _approvals / launch_templates) - same Base, same reason, and the
# consequence of dropping it is the worst on this list: with no tables, the
# go-live gate reads every requirement as satisfied, because an empty checklist
# is indistinguishable from a finished one - and a customer could be marked
# Live with nothing verified. Added to BOTH blocks below, per the note above.
import app.models.launch_delivery_models  # noqa: F401  (imported for side effects)
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

# Per-deal pricing authority (pricing_policies). Same Base, same reason. Without
# this import the table is never created, `resolve_policy` finds no rows, and
# the engine falls back to "a rep may discount nothing" - which is safe but is
# also silently NOT the policy anybody configured.
import app.models.pricing_policy_models  # noqa: F401  (imported for side effects)
# Sales compensation (compensation_plans / _rules / _package_caps /
# compensation_entries / stage_probabilities). Same Base, same reason. A missing
# compensation_entries table would make `earn()` raise on a real collected
# payment, and a missing plans table reads as "no plan configured" - the same
# quiet wrong answer the qualification_rules line above records.
import app.models.compensation_models  # noqa: F401  (imported for side effects)
# Customer lifecycle history (customer_lifecycle_events). Same Base, same
# reason. Without this import the table is never created and a cancellation has
# nowhere to record WHY a customer left — the current-state columns on
# `organizations` would still write, so the loss would be silent and would look
# like a customer who cancelled for no reason.
import app.models.customer_lifecycle_models  # noqa: F401  (imported for side effects)
# Customer SaaS billing (brand_billing_plans / brand_billing_configs /
# billing_events / billing_invoices / billing_payments). Same Base, same
# reason. Two of these carry consequences worse than a missing screen if the
# import is ever dropped: billing_events is the webhook idempotency ledger, so
# without it every Stripe retry re-runs its writes and one payment can earn
# commission twice; and billing_payments is the only local record that money
# moved, so a missing table would make revenue reporting quietly read zero
# rather than fail. Do not remove this line.
import app.models.billing_models  # noqa: F401  (imported for side effects)
# The brand CATALOGUE - recurring add-ons and one-time products/services, the
# things a brand sells that are not the base subscription. Same Base, same
# reason. Without this import brand_catalog_items is never created, and every
# catalogue listing returns empty - which is indistinguishable from a brand
# that has configured nothing, so the failure looks like normal emptiness.
# Added to BOTH blocks in this file, per the merge-artifact note above.
import app.models.catalog_models  # noqa: F401  (imported for side effects)
# What customers have BOUGHT from that catalogue - catalog_purchases. Same
# Base, same reason, and the consequence of dropping it is money-shaped: the
# add-on and one-time purchase rows are the only local record that a customer
# owes or has paid for something beyond their plan, and a missing table would
# make every purchase listing read empty rather than fail.
import app.models.purchase_models  # noqa: F401  (imported for side effects)
# Per-device authentication sessions (user_sessions). Same Base, same reason,
# and the consequence of dropping this line is the sharpest on the list: with
# no table, `session_service.find_by_jti` raises on EVERY authenticated
# request, so the entire API 500s rather than degrading. It is also what makes
# force-logout, password-change revocation and per-device sign-out real — the
# `users.session_token` column alone can no longer end more than one session.
import app.models.session_models  # noqa: F401  (imported for side effects)
# Push device registration (device_push_tokens). Same Base, same reason. A
# missing table here is quieter than the one above — registration fails and
# notifications simply never arrive — which is precisely why it is worth
# naming: silence is what a broken push system looks like from the outside.
import app.models.device_models  # noqa: F401  (imported for side effects)
# Background job records (background_jobs). Same Base, same reason. Without this
# import the table is never created and job persistence silently has nowhere to
# write — jobs appear to enqueue but leave no durable record.
import app.models.job_models  # noqa: F401  (imported for side effects)

# ── Demo Suite (demo_environments / demo_sessions / demo_action_events) ─────
# Same Base, same reason as every line above. This is NOT the APP_ENV=demo box's
# `demo_models` — see app/models/demo_suite_models.py for why the two exist side
# by side. Without this import the three tables are never created, and the
# failure is the quiet kind: God Mode would offer to build a brand's demo
# environment and the build would fail on first write with a missing table.
import app.models.demo_suite_models  # noqa: F401  (imported for side effects)
# Training and readiness (training_assignments / training_step_progress). Same
# Base, same reason. A missing table here reads as "nobody has been assigned
# any training", which is indistinguishable from the truth on a fresh install
# and is exactly the quiet wrong answer the qualification_rules comment above
# records. Do not remove this line.
import app.models.training_models  # noqa: F401  (imported for side effects)
# Support Intelligence — the SECOND copy of this line, and it has to be here.
# This file contains the whole module twice (a merge artifact recorded in the
# launch_intake_models comment above); an import added to only one copy looks
# correct in a diff and half-works. Same tables, same reason as the first copy.
import app.models.support_models  # noqa: F401  (imported for side effects)
# OAuth authorization transactions — the SECOND copy of this line, same tables
# and same reason as the first. See the note on that copy for what breaks if it
# is dropped.
import app.models.oauth_models  # noqa: F401  (imported for side effects)
# AI Workforce — the SECOND copy of this line, for the same reason as the
# support line above it. Same tables, same ownership, same consequence if it
# is ever dropped from one block and not the other.
import app.models.workforce_models  # noqa: F401  (imported for side effects)
