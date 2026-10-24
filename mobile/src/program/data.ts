/** Existing SCI Live endpoints, with no new service and no write operations.
 * Authorization is enforced by each backend route and X-Workspace-Id.
 */
import { api } from '../api/client';

export type ProgramInboxItem = {
  lead_id: string;
  response_id?: string;
  name: string;
  location?: string | null;
  summary?: string | null;
  channel_label?: string;
  channel?: string;
  badge?: string;
  owner?: string;
  handling_status?: string;
  received_at?: string | null;
  sla_breached?: boolean;
};
export type ProgramTask = { id?: string; message?: string; title?: string; description?: string; [key: string]: unknown };
export type ProgramOverview = {
  counts: { needs_attention: number; replies_today: number; visits_booked: number; contacted_today: number };
  inbox: ProgramInboxItem[];
  tasks: ProgramTask[];
  owner_alert?: { count: number; text: string } | null;
  upcoming?: { callbacks?: number; [key: string]: unknown };
  locations?: Array<{ id: string; name: string }>;
  updated_at?: string;
};
export type ProgramPipeline = {
  loaded: number; not_yet_contacted: number; in_outreach: number;
  enrolled_today: number; replies_today: number;
  ai_conversations: number; appointments_offered: number; appointments_confirmed: number;
  first_touches_today?: { sent: number; delivered: number; failed: number };
  later_touches_today?: { sent: number; delivered: number; failed: number };
  failed_today?: { count: number; reasons: Record<string, number> };
  updated_at?: string;
};
export type ProgramVisit = {
  id: string; lead_id: string; name: string; location?: string | null;
  when: string; status: string; label?: string | null;
};
export type FamilyThread = {
  who: 'family' | 'us' | 'ai'; text?: string | null;
  channel?: string; delivery?: string; at?: string | null;
};
export type ProgramFamily = {
  lead_id: string; name: string; location?: string | null; campaign?: string | null;
  badge?: string | null; status?: string | null;
  setter?: { state?: string; offered?: string[]; handoff_reason?: string } | null;
  booking?: { when?: string; label?: string; status?: string } | null;
  thread: FamilyThread[];
};

export const program = {
  overview: (filter: 'all' | 'needs_reply' = 'all') =>
    api.get<ProgramOverview>('/program/command?filter=' + filter + '&limit=40'),
  pipeline: () => api.get<ProgramPipeline>('/program/command/pipeline'),
  visits: () => api.get<{ items: ProgramVisit[] }>('/program/appointments?when=upcoming'),
  family: (id: string) => api.get<ProgramFamily>('/program/command/family/' + encodeURIComponent(id)),
};

export const count = (x: unknown): number | null => {
  if (typeof x === 'number' && Number.isFinite(x)) return x;
  return null;
};
export const shortTime = (raw?: string | null): string => {
  if (!raw) return 'Time not available';
  const date = new Date(raw);
  if (Number.isNaN(date.getTime())) return 'Time not available';
  return date.toLocaleString(undefined, {
    month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit',
  });
};
export const safeText = (raw?: string | null, fallback = 'No message preview'): string =>
  raw && raw.trim() ? raw.trim() : fallback;
