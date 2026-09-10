/**
 * THE FOUR DEFECTS MIKE PHOTOGRAPHED, PINNED.
 *
 * Every fixture below is the shape the Python actually returns — copied from
 * `manager_workspace._item()`, `manager_workspace.team_today()`,
 * `manager_workspace.rep_rollup()` and `pipeline_projection.project()`. A test
 * written against an invented payload would have passed while the app was
 * broken, which is exactly how these four shipped.
 */

import {
  attentionCards, meetingCards, pipelineDealCards, repCards, stageGroups,
  weightedDisplay,
} from '../manager/present';
import { dedupeBy, rowKey } from '../keys';
import { looksLikeCode } from '../vocab';
import { OPEN_STAGES } from '../vocab';

// ── fixtures, from app/services/manager_workspace.py ────────────────────────

/** One company with TWO problems — the exact case that duplicated a key. */
const ATTENTION = [
  {
    kind: 'video_failed', level: 'red',
    title: 'Video meeting failed', detail: 'Zoom returned 401.',
    action: 'Retry the video link before the call',
    opportunity_id: 'opp-1', company: 'Walmart', stage: 'closing',
    stage_label: 'Closing', owner_user_id: 'u1', owner_name: 'John Smith',
    deal_value: 120000, proposal_id: null, appointment_id: 'appt-9',
  },
  {
    kind: 'overdue_action', level: 'amber',
    title: 'Next action is overdue', detail: 'Send the revised scope.',
    action: 'Reset the date or do it',
    opportunity_id: 'opp-1', company: 'Walmart', stage: 'closing',
    stage_label: 'Closing', owner_user_id: 'u1', owner_name: 'John Smith',
    deal_value: 120000, proposal_id: null, appointment_id: null,
  },
  {
    kind: 'proposal_unopened', level: 'amber',
    title: 'Sent and never opened', detail: 'Sent 6 days ago. No portal activity.',
    action: 'Check the address, or call',
    opportunity_id: 'opp-2', company: 'Building Equity Investments LLC',
    stage: 'demo_proposal', stage_label: 'Demo & Proposal',
    owner_user_id: 'u2', owner_name: 'Blake Rehani',
    deal_value: 1497, proposal_id: 'prop-3', appointment_id: null,
  },
];

describe('the duplicate-key defect', () => {
  it('gives two problems on ONE deal two different keys', () => {
    const cards = attentionCards(ATTENTION);
    const keys = cards.map((c) => c.key);
    expect(new Set(keys).size).toBe(keys.length);
  });

  it('keeps keys unique even when the server sends no ids at all', () => {
    const nameless = [{ kind: 'stalled' }, { kind: 'stalled' }, { kind: 'stalled' }];
    const keys = attentionCards(nameless).map((c) => c.key);
    expect(new Set(keys).size).toBe(3);
  });

  it('rowKey never collides for rows sharing every discriminator', () => {
    const keys = [0, 1, 2].map((i) => rowKey([null, undefined, ''], i));
    expect(new Set(keys).size).toBe(3);
  });

  it('rowKey uses the discriminators when they exist', () => {
    expect(rowKey(['video_failed', 'opp-1'], 0)).toContain('video_failed');
    expect(rowKey(['video_failed', 'opp-1'], 0)).toContain('opp-1');
  });
});

describe('no raw backend code reaches a manager', () => {
  it('renders the SERVER title, not the kind enum', () => {
    const [first] = attentionCards(ATTENTION);
    expect(first.title).toBe('Video meeting failed');
    expect(first.title).not.toBe('video_failed');
  });

  it('has no card whose visible text looks like an identifier', () => {
    for (const c of attentionCards(ATTENTION)) {
      expect(looksLikeCode(c.title)).toBe(false);
      expect(looksLikeCode(c.why ?? '')).toBe(false);
      expect(looksLikeCode(c.action ?? '')).toBe(false);
      expect(looksLikeCode(c.subject ?? '')).toBe(false);
      expect(looksLikeCode(c.who ?? '')).toBe(false);
    }
  });

  it('falls back to a phrase — never the code — when title is missing', () => {
    const [c] = attentionCards([{ kind: 'proposal_unopened', level: 'amber' }]);
    expect(c.title).toBe('Proposal never opened');
  });

  it('refuses a title that is itself a code', () => {
    const [c] = attentionCards([{ kind: 'overdue_action', title: 'overdue_action' }]);
    expect(c.title).toBe('Follow-up overdue');
  });

  it('answers all five questions a manager asks of a card', () => {
    const [c] = attentionCards(ATTENTION);
    expect(c.subject).toBe('Walmart');                    // which customer
    expect(c.title).toBeTruthy();                         // what happened
    expect(c.who).toContain('John Smith');                // who owns it
    expect(c.urgent).toBe(true);                          // how urgent
    expect(c.action).toBeTruthy();                        // what can I do
    expect(c.opportunityId).toBe('opp-1');                // and where it goes
  });
});

