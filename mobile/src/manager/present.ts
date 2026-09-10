/**
 * THE MANAGER PAYLOAD, TURNED INTO PRODUCT LANGUAGE.
 *
 * WHY THIS FILE EXISTS. The screens were reading fields the server does not
 * send, and falling back to fields it does send but never meant for display.
 * Three separate symptoms, one cause:
 *
 *   "Deal / video_failed"   — the row asked for `reason`, which does not exist,
 *                             then fell back to `kind`, which is a database
 *                             enum. `title` ("Video meeting failed") and
 *                             `detail` ("Zoom returned 401") were sitting in
 *                             the same object, unread.
 *
 *   "Appointment / —"       — `team_today` is `{people: [...]}`, not a list of
 *                             meetings. `asList`'s last-resort "first array
 *                             property" found `people`, so the screen rendered
 *                             one card per TEAM MEMBER and called each of them
 *                             an appointment.
 *
 *   "BY STAGE" empty        — the projection payload has no `by_stage` key at
 *                             all. It has `deals`, each carrying `stage`.
 *
 * So this module is deliberately pure and deliberately dumb: it takes the
 * payload the server actually returns and produces the fields a card actually
 * draws. It computes no money the server did not compute (a weighted forecast
 * calculated on a phone is a second forecast) and invents no data — a field
 * the server did not send comes back null, and the screen omits that line
 * rather than printing a dash where a fact should be.
 */

import { attentionKindLabel, humanText, meetingKindLabel, roleLabel, stageLabel }
  from '../vocab';
import { dedupeBy } from '../keys';

type Rec = Record<string, unknown>;

const str = (v: unknown): string | null => {
  if (typeof v === 'string' && v.trim()) return v.trim();
  if (typeof v === 'number') return String(v);
  return null;
};
const num = (v: unknown): number | null => {
  if (v === null || v === undefined || v === '') return null;
  const n = typeof v === 'number' ? v : Number(v);
  return Number.isFinite(n) ? n : null;
};

// ── attention ───────────────────────────────────────────────────────────────

export type AttentionCard = {
  /** Stable across renders and unique even when one deal raises two rows. */
  key: string;
  /** WHICH deal. */
  subject: string | null;
  /** WHAT happened, in words. */
  title: string;
  /** WHY, from the server's own sentence. */
  why: string | null;
  /** WHO owns it, and how urgent — assembled, may be null. */
  who: string | null;
  /** WHAT to do, the server's verb. */
  action: string | null;
  urgent: boolean;
  opportunityId: string | null;
  kind: string | null;
};

export function attentionCards(items: Rec[]): AttentionCard[] {
  return items.map((a, i) => {
    const kind = str(a.kind);
    // `title` FIRST. The server writes it for a human; `kind` is the fallback
    // for a payload that predates it, and `attentionKindLabel` guarantees even
    // that fallback is words rather than an identifier.
    const title = humanText(str(a.title), attentionKindLabel(kind));
    const owner = str(a.owner_name);
    const stage = str(a.stage_label) ?? (a.stage ? stageLabel(str(a.stage)) : null);
    const value = num(a.deal_value);

    const whoParts = [
      owner,
      stage,
      value !== null && value > 0
        ? value.toLocaleString(undefined,
            { style: 'currency', currency: 'USD', maximumFractionDigits: 0 })
        : null,
    ].filter(Boolean) as string[];

    return {
      // Every discriminator, because one deal legitimately produces several
      // rows and one proposal legitimately produces several kinds.
      key: [kind, str(a.opportunity_id), str(a.proposal_id),
            str(a.appointment_id)]
        .filter(Boolean).join('|') + `#${i}`,
      subject: str(a.company),
      title,
      why: humanText(str(a.detail), '') || null,
      who: whoParts.length ? whoParts.join(' · ') : null,
      action: humanText(str(a.action), '') || null,
      urgent: str(a.level) === 'red',
      opportunityId: str(a.opportunity_id),
      kind,
    };
  });
}

