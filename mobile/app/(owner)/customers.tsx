/**
 * OWNER · CUSTOMERS — an operating list, not a phone directory.
 *
 * ═══════════════════════════════════════════════════════════════════════════
 * WHAT CHANGED AND WHY
 * ═══════════════════════════════════════════════════════════════════════════
 *
 * The previous version listed every organization the platform has ever had, in
 * server order, with a name and an Active pill. Two consequences:
 *
 *   - "ZZ Launch Verify A (test)" and its siblings sat in the same list as
 *     real paying customers, in the same weight, with nothing to separate them.
 *   - There was no way to answer the only question worth asking on a phone:
 *     WHICH OF THESE NEEDS ME.
 *
 * NOTHING IS DELETED AND NOTHING IS HIDDEN FOREVER. Inactive and verification
 * records are still here, one tap away under their own filter. What changed is
 * the DEFAULT: active customers first, because that is what the list is for.
 *
 * The test-record rule is deliberately narrow — a name that starts with "ZZ "
 * or is parenthetically marked "(test)". A broad heuristic would eventually
 * demote a real customer whose name happened to match, and a customer missing
 * from the owner's list is a worse failure than a test record appearing in it.
 */

import React, { useMemo, useState } from 'react';
import { StyleSheet, TextInput } from 'react-native';
import { router } from 'expo-router';

import { owner } from '../../src/api/endpoints';
import { pick, useScopedQuery, useScopedRefresh } from '../../src/hooks/useApi';
import { listField } from '../../src/api/state';
import { FilterChips, SectionRetry, Skeleton } from '../../src/components/data';
import {
  EmptyState, Pill, Row, Screen, ScreenTitle,
} from '../../src/components/ui';
import { palette, radius, space, type as typography, HIT_SIZE } from '../../src/theme/tokens';

type Org = Record<string, unknown>;
type Tab = 'active' | 'attention' | 'implementation' | 'inactive';

const TEST_NAME = /^zz[\s_-]|\(test\)|\btest\b\s*(customer|org|account)?$/i;

function nameOf(o: Org): string {
  return String(pick(o, 'name', 'organization_name', 'company_name') ?? 'Organization');
}

function isActive(o: Org): boolean {
  const v = pick(o, 'is_active', 'active');
  return v !== false;
}

function isTestRecord(o: Org): boolean {
  return TEST_NAME.test(nameOf(o));
}

/** The implementation is live/complete, or it is still being built. */
function implState(o: Org): string | null {
  const s = pick<string>(o, 'implementation.status', 'implementation_status', 'status');
  return typeof s === 'string' && s ? s : null;
}

function inImplementation(o: Org): boolean {
  const s = (implState(o) ?? '').toLowerCase();
  return !!s && s !== 'live' && s !== 'complete' && s !== 'cancelled';
}

/**
 * WHY A CUSTOMER NEEDS ATTENTION, in the server's own terms.
 *
 * Nothing is inferred from a number the phone made up. Each of these is a field
 * the customer-organizations endpoint already returns; when none is present the
 * customer simply is not flagged, which is correct — absence of a signal is not
 * a problem, it is silence.
 */
function attentionReason(o: Org): string | null {
  const billing = String(pick(o, 'pricing.billing_status', 'billing_status') ?? '').toLowerCase();
  if (billing && !['active', 'trialing', 'ok'].includes(billing)) {
    return `Billing: ${billing.replace(/_/g, ' ')}`;
  }
  const staffing = pick<number>(o, 'staffing.active_users', 'active_users');
  if (typeof staffing === 'number' && staffing === 0 && isActive(o)) {
    return 'No active users';
  }
  const warnings = pick(o, 'implementation.launch_warnings', 'launch_warnings');
  if (Array.isArray(warnings) && warnings.length) {
    return `${warnings.length} launch warning${warnings.length === 1 ? '' : 's'}`;
  }
  const provisioning = String(pick(o, 'provisioning.status', 'provisioning_status') ?? '')
    .toLowerCase();
  if (provisioning === 'failed' || provisioning === 'blocked') {
    return `Provisioning ${provisioning}`;
  }
  return null;
}

