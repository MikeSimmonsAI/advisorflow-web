/**
 * Query helpers.
 *
 * EVERY KEY CARRIES THE EXPERIENCE. The cache is already cleared on an
 * experience switch (see ExperienceContext — that clear is a tenant-isolation
 * control), and this is the belt to that pair of braces: even if a clear were
 * ever missed, Org A's leads and Org B's leads could not collide on one key.
 * Two independent reasons the wrong company's data cannot appear under the
 * right company's heading is the correct number of reasons.
 */

import { useCallback } from 'react';
import {
  useQuery, useQueryClient, type UseQueryOptions,
} from '@tanstack/react-query';

import { useExperience } from '../experience/ExperienceContext';

export function useScopeKey(): string {
  const { active } = useExperience();
  return active?.key ?? 'no-experience';
}

/** A query scoped to the active experience. */
export function useScopedQuery<T>(
  key: readonly unknown[],
  fn: () => Promise<T>,
  options?: Omit<UseQueryOptions<T, unknown, T, readonly unknown[]>, 'queryKey' | 'queryFn'>,
) {
  const scope = useScopeKey();
  return useQuery<T, unknown, T, readonly unknown[]>({
    queryKey: [scope, ...key],
    queryFn: fn,
    ...options,
  });
}

/** Refresh everything on this screen's scope — what pull-to-refresh calls. */
export function useScopedRefresh() {
  const scope = useScopeKey();
  const client = useQueryClient();
  return useCallback(() => {
    void client.invalidateQueries({ queryKey: [scope] });
  }, [client, scope]);
}

// ── shape helpers ───────────────────────────────────────────────────────────
//
// Routers on this platform return either a bare list or an envelope, depending
// on when they were written. Rather than guess per screen — and get it wrong on
// the one router that disagrees — every list goes through here.

export function asList<T>(value: unknown, ...keys: string[]): T[] {
  if (Array.isArray(value)) return value as T[];
  if (value && typeof value === 'object') {
    const obj = value as Record<string, unknown>;
    for (const key of keys) {
      if (Array.isArray(obj[key])) return obj[key] as T[];
    }
    // Last resort: the first array-valued property. A router that returns
    // `{items: [...]}` under a name nobody predicted still renders.
    for (const v of Object.values(obj)) {
      if (Array.isArray(v)) return v as T[];
    }
  }
  return [];
}

export function pick<T = unknown>(obj: unknown, ...paths: string[]): T | undefined {
  if (!obj || typeof obj !== 'object') return undefined;
  const record = obj as Record<string, unknown>;
  for (const path of paths) {
    const value = path.split('.').reduce<unknown>(
      (acc, part) => (acc && typeof acc === 'object'
        ? (acc as Record<string, unknown>)[part]
        : undefined),
      record,
    );
    if (value !== undefined && value !== null) return value as T;
  }
  return undefined;
}

export function asText(value: unknown, fallback = '—'): string {
  if (value === null || value === undefined || value === '') return fallback;
  if (typeof value === 'string') return value;
  if (typeof value === 'number') return String(value);
  return fallback;
}

export function asMoney(value: unknown): string {
  const n = typeof value === 'number' ? value : Number(value);
  if (!Number.isFinite(n)) return '—';
  return n.toLocaleString(undefined, {
    style: 'currency', currency: 'USD', maximumFractionDigits: 0,
  });
}

export function asCount(value: unknown): number {
  const n = typeof value === 'number' ? value : Number(value);
  return Number.isFinite(n) ? n : 0;
}
