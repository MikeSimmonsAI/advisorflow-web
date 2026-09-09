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
