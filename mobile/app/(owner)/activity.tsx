/**
 * OWNER · ACTIVITY — what changed, newest first.
 *
 * This tab took the slot Issues was holding. Issues was empty most days; this
 * never is, which is the whole argument: a permanent tab has to earn its place
 * every day, not on the worst day.
 *
 * ONE SOURCE, NOT A RECONSTRUCTION. `/god/ops/audit` is the platform's own
 * record of what happened, already authorised and already ordered. Assembling a
 * feed on the phone from customers + implementations + opportunities would give
 * three clocks, three failure modes and an ordering that is wrong whenever one
 * of them is slow.
 */

import React, { useMemo, useState } from 'react';
import { router } from 'expo-router';

import { owner } from '../../src/api/endpoints';
import { pick, useScopedQuery, useScopedRefresh } from '../../src/hooks/useApi';
import { listField } from '../../src/api/state';
import { ActivityItem, FilterChips, SectionRetry, Skeleton } from '../../src/components/data';
import {
  Card, EmptyState, Screen, ScreenTitle, SectionHeader,
} from '../../src/components/ui';
import { auditEventLabel, humanText } from '../../src/vocab';
import { rowKey } from '../../src/keys';

type Entry = Record<string, unknown>;

function ago(iso: unknown): string | null {
  if (typeof iso !== 'string') return null;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return null;
  const mins = Math.round((Date.now() - d.getTime()) / 60000);
  if (mins < 1) return 'now';
  if (mins < 60) return `${mins}m`;
  if (mins < 60 * 24) return `${Math.round(mins / 60)}h`;
  return `${Math.round(mins / 1440)}d`;
}

function dayOf(iso: unknown): string {
  if (typeof iso !== 'string') return 'Earlier';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return 'Earlier';
  const today = new Date();
  const same = d.toDateString() === today.toDateString();
  if (same) return 'Today';
  const y = new Date(today.getTime() - 86400000);
  if (d.toDateString() === y.toDateString()) return 'Yesterday';
  return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
}

export default function OwnerActivity() {
  const refresh = useScopedRefresh();
  const [category, setCategory] = useState<string>('all');

  const query = useScopedQuery(['owner', 'audit', category], () =>
    owner.audit(category === 'all' ? {} : { category }));

  const entries = listField<Entry>(query, 'entries', 'items', 'audit');
  const rows = entries.value ?? [];

  const categories = useMemo(() => {
    const raw = pick(query.data, 'categories');
    const list = Array.isArray(raw) ? raw : [];
    return [
      { key: 'all', label: 'All' },
      ...list.slice(0, 5).map((c) => {
        const key = typeof c === 'string' ? c : String(pick(c, 'key', 'name') ?? '');
        const label = typeof c === 'string' ? c : String(pick(c, 'label', 'name', 'key') ?? key);
        return { key, label };
      }).filter((c) => c.key),
    ];
  }, [query.data]);

  // Grouped by day so a scroll has landmarks rather than being one long ribbon.
  const grouped = useMemo(() => {
    const out: Array<{ day: string; items: Entry[] }> = [];
    for (const e of rows) {
      const day = dayOf(pick(e, 'occurred_at', 'created_at', 'at', 'timestamp'));
      const last = out[out.length - 1];
      if (last && last.day === day) last.items.push(e);
      else out.push({ day, items: [e] });
    }
    return out;
  }, [rows]);

  return (
    <Screen refreshing={query.isFetching} onRefresh={refresh}>
      <ScreenTitle title="Activity" subtitle="What changed across the platform" />

      <SectionRetry
        show={entries.state === 'error'}
        onRetry={refresh}
        note={entries.reason ?? 'Activity could not be loaded.'}
      />

      {categories.length > 1 ? (
        <FilterChips value={category} onChange={setCategory} options={categories} />
      ) : null}

      {entries.state === 'loading' ? <Skeleton rows={5} /> : null}

      {entries.state !== 'loading' && !rows.length ? (
        <EmptyState
          title="Nothing recorded yet"
          body="Platform changes will appear here as they happen."
        />
      ) : null}

      {grouped.map((group) => (
        <React.Fragment key={group.day}>
          <SectionHeader title={group.day} />
          <Card>
            {group.items.map((e, i) => {
              const orgId = pick(e, 'organization_id', 'target_organization_id');
              return (
                <ActivityItem
                  key={rowKey([pick(e, 'id'), group.day], i)}
                  // `summary` and `label` are written for a person; `action`
                  // and `event_type` are audit-log codes ("customer.created"),
                  // so they go through the label table rather than onto the
                  // screen as themselves.
                  title={humanText(
                    String(pick(e, 'summary', 'label') ?? ''),
                    auditEventLabel(String(pick(e, 'action', 'event_type') ?? '')))}
                  detail={[
                    pick(e, 'organization_name', 'target_name'),
                    pick(e, 'actor_name', 'actor_email'),
                  ].filter(Boolean).join(' · ') || null}
                  when={ago(pick(e, 'occurred_at', 'created_at', 'at', 'timestamp'))}
                />
              );
            })}
          </Card>
        </React.Fragment>
      ))}
    </Screen>
  );
}
