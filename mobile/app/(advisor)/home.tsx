/**
 * WORKSPACE · HOME — the advisor's own day, in the customer's own terms.
 *
 * ═══════════════════════════════════════════════════════════════════════════
 * THIS IS NOT A SMALL VERSION OF THE BACK OFFICE
 * ═══════════════════════════════════════════════════════════════════════════
 *
 * A workspace user works LEADS AND FAMILIES. They do not have opportunities, a
 * brand pipeline, an implementation, a portfolio or a platform. Nothing on this
 * screen reads `/sales/*`, `/executive/*` or `/god/*` — not filtered out,
 * absent — and the vocabulary follows: leads and replies, never "deals".
 *
 * WHAT IT READS, AND THE EXACT SHAPES:
 *
 *   /leads/daily-briefing → replies_needing_attention, cadence_touches_due_today,
 *                           leads_imported_last_24h, bookings_last_7_days,
 *                           certified_appointments_waiting
 *   /sms/replies/counts   → hot, callback, question, needs_follow_up, total, …
 *   /leads/needs-review   → items, total, page, page_size
 *
 * Those five briefing fields are counts and nothing else, so every one of them
 * goes through numField: a briefing that failed to load says so rather than
 * telling an advisor there is nothing to do today.
 */

import React from 'react';
import { router } from 'expo-router';

import { advisor } from '../../src/api/endpoints';
import { asList, pick, useScopedQuery, useScopedRefresh } from '../../src/hooks/useApi';
import { anyError, listField, numField } from '../../src/api/state';
import { useActiveExperience } from '../../src/experience/ExperienceContext';
import {
  AttentionItem, Metric, MetricGrid, SectionRetry, Skeleton,
} from '../../src/components/data';
import {
  EmptyState, Row, Screen, ScreenTitle, SectionHeader,
} from '../../src/components/ui';
import { nameOf, relativeOf } from '../../src/format';
import { palette } from '../../src/theme/tokens';

type Rec = Record<string, unknown>;

export default function AdvisorHome() {
  const exp = useActiveExperience();
  const refresh = useScopedRefresh();

  const brief = useScopedQuery(['advisor', 'briefing'], () => advisor.dailyBriefing());
  const counts = useScopedQuery(['advisor', 'reply-counts'], () => advisor.replyCounts());
  const review = useScopedQuery(['advisor', 'needs-review'], () => advisor.needsReview());

  const repliesWaiting = numField(brief, 'replies_needing_attention');
  const touchesDue = numField(brief, 'cadence_touches_due_today');
  const imported24 = numField(brief, 'leads_imported_last_24h');
  const bookings7 = numField(brief, 'bookings_last_7_days');
  const certifiedWaiting = numField(brief, 'certified_appointments_waiting');

  const hot = numField(counts, 'hot');
  const callback = numField(counts, 'callback');
  const needsFollowUp = numField(counts, 'needs_follow_up');

  const reviewItems = listField<Rec>(review, 'items');
  const toReview = reviewItems.value ?? [];

  const failed = anyError(repliesWaiting, touchesDue, hot);

  // What to do first, in the order a person working a queue would pick it up.
  const actions: Array<{ title: string; why: string; tone: 'danger' | 'warning'; go: () => void }> = [];
  if ((hot.value ?? 0) > 0) {
    actions.push({
      title: `${hot.value} interested repl${hot.value === 1 ? 'y' : 'ies'}`,
      why: 'Someone said yes and is waiting on you.',
      tone: 'danger',
      go: () => router.push('/(advisor)/leads' as never),
    });
  }
  if ((callback.value ?? 0) > 0) {
    actions.push({
      title: `${callback.value} callback request${callback.value === 1 ? '' : 's'}`,
      why: 'They asked to be called back.',
      tone: 'danger',
      go: () => router.push('/(advisor)/leads' as never),
    });
  }
  if ((certifiedWaiting.value ?? 0) > 0) {
    actions.push({
      title: `${certifiedWaiting.value} appointment${certifiedWaiting.value === 1 ? '' : 's'} waiting`,
      why: 'Certified and not yet actioned.',
      tone: 'warning',
      go: () => router.push('/(advisor)/calendar' as never),
    });
  }
  if ((touchesDue.value ?? 0) > 0) {
    actions.push({
      title: `${touchesDue.value} touch${touchesDue.value === 1 ? '' : 'es'} due today`,
      why: 'Scheduled outreach that has not gone yet.',
      tone: 'warning',
      go: () => router.push('/(advisor)/leads' as never),
    });
  }

  return (
    <Screen
      refreshing={brief.isFetching || counts.isFetching}
      onRefresh={refresh}
    >
      <ScreenTitle title="Home" subtitle={exp.label} />

      <SectionRetry
        show={failed}
        onRetry={refresh}
        note="Part of your briefing could not be loaded. These are not zeros."
      />

      {brief.isLoading && counts.isLoading ? <Skeleton rows={3} /> : null}

      {/* ── DO THIS FIRST ─────────────────────────────────────────────────── */}
      {actions.length ? (
        <>
          <SectionHeader title="Needs you now" />
          {actions.map((a, i) => (
            <AttentionItem
              key={i}
              title={a.title}
              why={a.why}
              action="Open"
              tone={a.tone}
              onPress={a.go}
            />
          ))}
        </>
      ) : null}

      {/* ── TODAY ─────────────────────────────────────────────────────────── */}
      <SectionHeader title="Today" />
      <MetricGrid>
        <Metric
          label="Replies waiting"
          field={repliesWaiting}
          onPress={() => router.push('/(advisor)/leads' as never)}
        />
        <Metric label="Touches due" field={touchesDue} />
        <Metric label="New leads · 24h" field={imported24} />
        <Metric label="Bookings · 7d" field={bookings7} />
      </MetricGrid>

      {/* ── NEEDS REVIEW ──────────────────────────────────────────────────── */}
      {toReview.length ? (
        <>
          <SectionHeader title={`Needs review · ${toReview.length}`} />
          {toReview.slice(0, 8).map((l, i) => (
            <Row
              key={String(pick(l, 'id') ?? i)}
              title={nameOf(l, 'Lead')}
              subtitle={String(pick(l, 'reason', 'status', 'classification') ?? '') || null}
              meta={relativeOf(pick(l, 'last_contacted_at', 'updated_at', 'created_at'))}
              accent={palette.warning}
              onPress={() => {
                const id = pick(l, 'id');
                if (id) router.push(`/lead/${String(id)}` as never);
              }}
            />
          ))}
        </>
      ) : null}

      {!actions.length && !toReview.length && !brief.isLoading && !failed ? (
        <EmptyState
          title="You are caught up"
          body="No replies waiting, nothing overdue and nothing to review. Leads and Calendar are there when you want them."
        />
      ) : null}

      {/* ── QUICK ACCESS ──────────────────────────────────────────────────── */}
      <SectionHeader title="Quick access" />
      <Row
        title="Leads"
        subtitle={needsFollowUp.value ? `${needsFollowUp.value} need follow-up` : 'Search and work your leads'}
        onPress={() => router.push('/(advisor)/leads' as never)}
      />
      <Row
        title="Calendar"
        subtitle="Your appointments"
        onPress={() => router.push('/(advisor)/calendar' as never)}
      />
    </Screen>
  );
}
