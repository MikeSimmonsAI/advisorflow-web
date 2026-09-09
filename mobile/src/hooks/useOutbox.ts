/**
 * Wires the outbox to the API, and drains it when the app comes back.
 *
 * The queue module deliberately does not import the API layer — it is a store
 * with a replay loop and it stays testable without a network. This hook is the
 * one place the two meet.
 *
 * DRAINING ON FOREGROUND IS THE WHOLE POINT. A note written in a basement is
 * sent when the rep walks back to the car, without anybody remembering to press
 * anything. A queue that only flushes when a person finds the button is a queue
 * that holds a note for three days.
 */

import { useEffect } from 'react';
import { AppState } from 'react-native';

import { sales } from '../api/endpoints';
import { outbox, setQueueSender, type QueuedItem } from '../offline/queue';
import { useAuth } from '../auth/AuthContext';

async function send(item: QueuedItem): Promise<void> {
  switch (item.kind) {
    case 'note':
      await sales.addNote(item.targetId, item.payload);
      return;
    case 'discovery':
      await sales.upsertDiscovery(item.targetId, item.payload);
      return;
    case 'appointment_outcome':
      // Routed through the same endpoint the live path uses, so a queued
      // outcome and an immediate one are the same write.
      await sales.patchOpportunity(item.targetId, item.payload);
      return;
    default:
      // An unknown kind is a payload from an older build of this app. Throwing
      // a 4xx-shaped error retires it rather than leaving it stuck at the head
      // of the queue blocking everything written after it.
      throw Object.assign(new Error('Unknown queued item'), { status: 422 });
  }
}

export function useOutbox(): void {
  const { status } = useAuth();

  useEffect(() => {
    setQueueSender(send);
    return () => setQueueSender(null);
  }, []);

  useEffect(() => {
    if (status !== 'signed_in') return undefined;

    void outbox.flush();
    const sub = AppState.addEventListener('change', (next) => {
      if (next === 'active') void outbox.flush();
    });
    return () => sub.remove();
  }, [status]);
}
