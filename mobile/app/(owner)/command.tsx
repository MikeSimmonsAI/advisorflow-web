/**
 * OWNER COMMAND — how is the business, what needs me, what changed.
 *
 * ═══════════════════════════════════════════════════════════════════════════
 * WHAT WAS WRONG WITH THE VERSION THIS REPLACES
 * ═══════════════════════════════════════════════════════════════════════════
 *
 * It rendered:
 *
 *     Platform: Unavailable    Customers: 0    Users: 0    Leads: 0
 *
 * on a healthy server holding real customers, users and leads. Two bugs, both
 * silent:
 *
 *   1. WRONG FIELD NAMES. `/god/stats` returns `total_orgs`, `total_users`,
 *      `total_leads`. The screen asked for `organizations`, `users`, `leads`.
 *      Every one resolved to `undefined`.
 *
 *   2. `asCount(undefined)` IS `0`. So all three undefineds became zeros, and
 *      the owner was told he had no business. See api/state.ts — the helpers
 *      this screen now uses cannot do that.
 *
 *   3. `Platform: Unavailable` came from reading a top-level `severity` off
 *      `/god/platform-health`, which returns a LIST of sections, each with its
 *      own severity. There was no top-level field to find.
 *
 * ═══════════════════════════════════════════════════════════════════════════
 * WHAT THIS SCREEN IS FOR
 * ═══════════════════════════════════════════════════════════════════════════
 *
 * Three questions, in the order an owner asks them: how is the business, what
 * needs me, what changed. Everything here is either a number he runs the week
 * on or something he can act on. NOTHING DESTRUCTIVE IS REACHABLE — no
 * suspend, cancel, offboard or delete — and that restraint is no longer
 * announced in a paragraph at the bottom of the screen, because telling
 * somebody what the app cannot do is not the app's job.
 */

import React from 'react';
import { router } from 'expo-router';

import { owner } from '../../src/api/endpoints';
import { useScopedQuery, useScopedRefresh, asCount, pick } from '../../src/hooks/useApi';
import {
  anyError, centsField, listField, numField, textField,
} from '../../src/api/state';
import {
  ActivityItem, AttentionItem, FilterChips, Metric, MetricGrid, SectionRetry, Skeleton,
} from '../../src/components/data';
import {
  Card, EmptyState, Row, Screen, ScreenTitle, SectionHeader, SeverityPill,
} from '../../src/components/ui';
import { normaliseSeverity } from '../../src/vocab';
import { palette } from '../../src/theme/tokens';

type HealthSection = {
  key?: string; label?: string; severity?: string; status?: string;
  headline?: string; detail?: string; needs?: string;
};

function money(v: number | string): string {
  const n = Number(v);
  if (!Number.isFinite(n)) return '—';
  return n.toLocaleString(undefined, {
    style: 'currency', currency: 'USD', maximumFractionDigits: 0,
  });
}

function when(iso: unknown): string | null {
  if (typeof iso !== 'string') return null;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return null;
  const mins = Math.round((Date.now() - d.getTime()) / 60000);
  if (mins < 1) return 'now';
  if (mins < 60) return `${mins}m`;
  if (mins < 60 * 24) return `${Math.round(mins / 60)}h`;
  return `${Math.round(mins / 1440)}d`;
}

