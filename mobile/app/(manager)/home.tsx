/**
 * SALES MANAGER · TODAY — the team's day, what is stuck, what needs a decision.
 *
 * ONE REQUEST, ONE MOMENT. `/sales/manager/overview` is deliberately a single
 * endpoint — the router says so in its own docstring: six endpoints would give
 * six loading states and six slightly different "now", so a meeting could
 * appear in Team Today and be missing from the rep rollup drawn a second later.
 * This screen honours that and does not go and fetch the pieces separately.
 *
 * WHAT PASS 2 FIXED HERE, and why each was the same mistake:
 *
 *   1. DUPLICATE KEYS. Attention rows were keyed on `opportunity_id`, and the
 *      server raises one row PER PROBLEM. A deal with an overdue action and a
 *      failed video produced two rows with one key — React warned, and then
 *      reconciled them as a single row.
 *
 *   2. RAW ENUMS ON SCREEN. The card read `pick(a, 'reason', 'kind', ...)`.
 *      There is no `reason` field, so it fell through to `kind` and printed
 *      "video_failed" under the word "Deal". The server was already sending
 *      `title: "Video meeting failed"`, `detail`, `action` and `company` — the
 *      screen simply never asked for them.
 *
 *   3. FOUR CARDS SAYING "Appointment / —". `team_today` is
 *      `{people: [...]}`, not a list of meetings, so `asList`'s last-resort
 *      "first array property" returned the TEAM MEMBERS and drew one
 *      appointment card per person.
 *
 * All three are now decided in `src/manager/present.ts`, which is pure and
 * tested against the payload shapes the routers actually return.
 *
 * A MANAGER IS NOT A SMALL OWNER. Nothing here is platform-wide: every figure
 * is this brand's, resolved by `require_sales_manager` against a membership. A
 * manager who opens this cannot see another brand's team by any route the app
 * offers, and asking would be refused server-side rather than filtered here.
 */

import React from 'react';
import { router } from 'expo-router';

import { manager } from '../../src/api/endpoints';
import { asList, asMoney, pick, useScopedQuery, useScopedRefresh } from '../../src/hooks/useApi';
import { numField } from '../../src/api/state';
import { useActiveExperience } from '../../src/experience/ExperienceContext';
import {
  AttentionItem, Metric, MetricGrid, SectionRetry, Skeleton,
} from '../../src/components/data';
import {
  EmptyState, Pill, Row, Screen, ScreenTitle, SectionHeader,
} from '../../src/components/ui';
import { nameOf, relativeOf, timeOf } from '../../src/format';
import { attentionCards, meetingCards, repCards } from '../../src/manager/present';
import { rowKey } from '../../src/keys';
import { CONFIRMATION_LABELS } from '../../src/vocab';
import { palette } from '../../src/theme/tokens';

type Rec = Record<string, unknown>;

