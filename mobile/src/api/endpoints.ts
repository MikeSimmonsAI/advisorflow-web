/**
 * Every backend route this app calls, in one file, named after the router that
 * owns it.
 *
 * WHY ONE FILE. A path typed at a call site is a path that drifts. When
 * `/sales/appointments/{id}/confirmation` changes shape, the change should be
 * one edit and a type error everywhere it mattered — not a search for string
 * literals across thirty screens.
 *
 * WHY THESE PATHS AND NOT NEW ONES. Every one of them already exists and is
 * already authorised server-side. Mobile is another client of the same
 * platform, not a second platform: there is no mobile pricing engine, no mobile
 * scheduling engine, no mobile compensation maths, and no mobile idea of who an
 * executive may see.
 *
 * The one thing NOT here is prospect messaging for brand sales. It does not
 * exist server-side (Phase 0 GAP-4: no sales* router imports sms_service or
 * email_service, and the tenant messaging routers are require_tenant_user, which
 * a brand salesperson is not). `src/comms/communications.ts` is the interface
 * that gap lives behind.
 */

import { api, qs } from './client';
import type {
  Appointment, ApprovalRequest, CompensationMe, DeviceSession, ExecutiveOrg,
  Lead, LoginResponse, MyContexts, MyDay, Opportunity, OwnerQueue, Proposal,
  SalesPackage,
} from './types';

// ── auth (app/routers/auth_router.py) ───────────────────────────────────────

export const auth = {
  /** OAuth2 password form — `username` is the email, as the web client does. */
  login: (email: string, password: string) =>
    api.post<LoginResponse>('/auth/login', undefined, {
      form: { username: email, password },
      allowUnauthorized: true,   // a 401 here means "wrong password", not "session gone"
    }),

  myContexts: () => api.get<MyContexts>('/auth/my-contexts'),

  /** The server confirms membership BEFORE the first workspace screen paints. */
  enterWorkspace: (organizationId: string) =>
    api.get<Record<string, unknown>>(`/auth/workspace/${organizationId}`),

  refresh: () => api.post<{ access_token: string }>('/auth/refresh'),

  /** This device only. */
  logout: () => api.post<{ success: boolean; scope: string }>('/auth/logout'),

  /** Every device, including this one. */
  logoutAll: () => api.post<{ success: boolean; sessions_ended: number }>('/auth/logout-all'),

  sessions: () => api.get<{ sessions: DeviceSession[]; count: number }>('/auth/sessions'),
  revokeSession: (id: string) => api.del<{ success: boolean }>(`/auth/sessions/${id}`),

  changePassword: (current: string, next: string) =>
    api.post<{ success: boolean; reauthenticate: boolean }>('/auth/change-password', {
      current_password: current, new_password: next, confirm_password: next,
    }),
};

// ── mobile device support (app/routers/device_router.py) ────────────────────

export const devices = {
  register: (body: {
    token: string; platform: string; device_id?: string; device_name?: string;
    app_version?: string; active_context?: string; active_scope_id?: string;
  }) => api.post<Record<string, unknown>>('/me/devices', body),

  list: () => api.get<{ devices: Array<Record<string, unknown>>; push_enabled: boolean }>('/me/devices'),

  unregister: (token: string) => api.del<{ success: boolean }>('/me/devices', { token }),

  uploadCapability: () =>
    api.get<{ backend: string; durable: boolean; uploads_enabled: boolean; reason: string | null }>(
      '/me/upload-capability'),
};

// ── brand sales (app/routers/sales_router.py) ───────────────────────────────

