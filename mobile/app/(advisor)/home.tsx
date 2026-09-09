/**
 * ADVISOR HOME — the richest experience in the platform, because the tenant
 * stack is fully backed.
 *
 * Unlike brand sales (GAP-4), an advisor's messaging really works end to end:
 * `sms_router`, `email_router` and `compose_router` are all
 * `require_tenant_user` and an advisor IS a tenant user. Replies here are real
 * replies, and the conversation is a real conversation.
 *
 * WHAT MAKES THIS SCREEN SAFE IS TWO INDEPENDENT THINGS, and both are worth
 * naming because either alone would be a bug:
 *   1. `X-Workspace-Id` is set for this experience and cleared for every other
 *      one (ExperienceContext).
 *   2. The server re-derives the workspace from an ACTIVE membership on every
 *      request — `workspace_access.selected_workspace_id` returns nothing for
 *      an id the caller merely asserted.
 * The header asks; the membership answers.
 */

import React from 'react';
import { router } from 'expo-router';

import { advisor } from '../../src/api/endpoints';
import { asCount, asList, pick, useScopedQuery, useScopedRefresh } from '../../src/hooks/useApi';
import { useActiveExperience } from '../../src/experience/ExperienceContext';
import {
  Card, EmptyState, ErrorState, KeyValue, Loading, Pill, Row, Screen,
  ScreenTitle, SectionHeader,
} from '../../src/components/ui';
import { nameOf, relativeOf } from '../../src/format';
import { palette } from '../../src/theme/tokens';

export default function AdvisorHome() {
  const exp = useActiveExperience();
  const refresh = useScopedRefresh();

  const briefing = useScopedQuery(['advisor', 'briefing'], () => advisor.dailyBriefing());
  const counts = useScopedQuery(['advisor', 'reply-counts'], () => advisor.replyCounts());
  const replies = useScopedQuery(['advisor', 'replies'], () => advisor.replies({ limit: 15 }));

  if (briefing.isLoading && replies.isLoading) {
    return <Screen><Loading label="Loading your workspace" /></Screen>;
  }

  const replyRows = asList<Record<string, unknown>>(replies.data, 'replies', 'items');
  const attention = asList<Record<string, unknown>>(
    pick(briefing.data, 'needs_attention', 'hot_leads', 'priority'), 'items');
  const unreviewed = asCount(pick(counts.data, 'unreviewed', 'counts.unreviewed', 'pending'));

  return (
    <Screen refreshing={briefing.isFetching || replies.isFetching} onRefresh={refresh}>
      <ScreenTitle title={exp.label} subtitle="Today's briefing" />

      {briefing.isError && replies.isError ? (
        <ErrorState error={briefing.error} onRetry={refresh} />
      ) : null}

      <Card>
        <KeyValue label="Replies to review" value={unreviewed} />
        <KeyValue
          label="New leads"
          value={asCount(pick(briefing.data, 'new_leads', 'counts.new'))}
        />
        <KeyValue
          label="Due today"
          value={asCount(pick(briefing.data, 'due_today', 'counts.due_today'))}
        />
      </Card>

      {attention.length ? (
        <>
          <SectionHeader title={`Needs attention · ${attention.length}`} />
          {attention.slice(0, 10).map((l, i) => (
            <Row
              key={String(l.id ?? i)}
              title={nameOf(l, 'Lead')}
              subtitle={typeof l.reason === 'string' ? l.reason
                : typeof l.status === 'string' ? l.status : null}
              meta={l.last_contacted_at
                ? `Last contact ${relativeOf(l.last_contacted_at)}` : 'Never contacted'}
              accent={palette.warning}
              onPress={() => { if (l.id) router.push(`/lead/${l.id}` as never); }}
            />
          ))}
        </>
      ) : null}

      <SectionHeader title="Recent replies" />
      {!replyRows.length ? (
        <EmptyState title="No replies yet" body="New replies land here as they arrive." />
      ) : null}
      {replyRows.map((r, i) => (
        <Row
          key={String(r.id ?? i)}
          title={nameOf(r, 'Reply')}
          subtitle={typeof r.body === 'string' ? r.body
            : typeof r.message === 'string' ? r.message : null}
          meta={relativeOf(r.created_at ?? r.received_at)}
          onPress={() => { if (r.lead_id) router.push(`/lead/${r.lead_id}` as never); }}
          right={r.classification
            ? <Pill label={String(r.classification)} tone="accent" />
            : undefined}
        />
      ))}
    </Screen>
  );
}