export default function ManagerToday() {
  const exp = useActiveExperience();
  const brand = exp.brandSalesOrgId;
  const refresh = useScopedRefresh();

  const q = useScopedQuery(['manager', 'overview', brand], () =>
    manager.overview({ brand_sales_org_id: brand }));

  const payload = (q.data ?? {}) as Rec;

  const attentionTotal = numField(q, 'attention.total');
  const attentionRed = numField(q, 'attention.red');
  const approvalsPending = numField(q, 'approvals.pending_count');
  const teamSize = numField(q, 'team.length');

  // `team_today` is an OBJECT with a `people` array, so it is read by path
  // rather than handed to asList — see present.ts.
  const meetings = meetingCards(pick<Rec>(payload, 'team_today'));
  const attention = attentionCards(asList<Rec>(pick(payload, 'attention.items'), 'items'));
  const pendingApprovals = asList<Rec>(pick(payload, 'approvals.pending'), 'items');
  const reps = repCards(asList<Rec>(pick(payload, 'reps'), 'items'));
  const closing = asList<Rec>(pick(payload, 'closing_pipeline'), 'items', 'deals');

  const team = asList<Rec>(pick(payload, 'team'), 'items');

  return (
    <Screen refreshing={q.isFetching} onRefresh={refresh}>
      <ScreenTitle
        title="Today"
        subtitle={String(pick(payload, 'brand_name') ?? exp.detail ?? 'Your team')}
      />

      <SectionRetry
        show={q.isError}
        onRetry={refresh}
        note="The team overview could not be loaded."
      />

      {q.isLoading ? <Skeleton rows={3} /> : null}

      <MetricGrid>
        <Metric
          label="Needs intervention"
          field={attentionTotal}
          hint={attentionRed.value ? `${attentionRed.value} urgent` : undefined}
        />
        <Metric
          label="Approvals waiting"
          field={approvalsPending}
          onPress={() => router.push('/(manager)/approvals' as never)}
        />
        <Metric
          label="Team"
          field={team.length ? { state: 'ready', value: team.length } : teamSize}
          onPress={() => router.push('/(manager)/team' as never)}
        />
        <Metric
          label="Meetings today"
          field={{ state: meetings.length ? 'ready' : 'zero', value: meetings.length }}
        />
      </MetricGrid>

      {/* ── APPROVALS FIRST: a manager is the only person who can clear these ─ */}
      {pendingApprovals.length ? (
        <>
          <SectionHeader title={`Waiting on you · ${pendingApprovals.length}`} />
          {pendingApprovals.slice(0, 5).map((a, i) => (
            <AttentionItem
              key={rowKey([pick(a, 'id'), pick(a, 'opportunity_id')], i)}
              subject={String(pick(a, 'company_name', 'opportunity_name') ?? '') || null}
              title="Pricing approval requested"
              why={String(pick(a, 'reason', 'summary', 'detail') ?? '') || null}
              who={String(pick(a, 'requested_by_name', 'requester_name') ?? '') || null}
              action="Review and decide"
              tone="danger"
              onPress={() => router.push('/(manager)/approvals' as never)}
            />
          ))}
        </>
      ) : null}

      {/* ── EXCEPTIONS THE SERVER RAISED ──────────────────────────────────── */}
      {attention.length ? (
        <>
          <SectionHeader title={`Needs intervention · ${attention.length}`} />
          {attention.slice(0, 8).map((a) => (
            <AttentionItem
              key={a.key}
              subject={a.subject}
              title={a.title}
              why={a.why}
              who={a.who}
              action={a.action ?? 'Open the deal'}
              tone={a.urgent ? 'danger' : 'warning'}
              onPress={a.opportunityId
                ? () => router.push(`/opportunity/${a.opportunityId}` as never)
                : undefined}
            />
          ))}
        </>
      ) : null}

      {/* ── TEAM TODAY ────────────────────────────────────────────────────── */}
      {meetings.length ? (
        <>
          <SectionHeader title={`Team today · ${meetings.length}`} />
          {meetings.map((m) => (
            <Row
              key={m.key}
              title={m.title}
              subtitle={[m.subject, m.attendees.join(', ') || null]
                .filter(Boolean).join(' · ') || null}
              meta={[
                timeOf(m.startsAt),
                m.durationMinutes ? `${m.durationMinutes} min` : null,
              ].filter(Boolean).join(' · ')}
              accent={m.videoNeedsAttention ? palette.danger : palette.accent}
              onPress={m.appointmentId
                ? () => router.push(`/appointment/${m.appointmentId}` as never)
                : undefined}
              right={m.videoNeedsAttention
                ? <Pill label="Video problem" tone="danger" />
                : m.confirmationStatus && m.confirmationStatus !== 'confirmed'
                  ? <Pill
                      label={CONFIRMATION_LABELS[m.confirmationStatus] ?? 'Unconfirmed'}
                      tone="warning"
                    />
                  : <Pill label="Confirmed" tone="positive" />}
            />
          ))}
        </>
      ) : null}

      {/* ── CLOSING SOON ──────────────────────────────────────────────────── */}
      {closing.length ? (
        <>
          <SectionHeader title="Closing soon" />
          {closing.slice(0, 6).map((d, i) => (
            <Row
              key={rowKey([pick(d, 'opportunity_id'), pick(d, 'id')], i)}
              title={nameOf(d, 'Deal')}
              subtitle={String(pick(d, 'owner_name', 'salesperson_name') ?? '') || null}
              meta={relativeOf(pick(d, 'expected_close_date', 'close_date'))}
              onPress={() => {
                const id = pick(d, 'opportunity_id', 'id');
                if (id) router.push(`/opportunity/${String(id)}` as never);
              }}
              right={pick(d, 'amount', 'deal_value') != null
                ? <Pill label={asMoney(pick(d, 'amount', 'deal_value'))} tone="accent" />
                : undefined}
            />
          ))}
        </>
      ) : null}

      {/* ── REP ROLLUP ────────────────────────────────────────────────────── */}
      {reps.length ? (
        <>
          <SectionHeader title="Team activity" />
          {reps.map((r) => (
            <Row
              key={r.key}
              title={r.name}
              subtitle={[
                r.openDeals !== null ? `${r.openDeals} open` : null,
                r.needsAttention ? `${r.needsAttention} need attention` : null,
                r.meetingsToday ? `${r.meetingsToday} today` : null,
              ].filter(Boolean).join(' · ') || r.role}
              meta={r.lastActivity}
              onPress={() => router.push('/(manager)/team' as never)}
            />
          ))}
        </>
      ) : null}

      {!q.isLoading && !pendingApprovals.length && !attention.length
        && !meetings.length ? (
        <EmptyState
          title="Quiet day"
          body="No approvals waiting, nothing flagged for intervention, and no team meetings scheduled today."
        />
      ) : null}
    </Screen>
  );
}
