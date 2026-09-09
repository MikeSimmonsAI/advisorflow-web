/**
 * Shapes the server actually returns.
 *
 * These are DESCRIPTIONS, not contracts the app enforces. Everything optional
 * is optional because the server genuinely may omit it, and the screens are
 * written to render a record with holes rather than to crash on one — a rep in
 * a car park needs the four fields that are there, not a blank screen because
 * the fifth was null.
 */

// ── contexts (/auth/my-contexts) ────────────────────────────────────────────

export type ContextType =
  | 'platform'
  | 'executive'
  | 'workspace'
  | 'workspace_selector'
  | 'legacy_tenant';

export type AuthorizedContext = {
  type: ContextType;
  label?: string;
  role?: string;
  path?: string;
  brand_sales_org_id?: string;
  platform_id?: string;
  platform_name?: string;
  organization_id?: string;
  organization_name?: string;
  organization_slug?: string;
};

export type MyContexts = {
  contexts: AuthorizedContext[];
  platform_contexts: AuthorizedContext[];
  executive_contexts: AuthorizedContext[];
  workspace_contexts: AuthorizedContext[];
  has_back_office: boolean;
  workspace_count: number;
  default_context: AuthorizedContext;
};

export type LoginResponse = {
  access_token: string;
  token_type: string;
  role: string;
  full_name: string;
  organization_id: string | null;
  must_change_password: boolean;
};

export type DeviceSession = {
  id: string;
  client: string;
  device_name: string | null;
  app_version: string | null;
  created_at: string | null;
  last_seen_at: string | null;
  expires_at: string | null;
  revoked_at: string | null;
  revoked_reason: string | null;
  is_live: boolean;
  is_current: boolean;
};

// ── sales ───────────────────────────────────────────────────────────────────

export type Opportunity = {
  id: string;
  name?: string;
  company_name?: string;
  stage?: string;
  owner_user_id?: string;
  owner_name?: string;
  brand_sales_org_id?: string;
  amount?: number | null;
  contact_name?: string;
  contact_email?: string;
  contact_phone?: string;
  next_step?: string;
  expected_close_date?: string | null;
  updated_at?: string;
  created_at?: string;
  [k: string]: unknown;
};

export type Appointment = {
  id: string;
  title?: string;
  starts_at?: string;
  ends_at?: string;
  status?: string;
  confirmation_status?: string;
  location?: string;
  meeting_url?: string;
  opportunity_id?: string | null;
  opportunity_name?: string;
  prospect_name?: string;
  prospect_phone?: string;
  prospect_email?: string;
  meeting_type?: string;
  [k: string]: unknown;
};

export type Lead = {
  id: string;
  first_name?: string;
  last_name?: string;
  full_name?: string;
  phone?: string;
  email?: string;
  status?: string;
  tier?: string;
  message_track?: string;
  last_contacted_at?: string | null;
  created_at?: string;
  [k: string]: unknown;
};

export type Proposal = {
  id: string;
  opportunity_id?: string;
  version?: number;
  status?: string;
  title?: string;
  total?: number | null;
  package_id?: string | null;
  package_name?: string;
  is_below_floor?: boolean;
  pricing_request_status?: string | null;
  published_at?: string | null;
  sent_at?: string | null;
  deal_room_url?: string | null;
  blocks?: Array<Record<string, unknown>>;
  [k: string]: unknown;
};

export type SalesPackage = {
  id: string;
  name?: string;
  price?: number | null;
  floor_price?: number | null;
  description?: string;
  [k: string]: unknown;
};

export type MyDay = Record<string, unknown>;

export type CompensationMe = Record<string, unknown>;

// ── manager ─────────────────────────────────────────────────────────────────

export type ApprovalRequest = {
  id: string;
  proposal_id?: string;
  opportunity_id?: string;
  opportunity_name?: string;
  requested_by_name?: string;
  requested_amount?: number | null;
  floor_amount?: number | null;
  reason?: string;
  status?: string;
  created_at?: string;
  [k: string]: unknown;
};

// ── executive / owner ───────────────────────────────────────────────────────

export type ExecutiveOrg = {
  id?: string;
  organization_id?: string;
  name?: string;
  organization_name?: string;
  severity?: string;
  health?: string;
  [k: string]: unknown;
};

export type OwnerQueue = {
  key?: string;
  label?: string;
  title?: string;
  count?: number;
  severity?: string;
  items?: Array<Record<string, unknown>>;
  [k: string]: unknown;
};
