/**
 * CUSTOMER — awareness and quick operational actions.
 *
 * ═══════════════════════════════════════════════════════════════════════════
 * WHAT CHANGED
 * ═══════════════════════════════════════════════════════════════════════════
 *
 * The previous screen was a six-row metadata table and a closing paragraph
 * explaining what the app deliberately cannot do. The paragraph was the largest
 * piece of content on it. Telling somebody at length what a screen is not for
 * is not a feature — the restraint is still absolute, it is simply no longer
 * announced. Destructive lifecycle actions (cancel, offboard, archive, delete,
 * major billing change) are not rendered, not imported, and not reachable.
 *
 * WHAT IT IS NOW: who they are, whether they are healthy, what needs doing,
 * what happened lately, and a way to reach them — in that order, because that
 * is the order somebody standing outside a meeting needs them.
 *
 * EVERY NUMBER GOES THROUGH api/state.ts. A customer whose MRR could not be
 * read shows "Unavailable" with the server's reason, never $0 — telling an
 * owner a paying customer bills nothing is worse than telling him nothing.
 */

import React from 'react';
import { useLocalSearchParams, router } from 'expo-router';

import { owner } from '../../src/api/endpoints';
import { pick, useScopedQuery, useScopedRefresh } from '../../src/hooks/useApi';
import {
  anyError, centsField, listField, numField, textField, withNotConfigured,
} from '../../src/api/state';
import {
  ActivityItem, AttentionItem, Metric, MetricGrid, SectionRetry, Skeleton,
} from '../../src/components/data';
import { ContactActions } from '../../src/components/ContactActions';
import {
  Card, EmptyState, Pill, Screen, ScreenTitle, SectionHeader, SeverityPill,
} from '../../src/components/ui';
import { relativeOf } from '../../src/format';

function money(v: number | string): string {
  const n = Number(v);
  if (!Number.isFinite(n)) return '—';
  return n.toLocaleString(undefined, {
    style: 'currency', currency: 'USD', maximumFractionDigits: 0,
  });
}

