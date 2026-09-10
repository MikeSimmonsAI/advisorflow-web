/**
 * THE PLATFORM'S VOCABULARY, MIRRORED — NEVER RE-INVENTED.
 *
 * Every value below is copied verbatim from the Python that owns it. The file
 * and constant are named against each block so a future change can be traced to
 * one place rather than hunted for:
 *
 *   app/models/sales_models.py       OPPORTUNITY_STAGES, STAGE_LABELS
 *   app/models/scheduling_models.py  APPOINTMENT_STATUSES, CONFIRMATION_STATUSES,
 *                                    ATTEND_*, DEFAULT_TIMEZONE
 *   app/services/severity.py         healthy / attention / action_required /
 *                                    unavailable / no_data
 *
 * WHY MIRROR RATHER THAN INVENT: a second vocabulary is how "closing" becomes
 * "close" on one client, and how a stage the server understands renders as a
 * blank chip on a phone. The mirror is deliberately dumb — no reordering, no
 * "friendlier" labels, no extra states. If the server gains a stage, this file
 * gains the same string and nothing else changes.
 *
 * WHY NOT GENERATE IT: Phase 0 said a generated shared package is the only
 * honest way to share these, and it is right. Until that generator exists this
 * file is the single hand-copied point, and `isKnownStage` below exists so an
 * unknown value from a newer server degrades to its raw string rather than
 * disappearing.
 */

// ── opportunity stages (sales_models.OPPORTUNITY_STAGES) ────────────────────

export const OPPORTUNITY_STAGES = [
  'prospect',
  'contacted',
  'discovery',
  'demo_build',
  'demo_proposal',
  'closing',
  'won',
  'onboarding',
  'live',
  'lost',
] as const;

export type OpportunityStage = (typeof OPPORTUNITY_STAGES)[number];

export const STAGE_LABELS: Record<string, string> = {
  prospect: 'Prospect',
  contacted: 'Contacted',
  discovery: 'Discovery',
  demo_build: 'Demo Build',
  demo_proposal: 'Demo & Proposal',
  closing: 'Closing',
  won: 'Won',
  onboarding: 'Onboarding',
  live: 'Live',
  lost: 'Lost',
};

/** Stages that are still in play. `won`/`live` are outcomes, `lost` is an end. */
export const OPEN_STAGES: readonly string[] = [
  'prospect', 'contacted', 'discovery', 'demo_build', 'demo_proposal', 'closing',
];

export function isKnownStage(value: string | null | undefined): boolean {
  return !!value && (OPPORTUNITY_STAGES as readonly string[]).includes(value);
}

/** Never returns empty. A stage the server knows and this build does not still
 *  renders as itself rather than vanishing from the screen. */
export function stageLabel(value: string | null | undefined): string {
  if (!value) return 'No stage';
  return STAGE_LABELS[value] ?? value.replace(/_/g, ' ');
}

// ── appointments (scheduling_models) ────────────────────────────────────────

export const APPOINTMENT_STATUSES = [
  'scheduled', 'completed', 'cancelled', 'no_show',
] as const;
export type AppointmentStatus = (typeof APPOINTMENT_STATUSES)[number];

export const APPOINTMENT_STATUS_LABELS: Record<string, string> = {
  scheduled: 'Scheduled',
  completed: 'Completed',
  cancelled: 'Cancelled',
  no_show: 'No show',
};

export const CONFIRMATION_STATUSES = [
  'pending', 'sent', 'confirmed', 'declined', 'cancelled', 'no_show',
] as const;
export type ConfirmationStatus = (typeof CONFIRMATION_STATUSES)[number];

export const CONFIRMATION_LABELS: Record<string, string> = {
  pending: 'Not yet asked',
  sent: 'Confirmation sent',
  confirmed: 'Confirmed',
  declined: 'Declined',
  cancelled: 'Cancelled',
  no_show: 'No show',
};

export const ATTENDANCE = [
  'unknown', 'accepted', 'declined', 'attended', 'no_show',
] as const;

export const DEFAULT_TIMEZONE = 'America/Chicago';

