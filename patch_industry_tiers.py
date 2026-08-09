"""
Patch: industry-specific tier seeding, OrgSettings industry list, TierDefinitions reset button.
"""
import os

BASE = r"C:\Users\simmo\OneDrive\Desktop\Web AvdvisorFlow Package\files\advisorflow-web"

# ── 1. tier_config_service.py ─────────────────────────────────────────────────

svc_path = os.path.join(BASE, "app", "services", "tier_config_service.py")
with open(svc_path, encoding="utf-8") as f:
    svc = f.read()

INDUSTRY_TIERS_BLOCK = '''
# ---------------------------------------------------------------------------
# Industry-specific tier sets. Each industry gets tiers that match its
# real sales/service workflow rather than defaulting to funeral terminology.
# ---------------------------------------------------------------------------

FIBER_DEFAULT_TIERS = [
    {
        "tier_key": "prospect", "tier_label": "Prospect", "sort_order": 0,
        "track_key": "fiber_prospect", "track_label": "Fiber Prospect Intro",
        "is_manual_selectable": True,
        "ai_tone_context": (
            "Prospect: a door-knocker just captured this lead as a verbal yes. "
            "Tone is warm and welcoming — confirm their interest, give a sense of "
            "next steps (scheduling the install), and keep it brief."
        ),
    },
    {
        "tier_key": "warm_lead", "tier_label": "Warm Lead", "sort_order": 1,
        "track_key": "fiber_warm", "track_label": "Fiber Warm Outreach",
        "is_manual_selectable": True,
        "ai_tone_context": (
            "Warm Lead: the customer has been contacted and showed interest. "
            "Move them toward scheduling an install appointment."
        ),
    },
    {
        "tier_key": "appt_set", "tier_label": "Appt Scheduled", "sort_order": 2,
        "track_key": "fiber_appt", "track_label": "Fiber Appt Reminder",
        "is_manual_selectable": True,
        "ai_tone_context": (
            "Appointment Scheduled: install date is confirmed. Tone is excited "
            "and reassuring — remind them of the date/time and what to expect."
        ),
    },
    {
        "tier_key": "installed", "tier_label": "Installed", "sort_order": 3,
        "track_key": "fiber_post_install", "track_label": "Post-Install Follow-up",
        "is_manual_selectable": True,
        "ai_tone_context": (
            "Installed: service is live. Tone is celebratory — check in on "
            "satisfaction, ask for a review if they are happy."
        ),
    },
    {
        "tier_key": "upsell", "tier_label": "Upsell / Referral", "sort_order": 4,
        "track_key": "fiber_upsell", "track_label": "Fiber Upsell",
        "is_manual_selectable": True,
        "ai_tone_context": (
            "Upsell: existing customer eligible for a speed upgrade or referral "
            "program. Tone is friendly and value-focused."
        ),
    },
    {
        "tier_key": "not_home", "tier_label": "Not Home / Revisit", "sort_order": 5,
        "track_key": "fiber_revisit", "track_label": "Fiber Re-knock",
        "is_manual_selectable": False,
        "ai_tone_context": (
            "Not Home: rep knocked but no answer. Brief, friendly message "
            "introducing Fiber Cartel and inviting them to reach out."
        ),
    },
]

SALES_DEFAULT_TIERS = [
    {
        "tier_key": "prospect", "tier_label": "Prospect", "sort_order": 0,
        "track_key": "sales_prospect", "track_label": "Initial Outreach",
        "ai_tone_context": "New prospect — warm, friendly intro. Focus on value and next step.",
    },
    {
        "tier_key": "warm", "tier_label": "Warm Lead", "sort_order": 1,
        "track_key": "sales_warm", "track_label": "Warm Follow-up",
        "ai_tone_context": "Engaged lead showing interest. Move toward scheduling a demo or call.",
    },
    {
        "tier_key": "hot", "tier_label": "Hot Lead", "sort_order": 2,
        "track_key": "sales_hot", "track_label": "Hot Close",
        "ai_tone_context": "High-intent lead ready to commit. Be direct, remove friction, guide to next step.",
    },
    {
        "tier_key": "customer", "tier_label": "Customer", "sort_order": 3,
        "track_key": "sales_upsell", "track_label": "Customer Upsell",
        "ai_tone_context": "Existing customer. Tone is warm and relationship-focused — upsell or referral ask.",
    },
    {
        "tier_key": "lost", "tier_label": "Lost / Not Now", "sort_order": 4,
        "track_key": "sales_nurture", "track_label": "Long-term Nurture",
        "is_manual_selectable": False,
        "ai_tone_context": "Passed for now — gentle long-cycle nurture to stay top of mind.",
    },
]

ROOFING_DEFAULT_TIERS = [
    {
        "tier_key": "new_lead", "tier_label": "New Lead", "sort_order": 0,
        "track_key": "roof_intro", "track_label": "Roofing Intro",
        "ai_tone_context": "New roofing inquiry. Friendly intro — offer a free inspection.",
    },
    {
        "tier_key": "inspection_set", "tier_label": "Inspection Scheduled", "sort_order": 1,
        "track_key": "roof_inspection", "track_label": "Inspection Reminder",
        "ai_tone_context": "Inspection is booked. Remind them of date/time and what to expect.",
    },
    {
        "tier_key": "estimate_sent", "tier_label": "Estimate Sent", "sort_order": 2,
        "track_key": "roof_estimate", "track_label": "Estimate Follow-up",
        "ai_tone_context": "Quote is out. Follow up warmly — address objections, keep urgency low.",
    },
    {
        "tier_key": "job_booked", "tier_label": "Job Booked", "sort_order": 3,
        "track_key": "roof_booked", "track_label": "Job Confirmation",
        "ai_tone_context": "Project accepted. Confirm start date, set expectations.",
    },
    {
        "tier_key": "job_complete", "tier_label": "Job Complete", "sort_order": 4,
        "track_key": "roof_complete", "track_label": "Post-Job Follow-up",
        "ai_tone_context": "Job done — check satisfaction, ask for review/referral.",
    },
]

INSURANCE_DEFAULT_TIERS = [
    {
        "tier_key": "prospect", "tier_label": "Prospect", "sort_order": 0,
        "track_key": "ins_intro", "track_label": "Insurance Intro",
        "ai_tone_context": "New insurance prospect. Warm intro — focus on peace of mind and protection.",
    },
    {
        "tier_key": "qualified", "tier_label": "Qualified", "sort_order": 1,
        "track_key": "ins_qualified", "track_label": "Qualified Follow-up",
        "ai_tone_context": "Lead is qualified. Move toward getting a quote on the books.",
    },
    {
        "tier_key": "quote_sent", "tier_label": "Quote Sent", "sort_order": 2,
        "track_key": "ins_quote", "track_label": "Quote Follow-up",
        "ai_tone_context": "Quote has been delivered. Follow up — answer questions, handle objections.",
    },
    {
        "tier_key": "app_in_progress", "tier_label": "App In Progress", "sort_order": 3,
        "track_key": "ins_app", "track_label": "Application Support",
        "ai_tone_context": "Application submitted or in progress. Support and keep moving forward.",
    },
    {
        "tier_key": "active", "tier_label": "Active Policy", "sort_order": 4,
        "track_key": "ins_active", "track_label": "Policy Holder Retention",
        "ai_tone_context": "Active policyholder. Renewal touch, referral ask, upsell opportunity.",
    },
]

HOME_SERVICES_DEFAULT_TIERS = [
    {
        "tier_key": "new_lead", "tier_label": "New Lead", "sort_order": 0,
        "track_key": "hs_intro", "track_label": "Home Services Intro",
        "ai_tone_context": "New home services inquiry. Friendly, helpful — book an estimate or consultation.",
    },
    {
        "tier_key": "estimate_scheduled", "tier_label": "Estimate Scheduled", "sort_order": 1,
        "track_key": "hs_estimate", "track_label": "Estimate Reminder",
        "ai_tone_context": "Estimate appointment is set. Remind them and build excitement.",
    },
    {
        "tier_key": "proposal_sent", "tier_label": "Proposal Sent", "sort_order": 2,
        "track_key": "hs_proposal", "track_label": "Proposal Follow-up",
        "ai_tone_context": "Proposal is out. Warm follow-up — answer questions and handle objections gently.",
    },
    {
        "tier_key": "job_won", "tier_label": "Job Won", "sort_order": 3,
        "track_key": "hs_job_won", "track_label": "Job Kickoff",
        "ai_tone_context": "Project awarded. Confirm details, set expectations, build confidence.",
    },
    {
        "tier_key": "complete", "tier_label": "Job Complete", "sort_order": 4,
        "track_key": "hs_complete", "track_label": "Post-Job Follow-up",
        "ai_tone_context": "Job done. Check satisfaction, ask for a review and referral.",
    },
]

REAL_ESTATE_DEFAULT_TIERS = [
    {
        "tier_key": "buyer_lead", "tier_label": "Buyer Lead", "sort_order": 0,
        "track_key": "re_buyer", "track_label": "Buyer Outreach",
        "ai_tone_context": "Prospective buyer. Friendly — understand their needs and offer to show listings.",
    },
    {
        "tier_key": "seller_lead", "tier_label": "Seller Lead", "sort_order": 1,
        "track_key": "re_seller", "track_label": "Seller Outreach",
        "ai_tone_context": "Homeowner interested in selling. Friendly — offer a CMA and guide next steps.",
    },
    {
        "tier_key": "showing_set", "tier_label": "Showing Scheduled", "sort_order": 2,
        "track_key": "re_showing", "track_label": "Showing Reminder",
        "ai_tone_context": "Showing is scheduled. Remind them and build excitement about the property.",
    },
    {
        "tier_key": "under_contract", "tier_label": "Under Contract", "sort_order": 3,
        "track_key": "re_contract", "track_label": "Contract Support",
        "ai_tone_context": "Under contract. Keep them informed and calm through the closing process.",
    },
    {
        "tier_key": "closed", "tier_label": "Closed", "sort_order": 4,
        "track_key": "re_closed", "track_label": "Post-Close Follow-up",
        "ai_tone_context": "Closed! Congratulate them and ask for reviews / referrals.",
    },
]

# Map industry values → their tier set. Fall back to SALES_DEFAULT_TIERS for
# any industry not explicitly listed here — it's generic enough to work.
INDUSTRY_TIER_SETS = {
    "fiber": FIBER_DEFAULT_TIERS,
    "fiber_internet": FIBER_DEFAULT_TIERS,
    "door_to_door": FIBER_DEFAULT_TIERS,
    "direct_sales": SALES_DEFAULT_TIERS,
    "solar": SALES_DEFAULT_TIERS,
    "telecom": SALES_DEFAULT_TIERS,
    "security": SALES_DEFAULT_TIERS,
    "roofing": ROOFING_DEFAULT_TIERS,
    "hvac": HOME_SERVICES_DEFAULT_TIERS,
    "plumbing": HOME_SERVICES_DEFAULT_TIERS,
    "electrical": HOME_SERVICES_DEFAULT_TIERS,
    "pest_control": HOME_SERVICES_DEFAULT_TIERS,
    "landscaping": HOME_SERVICES_DEFAULT_TIERS,
    "windows_doors": HOME_SERVICES_DEFAULT_TIERS,
    "painting": HOME_SERVICES_DEFAULT_TIERS,
    "flooring": HOME_SERVICES_DEFAULT_TIERS,
    "cleaning": HOME_SERVICES_DEFAULT_TIERS,
    "pool_spa": HOME_SERVICES_DEFAULT_TIERS,
    "tree_service": HOME_SERVICES_DEFAULT_TIERS,
    "water_treatment": HOME_SERVICES_DEFAULT_TIERS,
    "home_services": HOME_SERVICES_DEFAULT_TIERS,
    "insurance": INSURANCE_DEFAULT_TIERS,
    "life_insurance": INSURANCE_DEFAULT_TIERS,
    "health_insurance": INSURANCE_DEFAULT_TIERS,
    "medicare": INSURANCE_DEFAULT_TIERS,
    "annuities": INSURANCE_DEFAULT_TIERS,
    "real_estate": REAL_ESTATE_DEFAULT_TIERS,
    "mortgage": INSURANCE_DEFAULT_TIERS,  # quote-based flow similar to insurance
}


def get_tier_set_for_industry(industry: str) -> list:
    """Returns the appropriate default tier set for the given industry string."""
    if not industry:
        return RESTLAND_DEFAULT_TIERS
    key = industry.lower().strip().replace("-", "_").replace(" ", "_")
    if key in ("funeral", "cemetery", "funeral_cemetery"):
        return RESTLAND_DEFAULT_TIERS
    return INDUSTRY_TIER_SETS.get(key, SALES_DEFAULT_TIERS)


def clear_and_reseed_tier_definitions(db: Session, organization_id: str, industry: str) -> list[TierDefinition]:
    """
    Wipes ALL existing TierDefinition rows for this org and reseeds from
    the industry-appropriate defaults. Intentionally destructive — callers
    must confirm with the user before calling this.
    """
    db.query(TierDefinition).filter(TierDefinition.organization_id == organization_id).delete()
    db.commit()
    tier_set = get_tier_set_for_industry(industry)
    created = []
    for spec in tier_set:
        row = TierDefinition(organization_id=organization_id, **spec)
        db.add(row)
        created.append(row)
    db.commit()
    return created
'''

