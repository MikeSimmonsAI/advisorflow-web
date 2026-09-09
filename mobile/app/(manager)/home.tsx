/**
 * MANAGER HOME.
 *
 * A manager is a salesperson too — `sales_access.is_sales_member` returns true
 * for `sales_manager`, and decision #4 says a manager sells personally. So this
 * screen leads with what needs a decision and keeps the manager's OWN day on
 * it, rather than replacing one with the other. A manager who cannot see their
 * own appointments has to switch experiences to do their own job.
 *
 * What it does NOT do is recreate the desktop back office. Everything here is
 * something that needs a decision today: an approval waiting, a deal that
 * slipped, an appointment in an hour. Reports, exports and configuration stay
 * on a computer, where they are usable.
 */

import React from 'react';
import { router } from 'expo-router';

import { manager, sales, scheduling } from '../../src/api/endpoints';
import {
  asCount, asList, asMoney, pick, useScopedQuery, useScopedRefresh,
} from '../../src/hooks/useApi';
import { useActiveExperience } from '../../src/experience/ExperienceContext';
import {
  Card, EmptyState, ErrorState, KeyValue, Loading, Row, Screen,
  ScreenTitle, SectionHeader,
} from '../../src/components/ui';
import { isoDate, nameOf, relativeOf, timeOf } from '../../src/format';
import { stageLabel } from '../../src/vocab';
import { palette } from '../../src/theme/tokens';
import type { Appointment, ApprovalRequest } from '../../src/api/types';

export default function ManagerHome() {
  const exp = useActiveExperience();
  const brand = exp.brandSalesOrgId;
  const refresh = useScopedRefresh();
  const today = isoDate(new Date());

  const overview = useScopedQuery(['manager', 'overview', brand],
    () => manager.overview({ brand_sales_org_id: brand }));
  const approvals = useScopedQuery(['manager', 'approvals', brand],
    () => manager.approvals(brand));
  const mine = useScopedQuery(['appointments', 'today', brand, 'mine'],
    () => scheduling.appointments({
      brand_sales_org_id: brand, date_from: today, date_to: today, scope: 'mine',
    }));
  const day = useScopedQuery(['my-day', brand], () => sales.myDay(brand));

  if (overview.isLoading && approvals.isLoading) {
    return <Screen><Loading label="Loading your team" /></Screen>;
  }

  const pending = asList<ApprovalRequest>(approvals.data, 'approvals', 'requests', 'items')
    .filter((a) => !a.status || String(a.status).toLowerCase() === 'pending');
  const myAppts = asList<Appointment>(mine.data, 'appointments', 'items');
  const o = overview.data ?? {};
  const followUps = asList<Record<string, unknown>>(
    pick(day.data, 'overdue_follow_ups', 'follow_ups'), 'items');

  return (
    <Screen refreshing={overview.isFetching || approvals.isFetching} onRefresh={refresh}>
      <ScreenTitle title="Team" subtitle={exp.detail ?? 'What needs you today'} />

      {overview.isError && approvals.isError ? (
        <ErrorState error={overview.error} onRetry={refresh} />
      ) : null}

      {pending.length ? (
        <>
          <SectionHeader title={`Waiting on you · ${pending.length}`} />
          {pending.map((a) => (
            <Row
              key={String(a.id)}
              title={String(a.opportunity_name ?? 'Pricing approval')}
              subtitle={a.requested_by_name ? `From ${a.requested_by_name}` : null}
              meta={[
                a.requested_amount != null ? `Asking ${asMoney(a.requested_amount)}` : null,
                a.floor_amount != null ? `floor ${asMoney(a.floor_amount)}` : null,
              ].filter(Boolean).join(' · ')}
              accent={palette.warning}
              onPress={() => router.push('/(manager)/approvals' as never)}
            />
          ))}
        </>
      ) : (
        <EmptyState title="No approvals waiting" body="Nothing is blocked on a decision from you." />
      )}

      <SectionHeader title="Team today" />
      <Card>
        <KeyValue label="Reps" value={asCount(pick(o, 'rep_count', 'team.count', 'reps_total'))} />
        <KeyValue
          label="Appointments today"
          value={asCount(pick(o, 'appointments_today', 'today.appointments'))}
        />
        <KeyValue
          label="Open pipeline"
          value={asMoney(pick(o, 'pipeline_value', 'pipeline.total', 'open_value'))}
        />
        <KeyValue
          label="Deals in closing"
          value={asCount(pick(o, 'closing_count', 'stages.closing'))}
        />
      </Card>

      {myAppts.length ? (
        <>
          <SectionHeader title={`Your own day · ${myAppts.length}`} />
          {myAppts.map((a) => (
            <Row
              key={String(a.id)}
              title={String(a.title ?? 'Appointment')}
              subtitle={a.prospect_name ?? a.opportunity_name ?? null}
              meta={timeOf(a.starts_at)}
              accent={palette.accent}
              onPress={() => router.push(`/appointment/${a.id}` as never)}
            />
          ))}
        </>
      ) : null}

      {followUps.length ? (
        <>
          <SectionHeader title={`Your overdue follow-ups · ${followUps.length}`} />
          {followUps.slice(0, 5).map((f, i) => (
            <Row
              key={String(f.id ?? i)}
              title={nameOf(f, 'Follow-up')}
              subtitle={typeof f.stage === 'string' ? stageLabel(f.stage) : null}
              meta={`Due ${relativeOf(f.due_at ?? f.follow_up_at)}`}
              accent={palette.danger}
              onPress={() => {
                if (f.opportunity_id) router.push(`/opportunity/${f.opportunity_id}` as never);
              }}
            />
          ))}
        </>
      ) : null}
    </Screen>
  );
}