export default function CustomerDetail() {
  const { id } = useLocalSearchParams<{ id: string }>();
  const refresh = useScopedRefresh();

  const query = useScopedQuery(['owner', 'customer', id],
    () => owner.customer360(String(id)), { enabled: !!id });

  const c = (query.data ?? {}) as Record<string, unknown>;
  const org = (pick<Record<string, unknown>>(c, 'organization', 'customer') ?? c) as
    Record<string, unknown>;

  const name = String(pick(org, 'name', 'organization_name') ?? 'Customer');
  const health = String(pick(c, 'severity', 'health', 'health_label') ?? '');
  const active = pick(org, 'is_active') !== false;

  // ── the numbers ──────────────────────────────────────────────────────────
  const users = numField(query, 'counts.users', 'user_count', 'staffing.active_users');
  const leads = numField(query, 'counts.leads', 'lead_count');
  const mrrRaw = centsField(query, 'billing.mrr_cents', 'mrr_cents', 'pricing.mrr_cents');
  const mrr = withNotConfigured(
    mrrRaw,
    textField(query, 'billing.mrr_unavailable_reason', 'mrr_unavailable_reason').value,
  );
  const plan = textField(query, 'billing.plan_name', 'plan_name', 'pricing.plan_name', 'plan');
  const since = textField(query, 'organization.created_at', 'created_at');
  const implStatus = textField(query,
    'implementation.status_label', 'implementation.status', 'implementation_status');

  // ── what needs doing ─────────────────────────────────────────────────────
  const warnings = listField<unknown>(
    { data: pick(c, 'implementation.launch_warnings', 'launch_warnings'), isLoading: false },
    'items');
  const blockers = listField<Record<string, unknown>>(
    { data: pick(c, 'blockers', 'exceptions', 'attention'), isLoading: false }, 'items');

  const billingStatus = String(
    pick(c, 'billing.billing_status', 'billing_status', 'pricing.billing_status') ?? '')
    .toLowerCase();
  const billingProblem = billingStatus
    && !['active', 'trialing', 'ok', 'paid'].includes(billingStatus);

  const attention: Array<{ title: string; why?: string | null; tone?: 'warning' | 'danger' }> = [];
  if (billingProblem) {
    attention.push({
      title: `Billing is ${billingStatus.replace(/_/g, ' ')}`,
      why: 'Commercial status needs review on a computer.',
      tone: 'danger',
    });
  }
  for (const w of (warnings.value ?? [])) {
    attention.push({
      title: typeof w === 'string' ? w : String(pick(w, 'label', 'message', 'title') ?? 'Launch warning'),
      why: typeof w === 'string' ? null : String(pick(w, 'detail', 'why') ?? '') || null,
      tone: 'warning',
    });
  }
  for (const b of (blockers.value ?? [])) {
    attention.push({
      title: String(pick(b, 'title', 'label', 'summary') ?? 'Blocker'),
      why: String(pick(b, 'detail', 'reason', 'why') ?? '') || null,
      tone: 'warning',
    });
  }

  // ── recent activity ──────────────────────────────────────────────────────
  const events = listField<Record<string, unknown>>(
    { data: pick(c, 'lifecycle_events', 'events', 'timeline', 'history'), isLoading: false },
    'items', 'events');

  // ── how to reach them ────────────────────────────────────────────────────
  const phone = textField(query,
    'primary_contact.phone', 'contact.phone', 'organization.phone', 'phone').value;
  const email = textField(query,
    'primary_contact.email', 'contact.email', 'organization.email', 'email').value;
  const contactName = textField(query,
    'primary_contact.name', 'contact.name', 'primary_contact.full_name').value;

  const failed = anyError(users, leads, mrr);

  return (
    <Screen refreshing={query.isFetching} onRefresh={refresh}>
      <ScreenTitle
        title={name}
        subtitle={[plan.value, implStatus.value].filter(Boolean).join(' · ') || undefined}
      />

      <Card>
        <MetricGrid>
          <Metric label="Health" field={{ state: 'ready', value: health || 'Not rated' }} />
          <Metric
            label="Status"
            field={{ state: 'ready', value: active ? 'Active' : 'Inactive' }}
          />
        </MetricGrid>
      </Card>

      <SectionRetry show={failed} onRetry={refresh}
                    note="Some of this customer's figures could not be loaded." />

      {query.isLoading ? <Skeleton rows={3} /> : null}

      {/* ── OVERVIEW ──────────────────────────────────────────────────────── */}
      <SectionHeader title="Overview" />
      <MetricGrid>
        <Metric label="Recurring revenue" field={mrr} format={money} emphasis />
        <Metric label="Users" field={users} />
        <Metric label="Leads" field={leads} />
        <Metric
          label="Customer since"
          field={since.state === 'ready'
            ? { state: 'ready', value: relativeOf(since.value) }
            : since}
        />
      </MetricGrid>

      {/* ── NEEDS ATTENTION ───────────────────────────────────────────────── */}
      {attention.length ? (
        <>
          <SectionHeader title="Needs attention" />
          {attention.map((a, i) => (
            <AttentionItem key={i} title={a.title} why={a.why} tone={a.tone ?? 'warning'} />
          ))}
        </>
      ) : null}

      {/* ── QUICK ACTIONS ─────────────────────────────────────────────────── */}
      <SectionHeader title="Quick actions" />
      <Card>
        <ContactActions
          phone={phone}
          email={email}
          personLabel={contactName ?? name}
        />
      </Card>

      {/* ── RECENT ACTIVITY ───────────────────────────────────────────────── */}
      <SectionHeader title="Recent activity" />
      {!(events.value ?? []).length ? (
        <EmptyState title="Nothing recent" />
      ) : (
        <Card>
          {(events.value ?? []).slice(0, 12).map((e, i) => (
            <ActivityItem
              key={String(pick(e, 'id') ?? i)}
              title={String(pick(e, 'summary', 'event', 'event_type', 'type') ?? 'Event')}
              detail={String(pick(e, 'detail', 'reason', 'note') ?? '') || null}
              when={relativeOf(pick(e, 'occurred_at', 'created_at'))}
            />
          ))}
        </Card>
      )}
    </Screen>
  );
}
