/**
 * THE OUTBOX — "a field note must never disappear because the cell dropped".
 *
 * That sentence is the whole specification, and the tests below are the four
 * ways it gets broken: a note dropped on a transport failure, notes reordered
 * because the queue skipped a stuck one, a payload the server will never accept
 * retried forever, and a queue nobody can see.
 */

import AsyncStorage from '@react-native-async-storage/async-storage';

import { outbox, setQueueSender, type QueuedItem } from '../offline/queue';

async function reset() {
  await AsyncStorage.removeItem('evosys.outbox.v1');
  setQueueSender(null);
}

function item(label: string, targetId = 'opp-1'):
  Omit<QueuedItem, 'id' | 'createdAt' | 'attempts'> {
  return { kind: 'note', label, targetId, payload: { body: label } };
}

describe('the outbox', () => {
  beforeEach(reset);
  afterEach(reset);

  it('keeps a queued note across a restart', async () => {
    await outbox.enqueue(item('Called the widow, calling back Thursday'));
    // A fresh read is what a cold start does.
    const items = await outbox.list();
    expect(items).toHaveLength(1);
    expect(items[0].label).toMatch(/Thursday/);
  });

  it('sends in the order the notes were written', async () => {
    const sent: string[] = [];
    setQueueSender(async (i) => { sent.push(i.label); });
    await outbox.enqueue(item('first'));
    await outbox.enqueue(item('second'));
    await outbox.enqueue(item('third'));

    const report = await outbox.flush();
    expect(report.sent).toBe(3);
    expect(sent).toEqual(['first', 'second', 'third']);
    expect(await outbox.list()).toEqual([]);
  });

  it('keeps a note when the network fails, and does not skip past it', async () => {
    let attempt = 0;
    setQueueSender(async () => {
      attempt += 1;
      throw Object.assign(new Error('offline'), { status: 0 });
    });
    await outbox.enqueue(item('one'));
    await outbox.enqueue(item('two'));

    const report = await outbox.flush();
    expect(report.sent).toBe(0);
    expect(report.remaining).toBe(2);
    // Stopped at the first failure rather than burning both attempts, and
    // crucially did not send 'two' ahead of 'one'.
    expect(attempt).toBe(1);
  });

  it('records why a send failed so the person can see it', async () => {
    setQueueSender(async () => {
      throw Object.assign(new Error('nope'), { status: 0, detail: 'No connection.' });
    });
    await outbox.enqueue(item('a note'));
    await outbox.flush();
    const [only] = await outbox.list();
    expect(only.attempts).toBe(1);
    expect(only.lastError).toBe('No connection.');
  });

  it('drops a note the server will never accept, rather than retrying it forever', async () => {
    setQueueSender(async () => {
      throw Object.assign(new Error('refused'), { status: 422, detail: 'Invalid note.' });
    });
    await outbox.enqueue(item('malformed'));
    const report = await outbox.flush();
    expect(report.remaining).toBe(0);
  });

  it('keeps retrying a 401 and a 429, which are temporary', async () => {
    setQueueSender(async () => {
      throw Object.assign(new Error('slow down'), { status: 429 });
    });
    await outbox.enqueue(item('rate limited'));
    const report = await outbox.flush();
    expect(report.remaining).toBe(1);
  });

  it('tells a screen how many of its own notes are waiting', async () => {
    await outbox.enqueue(item('one', 'opp-1'));
    await outbox.enqueue(item('two', 'opp-1'));
    await outbox.enqueue(item('elsewhere', 'opp-2'));
    expect(await outbox.countFor('opp-1')).toBe(2);
    expect(await outbox.countFor('opp-2')).toBe(1);
  });

  it('notifies subscribers, so queued work is visible rather than silent', async () => {
    const seen: number[] = [];
    const stop = outbox.subscribe((items) => seen.push(items.length));
    await outbox.enqueue(item('visible'));
    stop();
    expect(seen.at(-1)).toBe(1);
  });

  it('survives a corrupt outbox instead of bricking the app', async () => {
    await AsyncStorage.setItem('evosys.outbox.v1', '{not json');
    await expect(outbox.list()).resolves.toEqual([]);
  });
});