export const sales = {
  me: (brandSalesOrgId?: string) =>
    api.get<Record<string, unknown>>(`/sales/me${qs({ brand_sales_org_id: brandSalesOrgId })}`),

  myDay: (brandSalesOrgId?: string) =>
    api.get<MyDay>(`/sales/my-day${qs({ brand_sales_org_id: brandSalesOrgId })}`),

  packages: (brandSalesOrgId?: string) =>
    api.get<SalesPackage[] | { packages: SalesPackage[] }>(
      `/sales/packages${qs({ brand_sales_org_id: brandSalesOrgId })}`),

  team: (brandSalesOrgId?: string) =>
    api.get<Record<string, unknown>>(`/sales/team${qs({ brand_sales_org_id: brandSalesOrgId })}`),

  opportunities: (params: {
    brand_sales_org_id?: string; stage?: string; owner_user_id?: string;
    include_lost?: boolean;
  } = {}) => api.get<Opportunity[] | { opportunities: Opportunity[] }>(
    `/sales/opportunities${qs(params)}`),

  opportunity: (id: string) => api.get<Opportunity>(`/sales/opportunities/${id}`),

  createOpportunity: (body: Record<string, unknown>) =>
    api.post<Opportunity>('/sales/opportunities', body),

  patchOpportunity: (id: string, body: Record<string, unknown>) =>
    api.patch<Opportunity>(`/sales/opportunities/${id}`, body),

  /** Stage moves go through the same PATCH the desktop uses. No second state
   *  machine, no mobile-only "won" flow. */
  setStage: (id: string, stage: string) =>
    api.patch<Opportunity>(`/sales/opportunities/${id}`, { stage }),

  addNote: (id: string, body: Record<string, unknown>) =>
    api.post<Record<string, unknown>>(`/sales/opportunities/${id}/notes`, body),

  upsertDiscovery: (id: string, body: Record<string, unknown>) =>
    api.put<Record<string, unknown>>(`/sales/opportunities/${id}/discovery`, body),

  closing: (id: string) => api.get<Record<string, unknown>>(`/sales/opportunities/${id}/closing`),

  implementation: (id: string) =>
    api.get<Record<string, unknown>>(`/sales/opportunities/${id}/implementation`),

  implementations: () => api.get<Record<string, unknown>>('/sales/implementations'),
};

// ── scheduling (app/routers/sales_scheduling_router.py) ─────────────────────

export const scheduling = {
  appointments: (params: {
    brand_sales_org_id?: string; date_from?: string; date_to?: string;
    scope?: 'mine' | 'team'; include_cancelled?: boolean;
  } = {}) => api.get<Appointment[] | { appointments: Appointment[] }>(
    `/sales/appointments${qs(params)}`),

  appointment: (id: string) => api.get<Appointment>(`/sales/appointments/${id}`),

  meetingTypes: (params: { brand_sales_org_id?: string; opportunity_id?: string } = {}) =>
    api.get<Record<string, unknown>>(`/sales/meeting-types${qs(params)}`),

  book: (body: Record<string, unknown>) =>
    api.post<Appointment>('/sales/appointments', body),

  reschedule: (id: string, body: Record<string, unknown>) =>
    api.post<Appointment>(`/sales/appointments/${id}/reschedule`, body),

  cancel: (id: string, body: Record<string, unknown>) =>
    api.post<Appointment>(`/sales/appointments/${id}/cancel`, body),

  /** Writes the canonical CONFIRMATION_STATUSES. There is no second outcome
   *  model on the phone. */
  setConfirmation: (id: string, body: Record<string, unknown>) =>
    api.post<Appointment>(`/sales/appointments/${id}/confirmation`, body),

  hostLink: (id: string) => api.get<Record<string, unknown>>(`/sales/appointments/${id}/host-link`),

  resendInvitation: (id: string) =>
    api.post<Record<string, unknown>>(`/sales/appointments/${id}/resend-invitation`),

  myAvailability: () => api.get<Record<string, unknown>>('/sales/availability/me'),

  teamAvailability: (params: { day?: string; days?: number; brand_sales_org_id?: string } = {}) =>
    api.get<Record<string, unknown>>(`/sales/availability/team${qs(params)}`),
};

// ── proposals (app/routers/sales_proposal_router.py) ────────────────────────