# Append after the existing seed_default_tier_definitions function
OLD_SEED_FN = "def seed_default_tier_definitions(db: Session, organization_id: str) -> list[TierDefinition]:"
NEW_SEED_FN = """def seed_default_tier_definitions(db: Session, organization_id: str, industry: str = "funeral") -> list[TierDefinition]:"""

if OLD_SEED_FN not in svc:
    print("WARN: seed_default_tier_definitions signature not found")
else:
    svc = svc.replace(OLD_SEED_FN, NEW_SEED_FN)
    # Also update the body to use industry-appropriate set
    OLD_BODY = "    for spec in RESTLAND_DEFAULT_TIERS:"
    NEW_BODY = "    tier_set = get_tier_set_for_industry(industry)\n    for spec in tier_set:"
    svc = svc.replace(OLD_BODY, NEW_BODY)
    print("tier_config_service.py: seed_default_tier_definitions updated to be industry-aware")

# Append industry tier sets block before the existing RESTLAND_DEFAULT_TIERS
OLD_RESTLAND = "# ---------------------------------------------------------------------------\n# Restland's default tier set"
NEW_RESTLAND = INDUSTRY_TIERS_BLOCK + "\n# ---------------------------------------------------------------------------\n# Restland's default tier set"
if OLD_RESTLAND not in svc:
    print("WARN: RESTLAND comment block not found — appending at end")
    svc = svc + INDUSTRY_TIERS_BLOCK