export default function OwnerCommand() {
  const refresh = useScopedRefresh();

  const stats = useScopedQuery(['owner', 'stats'], () => owner.stats());
  const health = useScopedQuery(['owner', 'platform-health'], () => owner.platformHealth());
  const queues = useScopedQuery(['owner', 'queues'], () => owner.queues());
  const billing = useScopedQuery(['owner', 'billing-customers'], () => owner.billingCustomers());
  const impls = useScopedQuery(['owner', 'implementations'], () => owner.implementations());

  // ── the numbers, each mapped to the field the server actually returns ─────
  const customers = numField(stats, 'active_orgs', 'total_orgs');
  const totalCustomers = numField(stats, 'total_orgs');
  const users = numField(stats, 'total_users');
  const leads30 = numField(stats, 'new_leads_30d');
  const mrr = centsField(billing, 'visible_mrr_cents');
  const unpriced = numField(billing, 'visible_unpriced');

  const implList = listField<Record<string, unknown>>(impls, 'implementations');
  const openImpls = (implList.value ?? []).filter((i) => {
    const s = String(pick(i, 'status') ?? '').toLowerCase();
    return s !== 'live' && s !== 'complete' && s !== 'cancelled';
  });

  // ── what needs attention: health sections + exception queues + blocked ────
  const healthSections = listField<HealthSection>(health, 'sections', 'health', 'items');
  const unhealthy = (healthSections.value ?? []).filter((s) => {
    const sev = normaliseSeverity(String(s.severity ?? s.status ?? ''));
    return sev === 'attention' || sev === 'action_required';
  });

  const queueList = listField<Record<string, unknown>>(queues, 'queues', 'items');
  const liveQueues = (queueList.value ?? []).filter((q) => asCount(pick(q, 'count')) > 0);

  const attentionCount = unhealthy.length + liveQueues.length;

  const loadFailed = anyError(customers, users, leads30, mrr, healthSections, queueList);
  const firstLoad = stats.isLoading && queues.isLoading && billing.isLoading;

  return (
    <Screen
      refreshing={stats.isFetching || queues.isFetching || billing.isFetching}
      onRefresh={refresh}
    >
      <ScreenTitle
        title="Command"
        subtitle={attentionCount
          ? `${attentionCount} thing${attentionCount === 1 ? '' : 's'} need you`
          : 'How the business is running'}
      />

      <SectionRetry
        show={loadFailed}
        onRetry={refresh}
        note="Some figures could not be loaded. They are not zero."
      />

      {/* ── THE BUSINESS ─────────────────────────────────────────────────── */}
      <MetricGrid>
        <Metric
          label="Active customers"
          field={customers}
          onPress={() => router.push('/(owner)/customers' as never)}
          hint={totalCustomers.value !== undefined && customers.value !== undefined
            && totalCustomers.value !== customers.value
            ? `${totalCustomers.value} total` : undefined}
        />
        <Metric label="Users" field={users} />
        <Metric
          label="Recurring revenue"
          field={mrr}
          format={money}
          hint={unpriced.value ? `${unpriced.value} not yet priced` : undefined}
        />
        <Metric label="New leads · 30d" field={leads30} />
      </MetricGrid>

      {firstLoad ? <Skeleton rows={2} /> : null}

      {/* ── NEEDS ATTENTION ──────────────────────────────────────────────── */}
      <SectionHeader title="Needs attention" />
      {healthSections.state === 'loading' ? <Skeleton rows={2} /> : null}

      {unhealthy.map((s, i) => (
        <AttentionItem
          key={String(s.key ?? i)}
          title={String(s.label ?? s.key ?? 'Platform check')}
          why={s.headline ?? s.detail ?? s.needs ?? null}
          tone={normaliseSeverity(String(s.severity ?? s.status ?? '')) === 'action_required'
            ? 'danger' : 'warning'}
        />
      ))}

      {liveQueues.map((q, i) => (
        <AttentionItem
          key={String(pick(q, 'key') ?? `q${i}`)}
          title={String(pick(q, 'label', 'title', 'key') ?? 'Exception queue')}
          why={String(pick(q, 'detail', 'description') ?? '') || null}
          count={asCount(pick(q, 'count'))}
          action="Open queue"
          tone={normaliseSeverity(String(pick(q, 'severity') ?? '')) === 'action_required'
            ? 'danger' : 'warning'}
          onPress={() => router.push('/(owner)/issues' as never)}
        />
      ))}

      {!attentionCount && healthSections.state !== 'loading' && !loadFailed ? (
        <EmptyState
          title="Nothing needs you"
          body="Every exception queue is empty and no platform check is reporting a problem."
        />
      ) : null}

      {/* ── IMPLEMENTATIONS IN PROGRESS ──────────────────────────────────── */}
      {openImpls.length ? (
        <>
          <SectionHeader title={`Implementations · ${openImpls.length}`} />
          {openImpls.slice(0, 5).map((im, i) => (
            <Row
              key={String(pick(im, 'id') ?? i)}
              title={String(pick(im, 'organization_name', 'customer_name', 'company_name',
                                 'name') ?? 'Implementation')}
              subtitle={String(pick(im, 'status_label', 'status') ?? '')}
              meta={when(pick(im, 'updated_at', 'created_at')) ?? undefined}
              accent={palette.accentSoft}
              onPress={() => {
                const id = pick(im, 'organization_id', 'customer_organization_id');
                if (id) router.push(`/customer/${String(id)}` as never);
              }}
            />
          ))}
        </>
      ) : null}

      {/* ── QUICK ACCESS ─────────────────────────────────────────────────── */}
      <SectionHeader title="Quick access" />
      <Row
        title="Customers"
        subtitle="Search, filter and open a customer"
        onPress={() => router.push('/(owner)/customers' as never)}
      />
      <Row
        title="Recent activity"
        subtitle="What changed across the platform"
        onPress={() => router.push('/(owner)/activity' as never)}
      />
    </Screen>
  );
}
