/**
 * SALES MANAGER · TODAY — the team's day, what is stuck, what needs a decision.
 *
 * ONE REQUEST, ONE MOMENT. `/sales/manager/overview` is deliberately a single
 * endpoint — the router says so in its own docstring: six endpoints would give
 * six loading states and six slightly different "now", so a meeting could
 * appear in Team Today and be missing from the rep rollup drawn a second later.
 * This screen honours that and does not go and fetch the pieces separately.
 *
 * Its real shape is:
 *
 *     team · team_today · attention { items, total, red, by_kind, by_owner }
 *     approvals { pending, pending_count, recent } · closing_pipeline
 *     reps · proposal_queues
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

  const teamToday = asList<Rec>(pick(payload, 'team_today'), 'items', 'appointments');
  const attentionItems = asList<Rec>(pick(payload, 'attention.items'), 'items');
  const pendingApprovals = asList<Rec>(pick(payload, 'approvals.pending'), 'items');
  const reps = asList<Rec>(pick(payload, 'reps'), 'items');
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
          field={{ state: teamToday.length ? 'ready' : 'zero', value: teamToday.length }}
        />
      </MetricGrid>

      {/* ── APPROVALS FIRST: a manager is the only person who can clear these ─ */}
      {pendingApprovals.length ? (
        <>
          <SectionHeader title={`Waiting on you · ${pendingApprovals.length}`} />
          {pendingApprovals.slice(0, 5).map((a, i) => (
            <AttentionItem
              key={String(pick(a, 'id') ?? i)}
              title={String(pick(a, 'opportunity_name', 'company_name', 'title')
                ?? 'Pricing request')}
              why={String(pick(a, 'reason', 'summary', 'detail') ?? '') || null}
              action="Decide"
              tone="danger"
              onPress={() => router.push('/(manager)/approvals' as never)}
            />
          ))}
        </>
      ) : null}

      {/* ── EXCEPTIONS THE SERVER RAISED ──────────────────────────────────── */}
      {attentionItems.length ? (
        <>
          <SectionHeader title={`Needs intervention · ${attentionItems.length}`} />
          {attentionItems.slice(0, 8).map((a, i) => (
            <AttentionItem
              key={String(pick(a, 'id', 'opportunity_id') ?? i)}
              title={nameOf(a, 'Deal')}
              why={String(pick(a, 'reason', 'kind', 'detail') ?? '') || null}
              action="Open the deal"
              tone={pick(a, 'severity') === 'red' ? 'danger' : 'warning'}
              onPress={() => {
                const id = pick(a, 'opportunity_id', 'id');
                if (id) router.push(`/opportunity/${String(id)}` as never);
              }}
            />
          ))}
        </>
      ) : null}

      {/* ── TEAM TODAY ────────────────────────────────────────────────────── */}
      {teamToday.length ? (
        <>
          <SectionHeader title={`Team today · ${teamToday.length}`} />
          {teamToday.map((a, i) => (
            <Row
              key={String(pick(a, 'id') ?? i)}
              title={String(pick(a, 'title', 'meeting_type') ?? 'Appointment')}
              subtitle={[pick(a, 'owner_name', 'salesperson_name'),
                         pick(a, 'prospect_name', 'company_name')]
                .filter(Boolean).join(' · ') || null}
              meta={timeOf(pick(a, 'starts_at'))}
              accent={palette.accent}
              onPress={() => {
                const id = pick(a, 'id', 'appointment_id');
                if (id) router.push(`/appointment/${String(id)}` as never);
              }}
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
              key={String(pick(d, 'opportunity_id', 'id') ?? i)}
              title={nameOf(d, 'Deal')}
              subtitle={String(pick(d, 'owner_name', 'salesperson_name') ?? '') || null}
              meta={relativeOf(pick(d, 'expected_close_date', 'close_date'))}
              onPress={() => {
                const id = pick(d, 'opportunity_id', 'id');
                if (id) router.push(`/opportunity/${String(id)}` as never);
              }}
              right={pick(d, 'amount') != null
                ? <Pill label={asMoney(pick(d, 'amount'))} tone="accent" />
                : undefined}
            />
          ))}
        </>
      ) : null}

      {/* ── REP ROLLUP ────────────────────────────────────────────────────── */}
      {reps.length ? (
        <>
          <SectionHeader title="Team activity" />
          {reps.map((r, i) => (
            <Row
              key={String(pick(r, 'user_id', 'id') ?? i)}
              title={String(pick(r, 'name', 'full_name') ?? 'Salesperson')}
              subtitle={`${pick(r, 'open_deals') ?? 0} open`}
              meta={relativeOf(pick(r, 'last_activity_at', 'generated_at'))}
              onPress={() => router.push('/(manager)/team' as never)}
            />
          ))}
        </>
      ) : null}

      {!q.isLoading && !pendingApprovals.length && !attentionItems.length
        && !teamToday.length ? (
        <EmptyState
          title="Quiet day"
          body="No approvals waiting, nothing flagged for intervention, and no team meetings scheduled today."
        />
      ) : null}
    </Screen>
  );
}
