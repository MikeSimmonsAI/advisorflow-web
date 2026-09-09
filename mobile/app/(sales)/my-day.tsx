/**
 * MY DAY — "what do I need to do right now?"
 *
 * Not a dashboard. There is no vanity number on this screen, no chart, and no
 * metric that does not name something the rep can act on before lunch. Every
 * block below is a LIST OF THINGS WITH A TAP TARGET; if a block would only ever
 * be looked at, it does not belong here.
 *
 * Everything comes from GET /sales/my-day, which the desktop already uses.
 * There is no mobile-shaped variant and no client-side aggregation: a second
 * place that decides what "overdue" means is a second definition of overdue.
 *
 * The payload's exact key names have varied as the endpoint grew, so each block
 * reads through `pick`/`asList` with the handful of names the router has used.
 * A block whose data is absent renders nothing rather than an empty card — a
 * screen of five empty cards reads as broken, and a short screen reads as a
 * quiet morning.
 */

import React from 'react';
import { View } from 'react-native';
import { router } from 'expo-router';

import { sales, scheduling } from '../../src/api/endpoints';
import { asList, asMoney, pick, useScopedQuery, useScopedRefresh } from '../../src/hooks/useApi';
import { useActiveExperience } from '../../src/experience/ExperienceContext';
import {
  Card, EmptyState, ErrorState, Loading, Pill, Row, Screen, ScreenTitle,
  SectionHeader,
} from '../../src/components/ui';
import { nameOf, relativeOf, timeOf, whenOf, isoDate } from '../../src/format';
import { stageLabel } from '../../src/vocab';
import { palette, space } from '../../src/theme/tokens';
import type { Appointment, Opportunity } from '../../src/api/types';