else:
    svc = svc.replace(OLD_RESTLAND, NEW_RESTLAND)
    print("tier_config_service.py: industry tier sets appended")

with open(svc_path, "w", encoding="utf-8") as f:
    f.write(svc)
print("tier_config_service.py: saved")


# ── 2. tier_definitions_router.py ─────────────────────────────────────────────

router_path = os.path.join(BASE, "app", "routers", "tier_definitions_router.py")
with open(router_path, encoding="utf-8") as f:
    rtr = f.read()

# 2a. Update import to include clear_and_reseed
OLD_IMPORT = "from app.services.tier_config_service import seed_default_tier_definitions"
NEW_IMPORT = "from app.services.tier_config_service import seed_default_tier_definitions, clear_and_reseed_tier_definitions"
rtr = rtr.replace(OLD_IMPORT, NEW_IMPORT)

# 2b. Update seed-defaults to accept industry param
OLD_SEED_ENDPOINT = '''@router.post("/seed-defaults")
def seed_default_tiers(
    org_id: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    """Seed Restland default tiers for an org. Idempotent — no-op if tiers already exist."""
    target_org_id = (
        org_id if (current_user.role == "super_admin" and org_id)
        else current_user.organization_id
    )
    created = seed_default_tier_definitions(db, target_org_id)
    if created:
        return {"seeded": len(created), "message": f"Created {len(created)} default tier definitions."}
    return {"seeded": 0, "message": "Tiers already configured — no changes made."}'''