// ── team today ──────────────────────────────────────────────────────────────

/** `team_today` is an OBJECT. The screen treated it as a list and rendered the
 *  team members as appointments. */
const TEAM_TODAY = {
  date: '2026-09-10', timezone: 'America/Chicago', total_meetings: 2,
  unconfirmed: 1,
  by_kind: { discovery: 1, demo: 1, proposal: 0, closing: 0, internal: 0, other: 0 },
  working_today: 2,
  people: [
    {
      user_id: 'u1', name: 'John Smith', role: 'sales_rep',
      role_label: 'Sales Representative', meeting_count: 2, clear: false,
      meetings: [
        {
          id: 'appt-1', title: null, meeting_type: 'Discovery Call',
          kind: 'discovery', starts_at: '2026-09-10T15:00:00Z',
          duration_minutes: 30, opportunity_id: 'opp-1', company: 'Walmart',
          confirmation_status: 'confirmed', join_url: 'https://x',
          video_needs_attention: false,
        },
        {
          id: 'appt-2', title: null, meeting_type: 'Demo',
          kind: 'demo', starts_at: '2026-09-10T18:00:00Z',
          duration_minutes: 60, opportunity_id: 'opp-2',
          company: 'Building Equity Investments LLC',
          confirmation_status: 'pending', join_url: null,
          video_needs_attention: true,
        },
      ],
    },
    {
      user_id: 'u2', name: 'Blake Rehani', role: 'sales_manager',
      role_label: 'Sales Manager', meeting_count: 1, clear: false,
      // THE SAME MEETING, because Blake is also on it.
      meetings: [
        {
          id: 'appt-1', title: null, meeting_type: 'Discovery Call',
          kind: 'discovery', starts_at: '2026-09-10T15:00:00Z',
          duration_minutes: 30, opportunity_id: 'opp-1', company: 'Walmart',
          confirmation_status: 'confirmed', join_url: 'https://x',
          video_needs_attention: false,
        },
      ],
    },
    { user_id: 'u3', name: 'Mike Simmons', role: 'sales_rep',
      role_label: 'Sales Representative', meeting_count: 0, clear: true,
      meetings: [] },
  ],
};

describe('the "Appointment / —" defect', () => {
  it('reads the meetings, not the people', () => {
    const cards = meetingCards(TEAM_TODAY);
    // Two real meetings among three people — the old code drew three cards.
    expect(cards).toHaveLength(2);
  });

  it('never titles a card with the bare word Appointment', () => {
    for (const c of meetingCards(TEAM_TODAY)) {
      expect(c.title).not.toBe('Appointment');
      expect(c.title).toBeTruthy();
    }
    expect(meetingCards(TEAM_TODAY)[0].title).toBe('Discovery Call');
  });

  it('names the customer, the time and the people on it', () => {
    const [first] = meetingCards(TEAM_TODAY);
    expect(first.subject).toBe('Walmart');
    expect(first.startsAt).toBe('2026-09-10T15:00:00Z');
    expect(first.durationMinutes).toBe(30);
    expect(first.attendees.sort()).toEqual(['Blake Rehani', 'John Smith']);
  });

  it('shows a meeting attended by two of the team ONCE', () => {
    const ids = meetingCards(TEAM_TODAY).map((c) => c.appointmentId);
    expect(ids.filter((i) => i === 'appt-1')).toHaveLength(1);
  });

  it('gives every meeting card a unique key', () => {
    const keys = meetingCards(TEAM_TODAY).map((c) => c.key);
    expect(new Set(keys).size).toBe(keys.length);
  });

  it('orders the agenda by start time', () => {
    const times = meetingCards(TEAM_TODAY).map((c) => String(c.startsAt));
    expect([...times]).toEqual([...times].sort());
  });

  it('survives an empty or missing payload without inventing rows', () => {
    expect(meetingCards(null)).toEqual([]);
    expect(meetingCards({})).toEqual([]);
    expect(meetingCards({ people: [] })).toEqual([]);
  });

  it('carries the video warning through', () => {
    const demo = meetingCards(TEAM_TODAY).find((c) => c.title === 'Demo');
    expect(demo?.videoNeedsAttention).toBe(true);
  });

  it('dedupeBy keeps rows that have no identity rather than dropping them', () => {
    const rows = [{ id: 'a' }, { id: null }, { id: 'a' }, { id: null }];
    expect(dedupeBy(rows, (r) => r.id)).toHaveLength(3);
  });
});