export default function OwnerCustomers() {
  const refresh = useScopedRefresh();
  const [needle, setNeedle] = useState('');
  const [tab, setTab] = useState<Tab>('active');

  const query = useScopedQuery(['owner', 'customers'], () => owner.customers({ limit: 300 }));
  const orgs = listField<Org>(query, 'organizations', 'customers', 'items');
  const all = orgs.value ?? [];

  const buckets = useMemo(() => {
    const active: Org[] = [];
    const attention: Org[] = [];
    const implementation: Org[] = [];
    const inactive: Org[] = [];
    for (const o of all) {
      if (!isActive(o) || isTestRecord(o)) { inactive.push(o); continue; }
      if (attentionReason(o)) attention.push(o);
      if (inImplementation(o)) implementation.push(o);
      active.push(o);
    }
    return { active, attention, implementation, inactive };
  }, [all]);

  const rows = useMemo(() => {
    const base = buckets[tab];
    const q = needle.trim().toLowerCase();
    const filtered = q ? base.filter((o) => nameOf(o).toLowerCase().includes(q)) : base;
    // Attention first inside every tab — the reason to open this screen.
    return [...filtered].sort((a, b) => {
      const ax = attentionReason(a) ? 0 : 1;
      const bx = attentionReason(b) ? 0 : 1;
      if (ax !== bx) return ax - bx;
      return nameOf(a).localeCompare(nameOf(b));
    });
  }, [buckets, tab, needle]);

  return (
    <Screen refreshing={query.isFetching} onRefresh={refresh}>
      <ScreenTitle
        title="Customers"
        subtitle={orgs.state === 'ready' || orgs.state === 'zero'
          ? `${buckets.active.length} active · ${buckets.attention.length} need attention`
          : undefined}
      />

      <SectionRetry
        show={orgs.state === 'error'}
        onRetry={refresh}
        note={orgs.reason ?? 'The customer list could not be loaded.'}
      />

      <TextInput
        value={needle}
        onChangeText={setNeedle}
        placeholder="Search customers"
        placeholderTextColor={palette.textFaint}
        autoCapitalize="none"
        autoCorrect={false}
        style={styles.input}
        accessibilityLabel="Search customers"
      />

      <FilterChips<Tab>
        value={tab}
        onChange={setTab}
        options={[
          { key: 'active', label: 'Active', count: buckets.active.length },
          { key: 'attention', label: 'Needs attention', count: buckets.attention.length },
          { key: 'implementation', label: 'Implementation', count: buckets.implementation.length },
          { key: 'inactive', label: 'Inactive', count: buckets.inactive.length },
        ]}
      />

      {orgs.state === 'loading' ? <Skeleton rows={4} /> : null}

      {orgs.state !== 'loading' && !rows.length ? (
        <EmptyState
          title={needle ? 'Nothing matched' : 'Nothing in this list'}
          body={needle
            ? 'No customer name contains that.'
            : tab === 'attention'
              ? 'No customer is currently flagged. That is the good outcome.'
              : undefined}
        />
      ) : null}

      {rows.map((o, i) => {
        const id = String(pick(o, 'id', 'organization_id') ?? i);
        const reason = attentionReason(o);
        const impl = inImplementation(o) ? implState(o) : null;
        const plan = pick<string>(o, 'pricing.plan_name', 'plan_name', 'plan');
        return (
          <Row
            key={id}
            title={nameOf(o)}
            subtitle={reason ?? (plan ? String(plan) : null)}
            meta={impl ? `Implementation · ${impl}` : null}
            accent={reason ? palette.warning : undefined}
            onPress={() => router.push(`/customer/${id}` as never)}
            right={
              !isActive(o) ? <Pill label="Inactive" tone="neutral" />
                : reason ? <Pill label="Attention" tone="warning" />
                  : impl ? <Pill label="Building" tone="accent" />
                    : <Pill label="Active" tone="positive" />
            }
          />
        );
      })}
    </Screen>
  );
}

const styles = StyleSheet.create({
  input: {
    minHeight: HIT_SIZE,
    backgroundColor: palette.surfaceSunken,
    borderRadius: radius.md,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: palette.border,
    paddingHorizontal: space.lg,
    color: palette.text,
    ...typography.body,
  },
});