// ── severity (app/services/severity.py) ─────────────────────────────────────
//
// THE ONE RULE WORTH REPEATING: an unknown severity normalises to
// `unavailable`, NEVER to `healthy`. A screen that cannot reach the truth must
// not render a green tick. That is the whole reason this is a function rather
// than a lookup with a default.

export const SEVERITIES = [
  'healthy', 'attention', 'action_required', 'unavailable', 'no_data',
] as const;
export type Severity = (typeof SEVERITIES)[number];

export function normaliseSeverity(value: string | null | undefined): Severity {
  if (value && (SEVERITIES as readonly string[]).includes(value)) {
    return value as Severity;
  }
  return 'unavailable';
}

export const SEVERITY_LABELS: Record<Severity, string> = {
  healthy: 'Healthy',
  attention: 'Needs attention',
  action_required: 'Action required',
  unavailable: 'Unavailable',
  no_data: 'No data',
};

// ── membership roles (sales_models) ─────────────────────────────────────────

export const ROLE_SALES_REP = 'sales_rep';
export const ROLE_SALES_MANAGER = 'sales_manager';
export const ROLE_BRAND_EXECUTIVE = 'brand_executive';
export const ROLE_GOD = 'god_admin';

// ── membership role labels (manager_workspace.team_members role_label) ──────
//
// The server already sends `role_label` on every member row. These exist for
// the payloads that carry only `role`, and so that a screen never has to reach
// for the raw string: "sales_rep" under a person's name is a database column
// leaking onto a phone.

export const ROLE_LABELS: Record<string, string> = {
  sales_rep: 'Sales Representative',
  sales_manager: 'Sales Manager',
  brand_executive: 'Executive',
  god_admin: 'Platform Owner',
  org_admin: 'Workspace Administrator',
  advisor: 'Advisor',
};

/** Never returns a raw code. An unknown role degrades to Title Case words. */
export function roleLabel(value: string | null | undefined): string {
  if (!value) return 'Team member';
  const known = ROLE_LABELS[value];
  if (known) return known;
  return value.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase());
}

// ── attention kinds (app/services/manager_workspace.py, every _item() call) ──
//
// THE SERVER ALREADY SPEAKS ENGLISH. Each attention row carries `title`,
// `detail` and `action` written for a human — "Sent and never opened", "Sent 6
// days ago. No portal activity.", "Check the address, or call". The manager
// screen was reading `kind` instead, which is why a card said "video_failed".
//
// So these labels are a FALLBACK, not the primary source: they are used when a
// row arrives without its title (an older server, a partial payload), and by
// the by-kind summary, which only ever has the code. Reading `title` first and
// falling back here means a newer server's new kind still renders as words.

export const ATTENTION_KIND_LABELS: Record<string, string> = {
  proposal_declined: 'Proposal declined',
  change_requested: 'Customer asked for a change',
  proposal_expired: 'Proposal expired',
  proposal_unopened: 'Proposal never opened',
  proposal_ready: 'Proposal ready but unsent',
  overdue_action: 'Follow-up overdue',
  no_next_action: 'No next action set',
  stalled: 'Stalled in stage',
  no_activity: 'No recent activity',
  calendar_sync: 'Calendar sync problem',
  video_failed: 'Video meeting failed',
};

export function attentionKindLabel(value: string | null | undefined): string {
  if (!value) return 'Needs attention';
  const known = ATTENTION_KIND_LABELS[value];
  if (known) return known;
  return value.replace(/_/g, ' ').replace(/^\w/, (c) => c.toUpperCase());
}

// ── meeting buckets (manager_workspace.team_today, `kind`) ──────────────────

export const MEETING_KIND_LABELS: Record<string, string> = {
  discovery: 'Discovery',
  demo: 'Demo',
  proposal: 'Proposal review',
  closing: 'Closing',
  internal: 'Internal',
  other: 'Meeting',
};

export function meetingKindLabel(value: string | null | undefined): string {
  if (!value) return 'Meeting';
  return MEETING_KIND_LABELS[value]
    ?? value.replace(/_/g, ' ').replace(/^\w/, (c) => c.toUpperCase());
}

