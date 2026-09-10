/**
 * MY DAY — the salesperson's operating centre.
 *
 * ═══════════════════════════════════════════════════════════════════════════
 * THE SAME BUG OWNER COMMAND HAD
 * ═══════════════════════════════════════════════════════════════════════════
 *
 * This screen read `overdue_follow_ups`, `leads_needing_attention`,
 * `open_opportunities`, `proposals_awaiting_action` and `approvals`. NONE of
 * those keys exist in `/sales/my-day`. The endpoint returns:
 *
 *     metrics { active_opportunities, follow_ups_due, needs_action,
 *               demos_to_build, won_this_month, won_value_this_month }
 *     appointments_today · needs_confirmation · discoveries_today
 *     demos_today · deals_needing_action · stage_counts · recent_activity
 *
 * Every block therefore resolved to an empty list, every list rendered nothing,
 * and the screen showed "Nothing is waiting on you" to a rep with a full day.
 * The lists were not empty. They were never asked for.
 *
 * ═══════════════════════════════════════════════════════════════════════════
 * NEXT BEST ACTION
 * ═══════════════════════════════════════════════════════════════════════════
 *
 * One thing, at the top, chosen by a fixed order of precedence rather than by a
 * score nobody can audit. A rep opening this app between two meetings does not
 * want to triage a list — they want to be told the next move. The order is:
 *
 *     a meeting starting soon > an unconfirmed meeting today >
 *     a deal the server flagged > an overdue follow-up > a demo to build
 *
 * If none of those exist there is no next action, and the screen says so
 * plainly instead of inventing urgency.
 */

import React, { useMemo } from 'react';
import { router } from 'expo-router';

import { sales, scheduling } from '../../src/api/endpoints';
import { asList, asMoney, pick, useScopedQuery, useScopedRefresh } from '../../src/hooks/useApi';
import { listField, numField, centsField } from '../../src/api/state';
import { useActiveExperience } from '../../src/experience/ExperienceContext';
import {
  AttentionItem, ActivityItem, Metric, MetricGrid, SectionRetry, Skeleton,
} from '../../src/components/data';
import {
  Card, EmptyState, Pill, Row, Screen, ScreenTitle, SectionHeader,
} from '../../src/components/ui';
import { nameOf, relativeOf, timeOf, isoDate } from '../../src/format';
import { palette } from '../../src/theme/tokens';
import type { Appointment } from '../../src/api/types';
import { rowKey } from '../../src/keys';

type Rec = Record<string, unknown>;

function minutesUntil(iso: unknown): number | null {
  if (typeof iso !== 'string') return null;
  const t = new Date(iso).getTime();
  if (Number.isNaN(t)) return null;
  return Math.round((t - Date.now()) / 60000);
}