export default function MyDay() {
  const exp = useActiveExperience();
  const brand = exp.brandSalesOrgId;
  const refresh = useScopedRefresh();

  const day = useScopedQuery(['my-day', brand], () => sales.myDay(brand));

  // Today's appointments come from the scheduling router directly rather than
  // from whatever my-day happens to embed, because the calendar tab and this
  // block must agree. Two sources for "today" is how they disagree at 8:59.
  const today = isoDate(new Date());
  const appts = useScopedQuery(['appointments', 'today', brand], () =>
    scheduling.appointments({
      brand_sales_org_id: brand, date_from: today, date_to: today, scope: 'mine',
    }));

  if (day.isLoading && appts.isLoading) {
    return <Screen><Loading label="Building your day" /></Screen>;
  }
  if (day.isError && appts.isError) {
    return <Screen><ErrorState error={day.error} onRetry={refresh} /></Screen>;
  }

  const payload = day.data ?? {};
  const appointments = asList<Appointment>(appts.data, 'appointments', 'items');

  const followUps = asList<Record<string, unknown>>(
    pick(payload, 'overdue_follow_ups', 'follow_ups', 'due_follow_ups'), 'items');
  const attention = asList<Record<string, unknown>>(
    pick(payload, 'leads_needing_attention', 'attention', 'hot_leads'), 'items');
  const open = asList<Opportunity>(
    pick(payload, 'open_opportunities', 'opportunities', 'active_opportunities'), 'items');
  const proposalActions = asList<Record<string, unknown>>(
    pick(payload, 'proposals_awaiting_action', 'proposals', 'awaiting_action'), 'items');
  const approvals = asList<Record<string, unknown>>(
    pick(payload, 'approvals', 'approval_results', 'pricing_requests'), 'items');
  const recent = asList<Record<string, unknown>>(
    pick(payload, 'recent_activity', 'activity'), 'items');

  const nothing =
    !appointments.length && !followUps.length && !attention.length
    && !open.length && !proposalActions.length && !approvals.length;

  return (
    <Screen refreshing={day.isFetching || appts.isFetching} onRefresh={refresh}>
      <ScreenTitle
        title="My Day"
        subtitle={exp.detail ?? 'Everything waiting on you'}
      />

      {nothing ? (
        <EmptyState
          title="Nothing is waiting on you"
          body="No appointments today, nothing overdue, and no proposal needs a decision. Leads and Calendar are still there when you want them."
        />
      ) : null}

      {appointments.length ? (
        <>
          <SectionHeader title={`Today · ${appointments.length}`} />
          {appointments.map((a) => (
            <Row
              key={String(a.id)}
              title={String(a.title ?? a.meeting_type ?? 'Appointment')}
              subtitle={a.prospect_name ?? a.opportunity_name ?? null}
              meta={`${timeOf(a.starts_at)}${a.location ? ` · ${a.location}` : ''}`}
              accent={palette.accent}
              onPress={() => router.push(`/appointment/${a.id}` as never)}
              right={
                a.confirmation_status === 'confirmed'
                  ? <Pill label="Confirmed" tone="positive" />
                  : a.confirmation_status === 'declined'
                    ? <Pill label="Declined" tone="danger" />
                    : <Pill label="Unconfirmed" tone="warning" />
              }
            />
          ))}
        </>
      ) : null}

      {followUps.length ? (
        <>
          <SectionHeader title={`Overdue follow-ups · ${followUps.length}`} />
          {followUps.map((f, i) => (
            <Row
              key={String(f.id ?? i)}
              title={nameOf(f, 'Follow-up')}
              subtitle={typeof f.next_step === 'string' ? f.next_step : null}
              meta={`Due ${relativeOf(f.due_at ?? f.follow_up_at ?? f.next_step_at)}`}
              accent={palette.danger}
              onPress={() => {
                const oppId = f.opportunity_id ?? f.id;
                if (oppId) router.push(`/opportunity/${oppId}` as never);
              }}
            />
          ))}
        </>
      ) : null}

      {attention.length ? (
        <>
          <SectionHeader title={`Needs attention · ${attention.length}`} />
          {attention.map((l, i) => (
            <Row
              key={String(l.id ?? i)}
              title={nameOf(l, 'Lead')}
              subtitle={typeof l.reason === 'string' ? l.reason : null}
              meta={l.last_contacted_at ? `Last contact ${relativeOf(l.last_contacted_at)}` : 'Never contacted'}
              accent={palette.warning}
              onPress={() => l.id && router.push(`/lead/${l.id}` as never)}
            />
          ))}
        </>
      ) : null}

      {proposalActions.length ? (
        <>
          <SectionHeader title={`Proposals · ${proposalActions.length}`} />
          {proposalActions.map((p, i) => (
            <Row
              key={String(p.id ?? i)}
              title={String(p.title ?? p.opportunity_name ?? 'Proposal')}
              subtitle={typeof p.status === 'string' ? p.status : null}
              meta={p.total !== undefined ? asMoney(p.total) : null}
              onPress={() => p.id && router.push(`/proposal/${p.id}` as never)}
            />
          ))}
        </>
      ) : null}

      {approvals.length ? (
        <>
          <SectionHeader title="Approvals" />
          {approvals.map((a, i) => (
            <Card key={String(a.id ?? i)}>
              <Row
                title={String(a.opportunity_name ?? 'Pricing request')}
                subtitle={typeof a.status === 'string' ? `Status: ${a.status}` : null}
                meta={a.decided_at ? `Decided ${relativeOf(a.decided_at)}` : 'Awaiting a decision'}
              />
            </Card>
          ))}
        </>
      ) : null}

      {open.length ? (
        <>
          <SectionHeader title={`Active deals · ${open.length}`} />
          {open.slice(0, 8).map((o) => (
            <Row
              key={String(o.id)}
              title={nameOf(o, 'Opportunity')}
              subtitle={stageLabel(o.stage)}
              meta={o.expected_close_date ? `Close ${whenOf(o.expected_close_date)}` : null}
              onPress={() => router.push(`/opportunity/${o.id}` as never)}
              right={o.amount != null ? <Pill label={asMoney(o.amount)} tone="accent" /> : undefined}
            />
          ))}
        </>
      ) : null}

      {recent.length ? (
        <>
          <SectionHeader title="Recent activity" />
          <Card>
            <View style={{ gap: space.sm }}>
              {recent.slice(0, 6).map((r, i) => (
                <Row
                  key={String(r.id ?? i)}
                  title={String(r.summary ?? r.description ?? r.type ?? 'Activity')}
                  meta={relativeOf(r.created_at ?? r.occurred_at)}
                />
              ))}
            </View>
          </Card>
        </>
      ) : null}
    </Screen>
  );
}