// ── reps ────────────────────────────────────────────────────────────────────

const REPS = [
  {
    user_id: 'u2', name: 'Blake Rehani', email: 'b@x.com',
    role: 'sales_manager', role_label: 'Sales Manager',
    open_deals: 3, needs_attention: 2, overdue_actions: 1, meetings_today: 1,
    demos_to_build: 0, proposals_awaiting_send: 1, proposals_with_customer: 0,
    pipeline_value: 1497, last_recorded_activity_ago: '2 hours ago',
  },
  {
    user_id: 'u4', name: 'Michael Schlueter', email: 'm@x.com',
    role: 'sales_rep', role_label: 'Sales Representative',
    open_deals: 1, needs_attention: 0, overdue_actions: 0, meetings_today: 0,
    pipeline_value: 2000000, last_recorded_activity_ago: '6 days ago',
  },
  // No role_label — an older payload. Must still not render "sales_rep".
  { user_id: 'u3', name: 'Mike Simmons', role: 'sales_rep',
    open_deals: 0, needs_attention: 0, pipeline_value: 0 },
];

describe('the unlabelled money badge', () => {
  it('reads pipeline_value, which rep_rollup defines as OPEN PIPELINE', () => {
    const cards = repCards(REPS);
    expect(cards.map((c) => c.openPipeline)).toEqual([1497, 2000000, 0]);
  });

  it('keeps a real zero as zero and never as missing', () => {
    const mike = repCards(REPS).find((c) => c.name === 'Mike Simmons');
    expect(mike?.openPipeline).toBe(0);
  });

  it('reports an absent figure as null rather than zero', () => {
    const [c] = repCards([{ user_id: 'u9', name: 'New Person' }]);
    expect(c.openPipeline).toBeNull();
    expect(c.openDeals).toBeNull();
  });

  it('never shows a raw role code', () => {
    for (const c of repCards(REPS)) {
      expect(looksLikeCode(c.role)).toBe(false);
    }
    expect(repCards(REPS)[2].role).toBe('Sales Representative');
  });

  it('reads the counts the rollup actually sends', () => {
    const [blake] = repCards(REPS);
    expect(blake.openDeals).toBe(3);
    expect(blake.needsAttention).toBe(2);
    expect(blake.overdueActions).toBe(1);
    expect(blake.meetingsToday).toBe(1);
    expect(blake.lastActivity).toBe('2 hours ago');
  });

  it('gives every rep row a unique key', () => {
    const keys = repCards(REPS).map((c) => c.key);
    expect(new Set(keys).size).toBe(keys.length);
  });
});

// ── pipeline ────────────────────────────────────────────────────────────────

/** From pipeline_projection.project(include_deals=True). Note: NO `by_stage`,
 *  and the deal rows have `opportunity_id`, never `id`. */
const PROJECTION = {
  basis: 'projected',
  opportunity_count: 4,
  pipeline_value: 2121497,
  pipeline_total_fixed_contract_value: 2121497,
  weighted_pipeline_value: null,
  weighted_available: false,
  pricing_incomplete_count: 1,
  compensation_plan_configured: true,
  deals: [
    { opportunity_id: 'opp-1', company_name: 'Walmart', stage: 'closing',
      fixed_contract_value: 120000, pricing_complete: true,
      incomplete_reason: null, probability_pct: 70 },
    { opportunity_id: 'opp-2', company_name: 'Building Equity Investments LLC',
      stage: 'demo_proposal', fixed_contract_value: 1497,
      pricing_complete: true, incomplete_reason: null, probability_pct: 40 },
    { opportunity_id: 'opp-3', company_name: 'Acme Fibre', stage: 'prospect',
      fixed_contract_value: 2000000, pricing_complete: false,
      incomplete_reason: 'No term agreed', probability_pct: null },
    { opportunity_id: 'opp-4', company_name: 'Second Prospect', stage: 'prospect',
      fixed_contract_value: 0, pricing_complete: true,
      incomplete_reason: null, probability_pct: null },
  ],
};