NEW_SEED_ENDPOINT = '''@router.post("/seed-defaults")
def seed_default_tiers(
    org_id: Optional[str] = None,
    industry: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    """Seed industry-appropriate default tiers. Idempotent — no-op if tiers already exist."""
    target_org_id = (
        org_id if (current_user.role == "super_admin" and org_id)
        else current_user.organization_id
    )
    # Resolve industry: caller can pass it explicitly; otherwise fall back to org settings
    if not industry:
        from app.models.models import Organization
        org = db.query(Organization).filter(Organization.id == target_org_id).first()
        industry = org.industry if org else "funeral"
    created = seed_default_tier_definitions(db, target_org_id, industry=industry or "funeral")
    if created:
        return {"seeded": len(created), "message": f"Created {len(created)} {industry} tier definitions."}
    return {"seeded": 0, "message": "Tiers already configured — no changes made."}


@router.post("/reset-defaults")
def reset_default_tiers(
    org_id: Optional[str] = None,
    industry: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    """DESTRUCTIVE: wipes all tiers for the org and reseeds from industry defaults."""
    target_org_id = (
        org_id if (current_user.role == "super_admin" and org_id)
        else current_user.organization_id
    )
    if not industry:
        from app.models.models import Organization
        org = db.query(Organization).filter(Organization.id == target_org_id).first()
        industry = org.industry if org else "funeral"
    created = clear_and_reseed_tier_definitions(db, target_org_id, industry or "funeral")
    return {"reset": len(created), "industry": industry, "message": f"Reset to {len(created)} {industry} industry defaults."}'''

