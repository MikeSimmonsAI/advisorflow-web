/**
 * Dates and times, said the way a person standing in a car park reads them.
 *
 * "2:30 PM" and "Tomorrow, 9:00 AM" — never an ISO string, never a relative
 * time so vague it cannot be acted on ("in a while"). The one place a raw
 * timestamp survives is a tooltip nobody has on a phone, so there is no reason
 * to render one.
 *
 * Everything tolerates null and returns a dash rather than "Invalid Date".
 * A record with a missing timestamp is normal; a screen that crashes on one is
 * not.
 */

const TIME: Intl.DateTimeFormatOptions = { hour: 'numeric', minute: '2-digit' };
const DAY: Intl.DateTimeFormatOptions = { weekday: 'short', month: 'short', day: 'numeric' };

export function parseDate(value: unknown): Date | null {
  if (!value) return null;
  const d = new Date(value as string);
  return Number.isNaN(d.getTime()) ? null : d;
}

function sameDay(a: Date, b: Date): boolean {
  return a.getFullYear() === b.getFullYear()
    && a.getMonth() === b.getMonth()
    && a.getDate() === b.getDate();
}

export function timeOf(value: unknown): string {
  const d = parseDate(value);
  return d ? d.toLocaleTimeString(undefined, TIME) : '—';
}

export function dayOf(value: unknown): string {
  const d = parseDate(value);
  return d ? d.toLocaleDateString(undefined, DAY) : '—';
}

/** "Today, 2:30 PM" / "Tomorrow, 9:00 AM" / "Thu, Sep 11, 9:00 AM". */
export function whenOf(value: unknown): string {
  const d = parseDate(value);
  if (!d) return '—';
  const now = new Date();
  const tomorrow = new Date(now);
  tomorrow.setDate(now.getDate() + 1);

  const time = d.toLocaleTimeString(undefined, TIME);
  if (sameDay(d, now)) return `Today, ${time}`;
  if (sameDay(d, tomorrow)) return `Tomorrow, ${time}`;
  return `${d.toLocaleDateString(undefined, DAY)}, ${time}`;
}

/** "3 days ago" / "in 2 hours". Used for last-contact and follow-up ages,
 *  where the exact minute is noise and the order of magnitude is the point. */
export function relativeOf(value: unknown): string {
  const d = parseDate(value);
  if (!d) return '—';
  const diffMs = d.getTime() - Date.now();
  const past = diffMs < 0;
  const abs = Math.abs(diffMs);

  const minutes = Math.round(abs / 60000);
  if (minutes < 1) return 'just now';
  if (minutes < 60) return past ? `${minutes} min ago` : `in ${minutes} min`;

  const hours = Math.round(minutes / 60);
  if (hours < 24) return past ? `${hours}h ago` : `in ${hours}h`;

  const days = Math.round(hours / 24);
  if (days < 30) return past ? `${days}d ago` : `in ${days}d`;

  const months = Math.round(days / 30);
  return past ? `${months}mo ago` : `in ${months}mo`;
}

/** ISO date (YYYY-MM-DD) for the query parameters the scheduling routes take. */
export function isoDate(d: Date): string {
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, '0');
  const day = String(d.getDate()).padStart(2, '0');
  return `${y}-${m}-${day}`;
}

export function addDays(d: Date, n: number): Date {
  const out = new Date(d);
  out.setDate(out.getDate() + n);
  return out;
}

/** A person's name from whichever fields a record happens to carry. */
export function nameOf(record: Record<string, unknown> | null | undefined,
                       fallback = 'Unnamed'): string {
  if (!record) return fallback;
  const full = record.full_name ?? record.name ?? record.company_name;
  if (typeof full === 'string' && full.trim()) return full.trim();
  const first = typeof record.first_name === 'string' ? record.first_name : '';
  const last = typeof record.last_name === 'string' ? record.last_name : '';
  const joined = `${first} ${last}`.trim();
  return joined || fallback;
}