export const proposals = {
  forOpportunity: (opportunityId: string) =>
    api.get<Proposal[] | { proposals: Proposal[] }>(
      `/sales/opportunities/${opportunityId}/proposals`),

  get: (id: string) => api.get<Proposal>(`/sales/proposals/${id}`),

  create: (body: Record<string, unknown>) => api.post<Proposal>('/sales/proposals', body),

  update: (id: string, body: Record<string, unknown>) =>
    api.patch<Proposal>(`/sales/proposals/${id}`, body),

  newVersion: (id: string) => api.post<Proposal>(`/sales/proposals/${id}/version`),

  publish: (id: string) => api.post<Proposal>(`/sales/proposals/${id}/publish`),

  /** A deliberate second tap in the UI. Sending is not an autosave. */
  send: (id: string, body: Record<string, unknown>) =>
    api.post<Record<string, unknown>>(`/sales/proposals/${id}/send`, body),

  /** Below-floor pricing routes HERE rather than to send. The UI says so before
   *  the rep can try. */
  requestPricingApproval: (id: string, body: Record<string, unknown>) =>
    api.post<Record<string, unknown>>(`/sales/proposals/${id}/pricing-request`, body),

  withdrawPricingRequest: (id: string) =>
    api.post<Record<string, unknown>>(`/sales/proposals/${id}/pricing-request/withdraw`),

  activity: (id: string) => api.get<Record<string, unknown>>(`/sales/proposals/${id}/activity`),

  addBlock: (id: string, body: Record<string, unknown>) =>
    api.post<Record<string, unknown>>(`/sales/proposals/${id}/blocks`, body),

  deleteBlock: (id: string, blockId: string) =>
    api.del<Record<string, unknown>>(`/sales/proposals/${id}/blocks/${blockId}`),
};

// ── manager (app/routers/sales_manager_router.py) ───────────────────────────

export const manager = {
  overview: (params: { brand_sales_org_id?: string; day?: string } = {}) =>
    api.get<Record<string, unknown>>(`/sales/manager/overview${qs(params)}`),

  repDetail: (userId: string, brandSalesOrgId?: string) =>
    api.get<Record<string, unknown>>(
      `/sales/manager/reps/${userId}${qs({ brand_sales_org_id: brandSalesOrgId })}`),

  approvals: (brandSalesOrgId?: string) =>
    api.get<ApprovalRequest[] | { approvals: ApprovalRequest[]; requests?: ApprovalRequest[] }>(
      `/sales/manager/approvals${qs({ brand_sales_org_id: brandSalesOrgId })}`),

  decide: (requestId: string, body: Record<string, unknown>) =>
    api.post<Record<string, unknown>>(`/sales/manager/approvals/${requestId}/decide`, body),

  pipeline: (params: {
    brand_sales_org_id?: string; owner_user_id?: string; stage?: string;
    include_deals?: boolean;
  } = {}) => api.get<Record<string, unknown>>(`/sales/manager/pipeline-projection${qs(params)}`),
};

// ── compensation (app/routers/compensation_router.py) ───────────────────────

export const compensation = {
  /** The rep's own. `require_sales_member`, no parameters — a rep cannot ask
   *  for somebody else's, which is why this is the only one on the phone. */
  me: () => api.get<CompensationMe>('/sales/compensation/me'),
};

// ── executive (app/routers/executive_router.py) ─────────────────────────────
//
// EVERY ONE OF THESE IS SCOPED BY executive_authority.portfolio_authority() ON
// THE SERVER. There is no client-side portfolio, no "all orgs on my brand"
// shortcut, and no assumption that a shared platform implies access. A normal
// executive sees explicitly assigned organisations and nothing else; an
// executive with no assignments sees an empty portfolio, which is correct and
// is rendered as such rather than as an error.