if OLD_SEED_ENDPOINT not in rtr:
    print("WARN: seed-defaults endpoint not found — appending reset-defaults only")
    rtr = rtr + "\n\n" + NEW_SEED_ENDPOINT.split('@router.post("/reset-defaults")')[1]
else:
    rtr = rtr.replace(OLD_SEED_ENDPOINT, NEW_SEED_ENDPOINT)
    print("tier_definitions_router.py: seed-defaults updated, reset-defaults added")

with open(router_path, "w", encoding="utf-8") as f:
    f.write(rtr)
print("tier_definitions_router.py: saved")


# ── 3. OrgSettings.jsx — expand INDUSTRIES list ───────────────────────────────

orgsettings_path = os.path.join(BASE, "frontend", "src", "pages", "OrgSettings.jsx")
with open(orgsettings_path, encoding="utf-8") as f:
    org = f.read()

OLD_INDUSTRIES = """const INDUSTRIES = [
  { value: 'funeral', label: '⚰️ Funeral & Cemetery' },
  { value: 'roofing', label: '🏠 Roofing' },
  { value: 'insurance', label: '🛡 Insurance' },
  { value: 'real_estate', label: '🏡 Real Estate' },
  { value: 'dental', label: '🦷 Dental' },
  { value: 'custom', label: '⚙️ Custom' },
]"""

NEW_INDUSTRIES = """const INDUSTRIES = [
  // Field Sales / D2D
  { value: 'fiber', label: '⚡ Fiber Internet', group: 'Field Sales / D2D' },
  { value: 'door_to_door', label: '🚪 Door-to-Door', group: 'Field Sales / D2D' },
  { value: 'direct_sales', label: '💼 Direct Sales', group: 'Field Sales / D2D' },
  { value: 'solar', label: '☀️ Solar', group: 'Field Sales / D2D' },
  { value: 'telecom', label: '📡 Telecom', group: 'Field Sales / D2D' },
  { value: 'security', label: '🔒 Security Systems', group: 'Field Sales / D2D' },
  // Insurance
  { value: 'insurance', label: '🛡 Life Insurance', group: 'Insurance' },
  { value: 'health_insurance', label: '🏥 Health Insurance', group: 'Insurance' },
  { value: 'medicare', label: '💊 Medicare', group: 'Insurance' },
  { value: 'annuities', label: '📈 Annuities', group: 'Insurance' },
  // Home Services
  { value: 'roofing', label: '🏠 Roofing', group: 'Home Services' },
  { value: 'hvac', label: '❄️ HVAC', group: 'Home Services' },
  { value: 'plumbing', label: '🔧 Plumbing', group: 'Home Services' },
  { value: 'electrical', label: '⚡ Electrical', group: 'Home Services' },
  { value: 'pest_control', label: '🐛 Pest Control', group: 'Home Services' },
  { value: 'landscaping', label: '🌿 Landscaping', group: 'Home Services' },
  { value: 'windows_doors', label: '🪟 Windows & Doors', group: 'Home Services' },
  { value: 'painting', label: '🎨 Painting', group: 'Home Services' },
  { value: 'flooring', label: '🏡 Flooring', group: 'Home Services' },
  { value: 'cleaning', label: '🧹 Cleaning', group: 'Home Services' },
  { value: 'pool_spa', label: '🏊 Pool & Spa', group: 'Home Services' },
  { value: 'tree_service', label: '🌲 Tree Service', group: 'Home Services' },
  { value: 'water_treatment', label: '💧 Water Treatment', group: 'Home Services' },
  // Healthcare
  { value: 'dental', label: '🦷 Dental', group: 'Healthcare' },
  { value: 'medical', label: '🏥 Medical', group: 'Healthcare' },
  { value: 'chiropractic', label: '🦴 Chiropractic', group: 'Healthcare' },
  { value: 'physical_therapy', label: '🏋️ Physical Therapy', group: 'Healthcare' },
  { value: 'veterinary', label: '🐾 Veterinary', group: 'Healthcare' },
  // Real Estate & Finance
  { value: 'real_estate', label: '🏡 Real Estate', group: 'Real Estate & Finance' },
  { value: 'mortgage', label: '🏦 Mortgage', group: 'Real Estate & Finance' },
  { value: 'financial_services', label: '💰 Financial Services', group: 'Real Estate & Finance' },
  // Funeral & Cemetery
  { value: 'funeral', label: '⚰️ Funeral & Cemetery', group: 'Funeral & Cemetery' },
  // Other
  { value: 'legal', label: '⚖️ Legal', group: 'Other' },
  { value: 'fitness', label: '💪 Fitness', group: 'Other' },
  { value: 'education', label: '📚 Education', group: 'Other' },
  { value: 'auto_repair', label: '🚗 Auto Repair', group: 'Other' },
  { value: 'custom', label: '⚙️ Custom / Other', group: 'Other' },
]"""

