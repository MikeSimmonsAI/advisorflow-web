/**
 * FIVE STATES, AND A REQUEST THAT FAILED IS NEVER ONE OF THE NUMBERS.
 *
 * ═══════════════════════════════════════════════════════════════════════════
 * THE BUG THIS FILE EXISTS TO MAKE IMPOSSIBLE
 * ═══════════════════════════════════════════════════════════════════════════
 *
 * Owner Command shipped reading:
 *
 *     asCount(pick(stats, 'organizations', 'counts.orgs', 'org_count'))
 *
 * and `asCount` ends `Number.isFinite(n) ? n : 0`. So `undefined` became `0`.
 * Two separate things then produced the same screen:
 *
 *     the request failed              -> 0 customers
 *     the field is named differently  -> 0 customers
 *
 * Both were true at once. `/god/stats` returns `total_orgs`, `total_users` and
 * `total_leads`; the screen asked for `organizations`, `users` and `leads`,
 * none of which exist in that payload. The owner of the platform was told he
 * had no customers, no users and no leads, by a healthy server holding all
 * three.
 *
 * A zero is a business fact. "I could not find out" is not a business fact, and
 * the type below is what stops the second from being rendered as the first.
 *
 * ═══════════════════════════════════════════════════════════════════════════
 * THE STATES
 * ═══════════════════════════════════════════════════════════════════════════
 *
 *   loading         the request is in flight and there is nothing cached
 *   ready           a real value arrived
 *   zero            a real value arrived and it is 0 — a fact, shown as "0"
 *   unavailable     the request succeeded but this field was not in it
 *   not_configured  the server said this cannot be reported yet, and why
 *   error           the request failed; carries the error so retry can be offered
 *
 * `unavailable` and `error` are deliberately different. The first is our bug or
 * a shape change; the second is the network or the server. Reading "Unavailable"
 * next to a Retry button that cannot help is its own small lie.
 */

import { pick } from '../hooks/useApi';

export type FieldState =
  | 'loading' | 'ready' | 'zero' | 'unavailable' | 'not_configured' | 'error';

export type Field<T> = {
  state: FieldState;
  value?: T;
  /** Why it is not a number. Server-supplied where possible, never invented. */
  reason?: string;
  error?: unknown;
};

/** The shape every screen gets from react-query, narrowed to what matters. */
export type QueryLike = {
  data?: unknown;
  isLoading?: boolean;
  isPending?: boolean;
  isError?: boolean;
  error?: unknown;
};

function errorReason(error: unknown): string {
  const e = error as { offline?: boolean; status?: number; detail?: string };
  if (e?.offline) return 'No connection';
  if (e?.status === 403) return 'Not authorised for this';
  if (e?.status === 404) return 'Not available on this platform';
  return e?.detail || 'Could not load';
}

/**
 * The gate every field passes through.
 *
 * NOTHING BELOW CAN INVENT A VALUE. There is no `?? 0`, no `|| 0`, and no
 * default parameter that would put a number where an answer is missing.
 */
function resolve<T>(q: QueryLike, raw: unknown, coerce: (v: unknown) => T | undefined): Field<T> {
  if (q.isError) return { state: 'error', reason: errorReason(q.error), error: q.error };
  if (q.data === undefined) {
    // Loading only while genuinely in flight; otherwise the request came back
    // with nothing usable and saying "loading" forever would be a hung spinner.
    if (q.isLoading || q.isPending) return { state: 'loading' };
    return { state: 'unavailable', reason: 'No response' };
  }
  if (raw === undefined || raw === null) {
    return { state: 'unavailable', reason: 'Not reported by the server' };
  }
  const value = coerce(raw);
  if (value === undefined) {
    return { state: 'unavailable', reason: 'Unexpected value' };
  }
  if (typeof value === 'number' && value === 0) {
    return { state: 'zero', value };
  }
  return { state: 'ready', value };
}

/** A count. `0` becomes state `zero`, never `unavailable`, and never the reverse. */
export function numField(q: QueryLike, ...paths: string[]): Field<number> {
  const raw = pick(q.data, ...paths);
  return resolve<number>(q, raw, (v) => {
    const n = typeof v === 'number' ? v : Number(v);
    return Number.isFinite(n) ? n : undefined;
  });
}

/** Money held in cents by every billing endpoint on this platform. */
export function centsField(q: QueryLike, ...paths: string[]): Field<number> {
  const f = numField(q, ...paths);
  return f.value === undefined ? f : { ...f, value: f.value / 100 };
}

export function textField(q: QueryLike, ...paths: string[]): Field<string> {
  const raw = pick(q.data, ...paths);
  return resolve<string>(q, raw, (v) =>
    typeof v === 'string' && v.trim() !== '' ? v : undefined);
}

/**
 * A list, with the same honesty. An empty array is `zero` (a real "none"),
 * a missing one is `unavailable`.
 */
export function listField<T>(q: QueryLike, ...paths: string[]): Field<T[]> {
  const data = q.data;
  if (q.isError) return { state: 'error', reason: errorReason(q.error), error: q.error };
  if (data === undefined) {
    if (q.isLoading || q.isPending) return { state: 'loading' };
    return { state: 'unavailable', reason: 'No response' };
  }
  if (Array.isArray(data)) {
    return data.length ? { state: 'ready', value: data as T[] } : { state: 'zero', value: [] };
  }
  if (data && typeof data === 'object') {
    const obj = data as Record<string, unknown>;
    for (const key of paths) {
      if (Array.isArray(obj[key])) {
        const arr = obj[key] as T[];
        return arr.length ? { state: 'ready', value: arr } : { state: 'zero', value: [] };
      }
    }
  }
  return { state: 'unavailable', reason: 'Not reported by the server' };
}

/**
 * A field the SERVER says it cannot report, with the server's own reason.
 *
 * `/god/billing/revenue` carries `mrr_unavailable_reason` per organization for
 * exactly this: an unpriced customer has no MRR, and that is neither zero
 * revenue nor a failure. Passing the server's sentence through is the whole
 * point — we do not paraphrase it and we do not replace it with a dash.
 */
export function withNotConfigured<T>(f: Field<T>, reason: string | undefined): Field<T> {
  if (!reason) return f;
  return { state: 'not_configured', reason };
}

/** The one-word label a screen shows in place of a number. */
export function stateLabel(state: FieldState): string {
  switch (state) {
    case 'unavailable': return 'Unavailable';
    case 'not_configured': return 'Not set up';
    case 'error': return 'Could not load';
    default: return '—';
  }
}

/** True when at least one field in a group failed, so a section can offer retry
 *  once rather than putting a button beside every number. */
export function anyError(...fields: Array<Field<unknown>>): boolean {
  return fields.some((f) => f.state === 'error');
}

export function anyLoading(...fields: Array<Field<unknown>>): boolean {
  return fields.some((f) => f.state === 'loading');
}