export const executive = {
  context: () => api.get<Record<string, unknown>>('/executive/context'),
  commandCenter: () => api.get<Record<string, unknown>>('/executive/command-center'),
  organizations: () => api.get<ExecutiveOrg[] | { organizations: ExecutiveOrg[] }>(
    '/executive/organizations'),
  organization: (orgId: string) =>
    api.get<Record<string, unknown>>(`/executive/organizations/${orgId}`),
  orgPerformance: (orgId: string) =>
    api.get<Record<string, unknown>>(`/executive/organizations/${orgId}/performance`),
  portfolio: () => api.get<Record<string, unknown>>('/executive/portfolio'),
  portfolioHealth: (filter = 'all') =>
    api.get<Record<string, unknown>>(`/executive/portfolio/health${qs({ filter })}`),
  customerHealth: () => api.get<Record<string, unknown>>('/executive/customer-health'),
  team: () => api.get<Record<string, unknown>>('/executive/team'),
};

// ── owner / god (god_router, god_ops_router, god_billing_router, 360) ───────
//
// Deliberately a SMALL slice of a very large control plane. A phone is the
// wrong place to end a customer, so nothing destructive is reachable here:
// no suspend, no cancellation, no offboarding, no permanent delete. Those
// routes exist and are not called.

export const owner = {
  stats: () => api.get<Record<string, unknown>>('/god/stats'),
  platformHealth: () => api.get<Record<string, unknown>>('/god/platform-health'),
  queues: () => api.get<OwnerQueue[] | { queues: OwnerQueue[] }>('/god/ops/queues'),
  salesOperations: () => api.get<Record<string, unknown>>('/god/ops/sales-operations'),
  customers: (params: { platform_id?: string; limit?: number } = {}) =>
    api.get<Record<string, unknown>>(`/god/ops/customer-organizations${qs(params)}`),
  customer360: (orgId: string) =>
    api.get<Record<string, unknown>>(`/god/customer-360/customers/${orgId}`),
  implementations: (params: Record<string, string | number | boolean | undefined> = {}) =>
    api.get<Record<string, unknown>>(`/god/ops/implementations${qs(params)}`),
  revenue: () => api.get<Record<string, unknown>>('/god/billing/revenue'),
};

// ── customer workspace / advisor (tenant routers) ───────────────────────────
//
// The advisor experience is the RICHEST one on the platform today, because the
// tenant messaging stack is fully backed — unlike brand sales (GAP-4). These
// are the same routes the web workspace calls, with page_size trimmed for a
// phone: /leads defaults to 500 and accepts up to 2000, which is a fine default
// for a desktop table and absurd on a cellular connection.

export const advisor = {
  leads: (params: { page?: number; page_size?: number; status?: string; search?: string } = {}) =>
    api.get<Record<string, unknown>>(`/leads/${qs({ page_size: 25, ...params })}`),
  lead: (id: string) => api.get<Lead>(`/leads/${id}`),
  leadTimeline: (id: string) => api.get<Record<string, unknown>>(`/leads/${id}/timeline`),
  dailyBriefing: () => api.get<Record<string, unknown>>('/leads/daily-briefing'),
  needsReview: () => api.get<Record<string, unknown>>('/leads/needs-review'),
  replies: (params: Record<string, string | number | undefined> = {}) =>
    api.get<Record<string, unknown>>(`/sms/replies${qs(params)}`),
  replyCounts: () => api.get<Record<string, unknown>>('/sms/replies/counts'),
  sendSms: (body: Record<string, unknown>) => api.post<Record<string, unknown>>('/sms/send', body),
  composeContext: (leadId: string) =>
    api.get<Record<string, unknown>>(`/compose/${leadId}/context`),
  calendarEvents: (params: Record<string, string | undefined> = {}) =>
    api.get<Record<string, unknown>>(`/calendar/events${qs(params)}`),
  recordOutcome: (body: Record<string, unknown>) =>
    api.post<Record<string, unknown>>('/outcomes/', body),
  notifications: () => api.get<Record<string, unknown>>('/notifications/'),
};