// ── team today ──────────────────────────────────────────────────────────────

export type MeetingCard = {
  key: string;
  /** The meeting type — "Discovery", "Demo" — never a bare "Appointment". */
  title: string;
  /** The customer or prospect it is with. */
  subject: string | null;
  /** Everyone from this team who is on it. */
  attendees: string[];
  startsAt: unknown;
  durationMinutes: number | null;
  confirmationStatus: string | null;
  videoNeedsAttention: boolean;
  appointmentId: string | null;
  opportunityId: string | null;
};

/**
 * Flatten `team_today.people[].meetings[]` into one agenda.
 *
 * A meeting with two of this brand's people on it appears under BOTH of them,
 * so the same appointment id arrives twice. It is collapsed to one card that
 * names both attendees — a manager wants one line per meeting, and rendering
 * it twice would also have produced duplicate React keys.
 */
export function meetingCards(teamToday: Rec | null | undefined): MeetingCard[] {
  const people = Array.isArray(teamToday?.people)
    ? ((teamToday as Rec).people as Rec[]) : [];

  const attendeesByAppt = new Map<string, string[]>();
  const flat: Rec[] = [];
  for (const person of people) {
    const who = str(person.name);
    const meetings = Array.isArray(person.meetings) ? (person.meetings as Rec[]) : [];
    for (const m of meetings) {
      const id = str(m.id);
      if (id && who) {
        const list = attendeesByAppt.get(id) ?? [];
        if (!list.includes(who)) list.push(who);
        attendeesByAppt.set(id, list);
      }
      flat.push(m);
    }
  }

  const unique = dedupeBy(flat, (m) => str(m.id));
  unique.sort((a, b) =>
    String(a.starts_at ?? '').localeCompare(String(b.starts_at ?? '')));

  return unique.map((m, i) => {
    const id = str(m.id);
    // The meeting TYPE is the useful headline. `title` is free text a rep may
    // never have filled in, which is why every card said "Appointment".
    const title = humanText(str(m.meeting_type), '')
      || humanText(str(m.title), '')
      || meetingKindLabel(str(m.kind));
    return {
      key: [id, str(m.opportunity_id)].filter(Boolean).join('|') + `#${i}`,
      title,
      subject: str(m.company),
      attendees: id ? (attendeesByAppt.get(id) ?? []) : [],
      startsAt: m.starts_at ?? null,
      durationMinutes: num(m.duration_minutes),
      confirmationStatus: str(m.confirmation_status),
      videoNeedsAttention: m.video_needs_attention === true,
      appointmentId: id,
      opportunityId: str(m.opportunity_id),
    };
  });
}

// ── reps ────────────────────────────────────────────────────────────────────

export type RepCard = {
  key: string;
  userId: string | null;
  name: string;
  /** "Sales Representative", never "sales_rep". */
  role: string;
  openDeals: number | null;
  /** THE MONEY BADGE, NAMED. `rep_rollup` computes it as
   *  `sum(deal_value for open opportunities owned by this rep)`, so it is OPEN
   *  PIPELINE — not revenue, not production, not anything closed. It was
   *  rendered as a bare dollar chip, which is how $2,000,000 of unclosed
   *  pipeline reads as somebody's sales figure. */
  openPipeline: number | null;
  needsAttention: number | null;
  overdueActions: number | null;
  meetingsToday: number | null;
  lastActivity: string | null;
};

export function repCards(reps: Rec[]): RepCard[] {
  return reps.map((r, i) => ({
    key: [str(r.user_id), str(r.id)].filter(Boolean).join('|') + `#${i}`,
    userId: str(r.user_id) ?? str(r.id),
    name: str(r.name) ?? str(r.full_name) ?? 'Team member',
    // `role_label` is the server's own phrase; `roleLabel` covers a payload
    // that only carries the code.
    role: humanText(str(r.role_label), '') || roleLabel(str(r.role)),
    openDeals: num(r.open_deals),
    openPipeline: num(r.pipeline_value),
    needsAttention: num(r.needs_attention),
    overdueActions: num(r.overdue_actions),
    meetingsToday: num(r.meetings_today),
    lastActivity: str(r.last_recorded_activity_ago),
  }));
}

