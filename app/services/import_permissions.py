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

# ONE CAPABILITY KEY PER DEP.
#
# Each dep below names exactly one registered capability key, and every key it
# names is the canonical one. The old spellings (import_leads, import_admin)
# survive here only as PYTHON NAMES bound to the same dependency object, for
# the routers that already import them - they are not separate keys and they
# do not reach the grant table. Binding by assignment rather than by a second
# require_feature_capability() call is deliberate: `is` identity is what makes
# "these two names are the same permission" a fact rather than a comment.

# Who may upload a file or kick off a Google Contacts pull.
require_import_stage = require_feature_capability("lead_import_stage")

# Who may view staged rows and set accept / merge / reject.
require_import_review = require_feature_capability("lead_import_review")

# Who may commit a reviewed batch to live leads.
require_import_commit = require_feature_capability("lead_import_commit")

# Who may archive or otherwise manage import batches.
require_import_manage = require_feature_capability("lead_import_manage")

# Legacy import names, same objects.
require_import_leads = require_import_stage
require_import_admin = require_import_manage