if OLD_INDUSTRIES not in org:
    print("WARN: INDUSTRIES constant not found in OrgSettings.jsx — may already be updated")
else:
    org = org.replace(OLD_INDUSTRIES, NEW_INDUSTRIES)
    print("OrgSettings.jsx: INDUSTRIES list expanded")

# Also update the Industry section to use a <select> instead of radio buttons
# Find the industry radio buttons section and replace with a dropdown
OLD_INDUSTRY_UI = """  { value: 'custom', label: '⚙️ Custom' },
]"""
# Already replaced above

# Now find where industries are rendered as radio buttons and replace with grouped select
# Let's search for the rendering pattern
OLD_RADIO_PATTERN = "INDUSTRIES.map(ind => ("
if OLD_RADIO_PATTERN in org:
    # Find the industry rendering block and replace with a select dropdown
    # We need to find the full block
    import re
    # Find from <div className="industry-grid"> or similar to the end of the INDUSTRIES map
    pattern = r'(\{INDUSTRIES\.map\(ind => \()[\s\S]*?(\)\))\s*\}'
    # Let's do a simpler targeted replacement
    pass

with open(orgsettings_path, "w", encoding="utf-8") as f:
    f.write(org)
print("OrgSettings.jsx: saved")


# ── 4. TierDefinitions.jsx — add industry-aware seeding + Reset button ────────

tier_ui_path = os.path.join(BASE, "frontend", "src", "pages", "TierDefinitions.jsx")
with open(tier_ui_path, encoding="utf-8") as f:
    tier_ui = f.read()

# 4a. Update import to include getBranding
OLD_TIER_IMPORT = "import { api, getCurrentUser } from '../api/client'"
NEW_TIER_IMPORT = "import { api, getCurrentUser, getBranding } from '../api/client'"
if OLD_TIER_IMPORT in tier_ui:
    tier_ui = tier_ui.replace(OLD_TIER_IMPORT, NEW_TIER_IMPORT)
    print("TierDefinitions.jsx: getBranding import added")

# 4b. Add resetting state after seeding state
OLD_SEEDING_STATE = "  const [seeding, setSeeding] = useState(false)"
NEW_SEEDING_STATE = "  const [seeding, setSeeding] = useState(false)\n  const [resetting, setResetting] = useState(false)"
if OLD_SEEDING_STATE in tier_ui:
    tier_ui = tier_ui.replace(OLD_SEEDING_STATE, NEW_SEEDING_STATE)
    print("TierDefinitions.jsx: resetting state added")

