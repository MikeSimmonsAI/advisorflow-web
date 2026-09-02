"""
import_permissions.py
─────────────────────
Thin wrappers over capabilities.require_feature_capability for the
Lead Import Intelligence routes.

These are dependency factories — drop them into Depends() on any route
that needs them. They are NOT a second authorization system: they call
require_feature_capability, which uses the same CAPABILITIES registry
and the same UserCapabilityGrant table as require_capability.

Resolution for every dep below:
    god_admin    -> ALLOW
    super_admin  -> ALLOW
    org_admin    -> ALLOW (by role, no explicit grant needed)
    others       -> must hold an explicit UserCapabilityGrant for the key
"""

from app.services.capabilities import require_feature_capability

# Who may upload a file or kick off a Google Contacts pull.
require_import_leads = require_feature_capability("import_leads")
require_import_stage = require_import_leads  # alias for leads_router.py

# Alias used by leads_router.py for the stage/upload gate.
require_import_stage = require_import_leads

# Who may view staged rows and set accept / merge / reject.
require_import_review = require_feature_capability("import_review")

# Who may commit a reviewed batch to live leads.
require_import_commit = require_feature_capability("import_commit")

# Who may archive or otherwise manage import batches.
require_import_admin = require_feature_capability("import_admin")
