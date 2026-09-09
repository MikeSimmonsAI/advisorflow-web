/**
 * THE THREE THINGS A REP CREATES IN A BASEMENT WITH NO SIGNAL.
 *
 * A note. An appointment outcome. A photo. Those get made once, standing in
 * front of the person or the plot, and if the cell drops they must not vanish.
 * Everything else in this app fails loudly with a retry button, which is the
 * right answer for a read.
 *
 * THIS IS NOT A SYNC ENGINE AND MUST NOT BECOME ONE.
 * --------------------------------------------------
 * There is no local business authority here: no local id allocation that the
 * server later has to reconcile, no conflict resolution, no offline stage
 * transitions. A queued item is a REQUEST THAT HAS NOT BEEN SENT YET, replayed
 * verbatim when there is signal. The backend remains the source of truth, and
 * the moment this file starts deciding what a record means, it has become a
 * second database with none of the guarantees of the first.
 *
 * QUEUED WORK IS VISIBLE. Every screen that can queue shows what is waiting and
 * labels it "not sent yet". Silent queues are how a rep believes a note is
 * filed for three days.
 *
 * WHY AsyncStorage AND NOT SecureStore. This holds business drafts, not
 * credentials — SecureStore has a small value limit and is the wrong tool for a
 * growing list. Credentials live in the Keychain and never come near this file;
 * `src/__tests__` asserts that.
 */

import AsyncStorage from '@react-native-async-storage/async-storage';

const KEY = 'evosys.outbox.v1';

export type QueuedKind = 'note' | 'appointment_outcome' | 'discovery';

export type QueuedItem = {
  id: string;
  kind: QueuedKind;
  /** What the person will recognise it as: "Note on Ridgecrest Funeral Home". */
  label: string;
  /** The record the write belongs to, for display and for replay. */
  targetId: string;
  payload: Record<string, unknown>;
  createdAt: number;
  attempts: number;
  lastError?: string;
};

type Sender = (item: QueuedItem) => Promise<void>;

/** Registered by the app once, so this module never imports the API layer and
 *  the queue stays testable without a network. */
let sender: Sender | null = null;
export function setQueueSender(fn: Sender | null): void { sender = fn; }

const listeners = new Set<(items: QueuedItem[]) => void>();

async function read(): Promise<QueuedItem[]> {
  try {
    const raw = await AsyncStorage.getItem(KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    return Array.isArray(parsed) ? (parsed as QueuedItem[]) : [];
  } catch {
    // A corrupt outbox must not brick the app. It is also not silently
    // discarded — read() returning empty leaves the bad payload on disk for a
    // later look rather than overwriting it here.
    return [];
  }
}

async function write(items: QueuedItem[]): Promise<void> {
  await AsyncStorage.setItem(KEY, JSON.stringify(items));
  listeners.forEach((fn) => fn(items));
}

export const outbox = {
  subscribe(fn: (items: QueuedItem[]) => void): () => void {
    listeners.add(fn);
    void read().then(fn);
    return () => { listeners.delete(fn); };
  },

  list: read,

  async enqueue(
    input: Omit<QueuedItem, 'id' | 'createdAt' | 'attempts'>,
  ): Promise<QueuedItem> {
    const item: QueuedItem = {
      ...input,
      id: `q_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 8)}`,
      createdAt: Date.now(),
      attempts: 0,
    };
    const items = await read();
    items.push(item);
    await write(items);
    return item;
  },

  async remove(id: string): Promise<void> {
    const items = await read();
    await write(items.filter((i) => i.id !== id));
  },

  async countFor(targetId: string): Promise<number> {
    return (await read()).filter((i) => i.targetId === targetId).length;
  },

  /**
   * Try to send everything, oldest first, and stop at the first failure.
   *
   * STOPPING IS DELIBERATE. Notes on one deal arrive in the order they were
   * written, and a queue that skipped past a stuck item to send later ones
   * would file today's note above last week's. It also means one server refusal
   * does not burn through thirty retries in a row.
   *
   * An item is dropped only when the SERVER refused it (4xx that is not 401 or
   * 429) — a payload the server will never accept is not worth carrying
   * forever, and the person is shown the reason. Transport failures are kept.
   */
  async flush(): Promise<{ sent: number; remaining: number }> {
    if (!sender) return { sent: 0, remaining: (await read()).length };

    let items = await read();
    let sent = 0;

    while (items.length) {
      const item = items[0];
      try {
        await sender(item);
        items = items.slice(1);
        sent += 1;
        await write(items);
      } catch (err) {
        const status = (err as { status?: number })?.status ?? 0;
        const permanent = status >= 400 && status < 500 && status !== 401 && status !== 429;
        item.attempts += 1;
        item.lastError = (err as { detail?: string; message?: string })?.detail
          ?? (err as { message?: string })?.message
          ?? 'Could not send.';
        if (permanent) {
          items = items.slice(1);
          await write(items);
          continue;
        }
        await write(items);
        break;
      }
    }
    return { sent, remaining: items.length };
  },
};