export default function MyDay() {
  const exp = useActiveExperience();
  const brand = exp.brandSalesOrgId;
  const refresh = useScopedRefresh();

  const day = useScopedQuery(['my-day', brand], () => sales.myDay(brand));

  const today = isoDate(new Date());
  const appts = useScopedQuery(['appointments', 'today', brand], () =>
    scheduling.appointments({
      brand_sales_org_id: brand, date_from: today, date_to: today, scope: 'mine',
    }));

  // ── the real payload ─────────────────────────────────────────────────────
  const activeDeals = numField(day, 'metrics.active_opportunities');
  const followUpsDue = numField(day, 'metrics.follow_ups_due');
  const needsAction = numField(day, 'metrics.needs_action');
  const wonThisMonth = numField(day, 'metrics.won_this_month');
  const wonValue = numField(day, 'metrics.won_value_this_month');

  const appointments = asList<Appointment>(appts.data, 'appointments', 'items');
  const payload = (day.data ?? {}) as Rec;
  const needsConfirmation = asList<Rec>(pick(payload, 'needs_confirmation'), 'items');
  const dealsNeedingAction = asList<Rec>(pick(payload, 'deals_needing_action'), 'items');
  const demosToday = asList<Rec>(pick(payload, 'demos_today'), 'items');
  const discoveriesToday = asList<Rec>(pick(payload, 'discoveries_today'), 'items');
  const recent = asList<Rec>(pick(payload, 'recent_activity'), 'items');

  // ── next best action ─────────────────────────────────────────────────────
  const next = useMemo(() => {
    const upcoming = appointments
      .map((a) => ({ a, mins: minutesUntil(a.starts_at) }))
      .filter((x) => x.mins !== null && x.mins > -30)
      .sort((x, y) => (x.mins as number) - (y.mins as number))[0];

    if (upcoming) {
      const m = upcoming.mins as number;
      return {
        title: m <= 0
          ? `Now: ${String(upcoming.a.title ?? 'Meeting')}`
          : `In ${m < 60 ? `${m} min` : `${Math.round(m / 60)}h`}: ${String(upcoming.a.title ?? 'Meeting')}`,
        why: String(upcoming.a.prospect_name ?? upcoming.a.opportunity_name ?? '') || null,
        action: 'Open the meeting',
        tone: 'danger' as const,
        go: () => router.push(`/appointment/${upcoming.a.id}` as never),
      };
    }
    if (needsConfirmation.length) {
      const a = needsConfirmation[0];
      return {
        title: 'Confirm a meeting',
        why: `${needsConfirmation.length} meeting${needsConfirmation.length === 1 ? ' has' : 's have'} not been confirmed by the prospect.`,
        action: 'Confirm now',
        tone: 'warning' as const,
        go: () => {
          const id = pick(a, 'id', 'appointment_id');
          if (id) router.push(`/appointment/${String(id)}` as never);
        },
      };
    }
    if (dealsNeedingAction.length) {
      const d = dealsNeedingAction[0];
      return {
        title: nameOf(d, 'A deal needs you'),
        why: String(pick(d, 'reason', 'next_step', 'detail') ?? 'Flagged by the pipeline.'),
        action: 'Open the deal',
        tone: 'warning' as const,
        go: () => {
          const id = pick(d, 'opportunity_id', 'id');
          if (id) router.push(`/opportunity/${String(id)}` as never);
        },
      };
    }
    if ((followUpsDue.value ?? 0) > 0) {
      return {
        title: `${followUpsDue.value} follow-up${followUpsDue.value === 1 ? '' : 's'} due`,
        why: 'These are past the date you set.',
        action: 'Open pipeline',
        tone: 'warning' as const,
        go: () => router.push('/(sales)/pipeline' as never),
      };
    }
    if (demosToday.length) {
      return {
        title: `${demosToday.length} demo${demosToday.length === 1 ? '' : 's'} to build`,
        why: 'A demo is expected before the next meeting.',
        action: 'Open pipeline',
        tone: 'warning' as const,
        go: () => router.push('/(sales)/pipeline' as never),
      };
    }
    return null;
  }, [appointments, needsConfirmation, dealsNeedingAction, followUpsDue.value, demosToday]);

  const failed = day.isError && appts.isError;

  return (
    <Screen refreshing={day.isFetching || appts.isFetching} onRefresh={refresh}>
      <ScreenTitle title="My Day" subtitle={exp.detail ?? 'Everything waiting on you'} />

      <SectionRetry
        show={day.isError || appts.isError}
        onRetry={refresh}
        note={failed ? 'Your day could not be loaded.' : 'Part of your day could not be loaded.'}
      />

      {/* ── NEXT BEST ACTION ──────────────────────────────────────────────── */}
      {next ? (
        <>
          <SectionHeader title="Do this next" />
          <AttentionItem
            title={next.title}
            why={next.why}
            action={next.action}
            tone={next.tone}
            onPress={next.go}
          />
        </>
      ) : null}

      {day.isLoading ? <Skeleton rows={3} /> : null}

      {/* ── THE NUMBERS ───────────────────────────────────────────────────── */}
      <MetricGrid>
        <Metric label="Active deals" field={activeDeals}
                onPress={() => router.push('/(sales)/pipeline' as never)} />
        <Metric label="Follow-ups due" field={followUpsDue} />
        <Metric label="Needs action" field={needsAction} />
        <Metric label="Won this month" field={wonThisMonth}
                hint={wonValue.value !== undefined ? asMoney(wonValue.value) : undefined} />
      </MetricGrid>

      {/* ── TODAY ─────────────────────────────────────────────────────────── */}
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

      {/* ── DEALS THE SERVER FLAGGED ──────────────────────────────────────── */}
      {dealsNeedingAction.length ? (
        <>
          <SectionHeader title={`Needs action · ${dealsNeedingAction.length}`} />
          {dealsNeedingAction.map((d, i) => (
            <Row
              key={rowKey([pick(d, 'opportunity_id', 'id')], i)}
              title={nameOf(d, 'Opportunity')}
              subtitle={String(pick(d, 'reason', 'next_step', 'detail') ?? '') || null}
              meta={relativeOf(pick(d, 'stage_changed_at', 'updated_at'))}
              accent={palette.warning}
              onPress={() => {
                const id = pick(d, 'opportunity_id', 'id');
                if (id) router.push(`/opportunity/${String(id)}` as never);
              }}
            />
          ))}
        </>
      ) : null}

      {/* ── DISCOVERY / DEMO WORK ─────────────────────────────────────────── */}
      {discoveriesToday.length || demosToday.length ? (
        <>
          <SectionHeader title="Prep" />
          {discoveriesToday.map((d, i) => (
            <Row key={`disc-${i}`} title={nameOf(d, 'Discovery')}
                 subtitle="Discovery today"
                 onPress={() => {
                   const id = pick(d, 'opportunity_id', 'id');
                   if (id) router.push(`/opportunity/${String(id)}` as never);
                 }} />
          ))}
          {demosToday.map((d, i) => (
            <Row key={`demo-${i}`} title={nameOf(d, 'Demo')}
                 subtitle="Demo today"
                 onPress={() => {
                   const id = pick(d, 'opportunity_id', 'id');
                   if (id) router.push(`/opportunity/${String(id)}` as never);
                 }} />
          ))}
        </>
      ) : null}

      {!next && !appointments.length && !dealsNeedingAction.length && !day.isLoading ? (
        <EmptyState
          title="Nothing is waiting on you"
          body="No meetings today, nothing flagged, and no follow-up overdue. Pipeline and Leads are there when you want them."
        />
      ) : null}

      {/* ── RECENT ────────────────────────────────────────────────────────── */}
      {recent.length ? (
        <>
          <SectionHeader title="Recent activity" />
          <Card>
            {recent.slice(0, 8).map((r, i) => (
              <ActivityItem
                key={rowKey([pick(r, 'id')], i)}
                title={String(pick(r, 'summary', 'event_type') ?? 'Activity')}
                detail={String(pick(r, 'detail') ?? '') || null}
                when={relativeOf(pick(r, 'occurred_at', 'created_at'))}
              />
            ))}
          </Card>
        </>
      ) : null}
    </Screen>
  );
}
