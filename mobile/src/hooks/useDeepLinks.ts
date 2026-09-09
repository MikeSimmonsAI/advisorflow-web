/**
 * Incoming links, held until there is somewhere to send them.
 *
 * A universal link can arrive before the app has a session — tapping an
 * appointment link from an email on a cold phone is the normal case, not the
 * edge case. Navigating then would land on the sign-in screen and lose the
 * destination, so the link is PARKED and replayed once the person is in.
 *
 * The destination is still only a route. `src/deeplinks.ts` says why that is
 * safe: the screen fetches the record through the normal authorised endpoint,
 * and the server decides.
 */

import { useEffect, useRef } from 'react';
import * as Linking from 'expo-linking';
import { router } from 'expo-router';

import { routeForUrl } from '../deeplinks';
import { useAuth } from '../auth/AuthContext';

export function useDeepLinks(): void {
  const { status } = useAuth();
  const pending = useRef<string | null>(null);

  useEffect(() => {
    let cancelled = false;

    function handle(url: string | null) {
      if (!url) return;
      const target = routeForUrl(url);
      if (!target) return;          // web-only or unrecognised: leave it alone
      pending.current = target;
      flush();
    }

    function flush() {
      if (status !== 'signed_in' || !pending.current || cancelled) return;
      const target = pending.current;
      pending.current = null;
      router.push(target as never);
    }

    void Linking.getInitialURL().then(handle);
    const sub = Linking.addEventListener('url', ({ url }) => handle(url));
    flush();

    return () => { cancelled = true; sub.remove(); };
  }, [status]);
}