// ── pipeline ────────────────────────────────────────────────────────────────

export type StageGroup = {
  key: string;
  stage: string;
  label: string;
  count: number;
  value: number;
};

/**
 * Group the projection's deals by stage.
 *
 * THE PAYLOAD HAS NO `by_stage`. The screen asked for one, found nothing, and
 * rendered an empty section under a heading — which reads as "this team has no
 * pipeline" rather than "this screen asked the wrong question". The deals are
 * right there, each with a stage, so the grouping happens here.
 *
 * `fixed_contract_value` is the figure summed, because it is the same one the
 * server's own `pipeline_value` total is built from — a section whose parts do
 * not add up to the headline above it is worse than no section.
 */
export function stageGroups(deals: Rec[], order: readonly string[]): StageGroup[] {
  const byStage = new Map<string, { count: number; value: number }>();
  for (const d of deals) {
    const stage = str(d.stage) ?? 'unknown';
    const entry = byStage.get(stage) ?? { count: 0, value: 0 };
    entry.count += 1;
    entry.value += num(d.fixed_contract_value) ?? num(d.total_contract_value) ?? 0;
    byStage.set(stage, entry);
  }

  const out: StageGroup[] = [];
  // Canonical order first, so the funnel reads top to bottom the way it does
  // on the desktop...
  for (const stage of order) {
    const e = byStage.get(stage);
    if (!e) continue;
    out.push({ key: stage, stage, label: stageLabel(stage), count: e.count, value: e.value });
    byStage.delete(stage);
  }
  // ...then anything the server knows and this build does not, rather than
  // dropping deals out of a total silently.
  for (const [stage, e] of byStage) {
    out.push({ key: stage, stage, label: stageLabel(stage), count: e.count, value: e.value });
  }
  return out;
}

export type PipelineDealCard = {
  key: string;
  opportunityId: string | null;
  company: string;
  stage: string;
  value: number | null;
  /** Set when the server could not establish the deal's recurring terms. The
   *  value shown is then a floor, and saying so is the difference between an
   *  understated number and a wrong one. */
  incompleteReason: string | null;
  probabilityPct: number | null;
};

export function pipelineDealCards(deals: Rec[]): PipelineDealCard[] {
  return deals.map((d, i) => ({
    // `opportunity_id`, not `id` — the projection row has no `id`, which is
    // why every deal row used to be keyed "undefined".
    key: (str(d.opportunity_id) ?? '') + `#${i}`,
    opportunityId: str(d.opportunity_id),
    company: str(d.company_name) ?? 'Unnamed deal',
    stage: stageLabel(str(d.stage)),
    value: num(d.fixed_contract_value) ?? num(d.total_contract_value),
    incompleteReason: d.pricing_complete === false
      ? (humanText(str(d.incomplete_reason), '') || 'Pricing incomplete')
      : null,
    probabilityPct: num(d.probability_pct),
  }));
}

/**
 * What to write next to "Weighted".
 *
 * The server sends `weighted_pipeline_value: null` with
 * `weighted_available: false` when nobody has configured stage probabilities —
 * deliberately null rather than zero, because zero reads as "this pipeline is
 * worth nothing". The screen turned that null into "—", which is the same
 * failure with fewer characters.
 */
export function weightedDisplay(payload: Rec): { value: number | null; note: string } {
  const available = payload.weighted_available === true;
  const value = num(payload.weighted_pipeline_value);
  if (available && value !== null) {
    return {
      value,
      note: 'Weighted by the stage probabilities configured for this brand. '
          + 'Calculated on the server, never on the phone.',
    };
  }
  return {
    value: null,
    note: 'Stage probabilities are not configured for this brand, so a '
        + 'weighted forecast cannot be produced.',
  };
}