/**
 * THE GUARD. Any string that still looks like an identifier — lower_snake_case
 * with no spaces — has escaped a label table. Tests use this to sweep the
 * rendered vocabulary; screens use it as a last line of defence before putting
 * a server string in front of a person.
 */
export function looksLikeCode(value: string | null | undefined): boolean {
  if (!value) return false;
  return /^[a-z][a-z0-9]*(_[a-z0-9]+)+$/.test(value.trim());
}

/** A server string that is safe to show, or the fallback if it is a code. */
export function humanText(value: string | null | undefined,
                          fallback: string): string {
  if (!value) return fallback;
  const t = String(value).trim();
  if (!t) return fallback;
  return looksLikeCode(t) ? fallback : t;
}

// ── audit-log actions (app/routers/audit_log_router.log_action callers) ─────
//
// Owner Activity renders the platform audit log. Its `action` column is a
// dotted machine code — "customer.created", "platform_owner.enter_customer" —
// and the screen printed whichever of `summary`/`action`/`event_type` it found
// first, so on any row without a summary a person read the column name.
//
// Only the codes this screen can actually surface are listed. Everything else
// degrades through `auditEventLabel` into words rather than being hidden: a
// timeline that silently drops an event is worse than one showing a phrase it
// had to derive.

export const AUDIT_EVENT_LABELS: Record<string, string> = {
  'customer.created': 'Customer created',
  'customer.activated': 'Customer activated',
  'customer.deactivated': 'Customer deactivated',
  'customer.archived': 'Customer archived',
  'customer.reactivated': 'Customer reactivated',
  'customer.cancelled': 'Customer cancelled',
  'customer.cancellation_requested': 'Cancellation requested',
  'customer.offboarding_started': 'Offboarding started',
  'customer.user_added': 'User added to a customer',
  'customer.location_created': 'Customer location added',
  'customer.features_set': 'Customer features changed',
  'customer_provisioned': 'Customer provisioned',
  'customer_admin_created': 'Customer administrator created',
  'customer_admin_invited': 'Customer administrator invited',
  'customer_admin_activated': 'Customer administrator activated their account',
  'customer_admin_invite_revoked': 'Customer administrator invite revoked',
  'customer_marked_live': 'Customer marked live',
  'user.create': 'User created',
  'user.update': 'User updated',
  'user.deactivate': 'User deactivated',
  'user.reactivate': 'User reactivated',
  'user.reset_password': 'Password reset',
  'user.force_logout': 'User signed out everywhere',
  'org.update': 'Organisation updated',
  'platform.brand_sales_org_created': 'Brand sales team created',
  'platform_owner.enter_customer': 'Owner entered a customer workspace',
  'platform_owner.exit_customer': 'Owner left a customer workspace',
  'platform_owner.enter_brand': 'Owner entered a brand',
  'platform_owner.neutralized': 'Owner access neutralised',
  'brand.capabilities_set': 'Brand capabilities changed',
  'package_pricing_changed': 'Package pricing changed',
  'billing_configuration_changed': 'Billing details changed',
  'implementation_owner_assigned': 'Implementation owner changed',
  'implementation_milestone_added': 'Milestone added',
  'implementation_milestone_changed': 'Milestone updated',
  'sales_user_created': 'Salesperson created',
  'sales_membership_granted': 'Team membership granted',
  'sales_membership_role_changed': 'Team role changed',
  'sales_access_link_issued': 'Sign-in link issued',
  'sales_access_activated': 'Sales access activated',
  'sales_access_link_revoked': 'Sign-in link revoked',
};

/** Never a dotted code, never empty. */
export function auditEventLabel(value: string | null | undefined): string {
  if (!value) return 'Platform change';
  const known = AUDIT_EVENT_LABELS[value];
  if (known) return known;
  // "compliance.permanent_dnc" -> "Compliance permanent dnc". Not elegant,
  // but it is words, and the alternative is a column name.
  const words = value.replace(/[._]/g, ' ').trim();
  return words ? words.charAt(0).toUpperCase() + words.slice(1) : 'Platform change';
}