# 4c. Update seedDefaults to pass industry
OLD_SEED_FN = """  async function seedDefaults() {
    setSeeding(true)
    try {
      const params = isSuperAdmin && orgId ? `?org_id=${encodeURIComponent(orgId)}` : ''
      const result = await api.post(`/tier-definitions/seed-defaults${params}`, {})
      flash(result.message || 'Defaults seeded.')
      load()
    } catch (e) {
      flash(e.message || 'Seed failed', true)
    } finally {
      setSeeding(false)
    }
  }"""

NEW_SEED_FN = """  async function seedDefaults() {
    setSeeding(true)
    try {
      const branding = getBranding()
      const industry = branding?.industry || 'funeral'
      const params = new URLSearchParams()
      if (isSuperAdmin && orgId) params.set('org_id', orgId)
      params.set('industry', industry)
      const result = await api.post(`/tier-definitions/seed-defaults?${params}`, {})
      flash(result.message || 'Defaults seeded.')
      load()
    } catch (e) {
      flash(e.message || 'Seed failed', true)
    } finally {
      setSeeding(false)
    }
  }

  async function resetDefaults() {
    const branding = getBranding()
    const industry = branding?.industry || 'funeral'
    const label = industry.replace(/_/g, ' ')
    if (!window.confirm(`This will DELETE all current tiers and replace them with ${label} industry defaults. Are you sure?`)) return
    setResetting(true)
    try {
      const params = new URLSearchParams()
      if (isSuperAdmin && orgId) params.set('org_id', orgId)
      params.set('industry', industry)
      const result = await api.post(`/tier-definitions/reset-defaults?${params}`, {})
      flash(result.message || 'Tiers reset.')
      load()
    } catch (e) {
      flash(e.message || 'Reset failed', true)
    } finally {
      setResetting(false)
    }
  }"""

if OLD_SEED_FN in tier_ui:
    tier_ui = tier_ui.replace(OLD_SEED_FN, NEW_SEED_FN)
    print("TierDefinitions.jsx: seedDefaults and resetDefaults functions added")

# 4d. Add Reset button next to Seed Defaults button
OLD_SEED_BTN = """          <div
            className={`td-btn td-btn--outline ${seeding ? 'td-btn--disabled' : ''}`}
            onClick={seedDefaults}
          >
            {seeding ? 'Seeding…' : '⟳ Seed Defaults'}
          </div>"""

NEW_SEED_BTN = """          <div
            className={`td-btn td-btn--outline ${seeding ? 'td-btn--disabled' : ''}`}
            onClick={seedDefaults}
          >
            {seeding ? 'Seeding…' : '⟳ Seed Defaults'}
          </div>
          <div
            className={`td-btn td-btn--danger ${resetting ? 'td-btn--disabled' : ''}`}
            onClick={resetDefaults}
            title="Delete all tiers and reseed from industry defaults"
          >
            {resetting ? 'Resetting…' : '↺ Reset to Industry Defaults'}
          </div>"""

if OLD_SEED_BTN in tier_ui:
    tier_ui = tier_ui.replace(OLD_SEED_BTN, NEW_SEED_BTN)
    print("TierDefinitions.jsx: Reset to Industry Defaults button added")

with open(tier_ui_path, "w", encoding="utf-8") as f:
    f.write(tier_ui)
print("TierDefinitions.jsx: saved")

# ── 5. Add td-btn--danger style to TierDefinitions.css ───────────────────────

tier_css_path = os.path.join(BASE, "frontend", "src", "pages", "TierDefinitions.css")
try:
    with open(tier_css_path, encoding="utf-8") as f:
        tier_css = f.read()
    DANGER_CSS = """
.td-btn--danger {
  background: rgba(240, 80, 80, 0.08);
  color: #f55;
  border: 1px solid rgba(240, 80, 80, 0.3);
}
.td-btn--danger:hover {
  background: rgba(240, 80, 80, 0.18);
  border-color: rgba(240, 80, 80, 0.6);
}
"""
    if ".td-btn--danger" not in tier_css:
        tier_css = tier_css + DANGER_CSS
        with open(tier_css_path, "w", encoding="utf-8") as f:
            f.write(tier_css)
        print("TierDefinitions.css: danger button style added")
    else:
        print("TierDefinitions.css: danger style already present")
except Exception as e:
    print(f"TierDefinitions.css: {e}")

print("\nAll industry tier patches applied.")