describe('the empty "BY STAGE" section', () => {
  it('groups the deals the payload does carry', () => {
    const groups = stageGroups(PROJECTION.deals, OPEN_STAGES);
    expect(groups.length).toBeGreaterThan(0);
  });

  it('counts and totals each stage', () => {
    const groups = stageGroups(PROJECTION.deals, OPEN_STAGES);
    const prospect = groups.find((g) => g.stage === 'prospect');
    expect(prospect?.count).toBe(2);
    expect(prospect?.value).toBe(2000000);
    expect(groups.find((g) => g.stage === 'closing')?.count).toBe(1);
  });

  it('adds up to the headline the server sent', () => {
    const groups = stageGroups(PROJECTION.deals, OPEN_STAGES);
    const summed = groups.reduce((t, g) => t + g.value, 0);
    expect(summed).toBe(PROJECTION.pipeline_total_fixed_contract_value);
  });

  it('renders stages in the canonical funnel order', () => {
    const groups = stageGroups(PROJECTION.deals, OPEN_STAGES);
    const order = groups.map((g) => g.stage);
    expect(order.indexOf('prospect')).toBeLessThan(order.indexOf('demo_proposal'));
    expect(order.indexOf('demo_proposal')).toBeLessThan(order.indexOf('closing'));
  });

  it('never drops a deal whose stage this build does not know', () => {
    const groups = stageGroups(
      [...PROJECTION.deals, { opportunity_id: 'x', stage: 'renegotiation',
                              fixed_contract_value: 500 }],
      OPEN_STAGES);
    const summed = groups.reduce((t, g) => t + g.value, 0);
    expect(summed).toBe(PROJECTION.pipeline_total_fixed_contract_value + 500);
    expect(groups.some((g) => g.stage === 'renegotiation')).toBe(true);
  });

  it('uses a human label for every group', () => {
    for (const g of stageGroups(PROJECTION.deals, OPEN_STAGES)) {
      expect(looksLikeCode(g.label)).toBe(false);
    }
  });

  it('gives every group a unique key', () => {
    const keys = stageGroups(PROJECTION.deals, OPEN_STAGES).map((g) => g.key);
    expect(new Set(keys).size).toBe(keys.length);
  });
});

describe('the unexplained weighted dash', () => {
  it('says probabilities are not configured instead of showing a dash', () => {
    const w = weightedDisplay(PROJECTION);
    expect(w.value).toBeNull();
    expect(w.note).toMatch(/not configured/i);
  });

  it('shows the SERVER figure when one exists, and never computes its own', () => {
    const w = weightedDisplay({
      ...PROJECTION, weighted_available: true, weighted_pipeline_value: 654321,
    });
    expect(w.value).toBe(654321);
    expect(w.note).toMatch(/server/i);
  });

  it('treats an available-but-null figure as not configured, not as zero', () => {
    const w = weightedDisplay({ weighted_available: true, weighted_pipeline_value: null });
    expect(w.value).toBeNull();
    expect(w.note).toMatch(/not configured/i);
  });

  it('never turns a missing weighted value into 0', () => {
    expect(weightedDisplay({}).value).not.toBe(0);
  });
});

describe('pipeline deal rows', () => {
  it('keys on opportunity_id, which is the field the payload has', () => {
    const cards = pipelineDealCards(PROJECTION.deals);
    expect(cards[0].key).toContain('opp-1');
    expect(new Set(cards.map((c) => c.key)).size).toBe(cards.length);
  });

  it('keeps keys unique even when every id is missing', () => {
    const cards = pipelineDealCards([{ stage: 'prospect' }, { stage: 'prospect' }]);
    expect(new Set(cards.map((c) => c.key)).size).toBe(2);
  });

  it('names the company and the stage in words', () => {
    const [c] = pipelineDealCards(PROJECTION.deals);
    expect(c.company).toBe('Walmart');
    expect(c.stage).toBe('Closing');
  });

  it('flags a deal whose pricing the server could not complete', () => {
    const acme = pipelineDealCards(PROJECTION.deals)
      .find((c) => c.company === 'Acme Fibre');
    expect(acme?.incompleteReason).toBe('No term agreed');
  });

  it('leaves a complete deal unflagged', () => {
    const [c] = pipelineDealCards(PROJECTION.deals);
    expect(c.incompleteReason).toBeNull();
  });
});
