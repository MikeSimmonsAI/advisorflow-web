/**
 * Who is signed in, on this device.
 *
 * THE SEQUENCE, AND WHY IT IS THIS ORDER
 * --------------------------------------
 *   1. POST /auth/login             → a token, minted against a session ROW for
 *                                     this install (X-Device-Id in the headers)
 *   2. store it in expo-secure-store (Keychain / Keystore, never AsyncStorage)
 *   3. GET /auth/my-contexts        → the authorised experience list
 *   4. render the tab set the SERVER described
 *
 * Step 3 is not a formality and its result is never cached to disk. The app
 * stores a token and NOTHING derived from it: no role, no "is manager" flag, no
 * remembered portfolio. A stale flag on a phone that has been in a drawer for a
 * month is how a revoked executive still sees an Executive tab, and the tab is
 * the least of it — the fetch behind it would be refused, and the person would
 * read a refusal as a bug.
 *
 * REFRESH IS SAFE NOW AND WAS NOT BEFORE. Until per-device sessions landed,
 * /auth/refresh rotated the single `users.session_token`, so refreshing on the
 * phone signed the desktop out and vice versa. It now rotates this device's own
 * row. That is why this provider refreshes on foreground at all.
 */

import React, {
  createContext, useCallback, useContext, useEffect, useMemo, useRef, useState,
} from 'react';
import { AppState, AppStateStatus } from 'react-native';

import { auth as authApi } from '../api/endpoints';
import { ApiError, setRequestContext, setUnauthorizedHandler } from '../api/client';
import type { MyContexts } from '../api/types';
import { secureStore } from './secureStore';
import { biometrics } from './biometrics';

type AuthStatus = 'loading' | 'signed_out' | 'locked' | 'signed_in';

type AuthValue = {
  status: AuthStatus;
  contexts: MyContexts | null;
  fullName: string | null;
  mustChangePassword: boolean;
  error: string | null;
  signIn: (email: string, password: string) => Promise<void>;
  signOut: () => Promise<void>;
  signOutEverywhere: () => Promise<void>;
  unlock: () => Promise<boolean>;
  reloadContexts: () => Promise<void>;
};

const AuthContext = createContext<AuthValue | null>(null);

/** How long the app may sit in the background before it asks for a face again.
 *  Two minutes: long enough to take a call mid-appointment, short enough that a
 *  phone left on a table is not an open session. */
const RELOCK_AFTER_MS = 2 * 60 * 1000;

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [status, setStatus] = useState<AuthStatus>('loading');
  const [contexts, setContexts] = useState<MyContexts | null>(null);
  const [fullName, setFullName] = useState<string | null>(null);
  const [mustChangePassword, setMustChangePassword] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const backgroundedAt = useRef<number | null>(null);

  // ── the single 401 handler ────────────────────────────────────────────────
  //
  // Registered once, here. A 401 means the server has already decided this
  // credential is dead: there is nothing to retry, and retrying is how an app
  // ends up hammering a dead session. Clear and drop to sign-in.
  const handleUnauthorized = useCallback(() => {
    void secureStore.clearSession();
    setRequestContext({});
    setContexts(null);
    setStatus('signed_out');
  }, []);

  useEffect(() => {
    setUnauthorizedHandler(handleUnauthorized);
    return () => setUnauthorizedHandler(null);
  }, [handleUnauthorized]);

  const loadContexts = useCallback(async () => {
    const next = await authApi.myContexts();
    setContexts(next);
    return next;
  }, []);

  const reloadContexts = useCallback(async () => {
    try {
      await loadContexts();
    } catch (err) {
      if (err instanceof ApiError && err.offline) return;  // keep what we have
      throw err;
    }
  }, [loadContexts]);

  // ── cold start ────────────────────────────────────────────────────────────
  useEffect(() => {
    let cancelled = false;
    (async () => {
      const token = await secureStore.getToken();
      if (!token) {
        if (!cancelled) setStatus('signed_out');
        return;
      }
      const wantsBiometric = await secureStore.getBiometricPreference();
      if (wantsBiometric && (await biometrics.isAvailable())) {
        if (!cancelled) setStatus('locked');
        return;
      }
      try {
        await loadContexts();
        if (!cancelled) setStatus('signed_in');
      } catch (err) {
        // A 401 has already been handled globally. Anything else — no signal on
        // a cold start being the common one — must not sign the person out: the
        // token is still valid and the app is simply offline.
        if (!cancelled) setStatus(err instanceof ApiError && err.status === 401
          ? 'signed_out' : 'signed_in');
      }
    })();
    return () => { cancelled = true; };
  }, [loadContexts]);

  // ── foreground: relock, then refresh this device's session ────────────────
  useEffect(() => {
    const sub = AppState.addEventListener('change', (next: AppStateStatus) => {
      if (next === 'background' || next === 'inactive') {
        backgroundedAt.current = Date.now();
        return;
      }
      if (next !== 'active') return;

      const away = backgroundedAt.current ? Date.now() - backgroundedAt.current : 0;
      backgroundedAt.current = null;

      void (async () => {
        if (away > RELOCK_AFTER_MS && (await secureStore.getBiometricPreference())
            && (await biometrics.isAvailable())) {
          setStatus((s) => (s === 'signed_in' ? 'locked' : s));
          return;
        }
        // Rotates THIS device's session row. Other devices are untouched — the
        // property that makes a phone and a desktop able to coexist at all.
        try {
          const res = await authApi.refresh();
          if (res?.access_token) await secureStore.setToken(res.access_token);
        } catch {
          /* offline, or the session ended; the 401 handler covers the latter */
        }
      })();
    });
    return () => sub.remove();
  }, []);

  const signIn = useCallback(async (email: string, password: string) => {
    setError(null);
    try {
      const res = await authApi.login(email.trim(), password);
      await secureStore.setToken(res.access_token);
      setFullName(res.full_name ?? null);
      setMustChangePassword(!!res.must_change_password);
      await loadContexts();
      setStatus('signed_in');
    } catch (err) {
      const message = err instanceof ApiError
        ? err.detail
        : 'Could not sign in. Please try again.';
      setError(message);
      throw err;
    }
  }, [loadContexts]);

  const signOut = useCallback(async () => {
    // THIS DEVICE ONLY. Signing out of the phone must not sign the person out
    // of the desktop they left running at the office — that was the old
    // behaviour, and it was not a choice anybody made.
    try {
      await authApi.logout();
    } catch {
      /* the local credential is discarded either way */
    }
    await secureStore.clearSession();
    setRequestContext({});
    setContexts(null);
    setStatus('signed_out');
  }, []);

  const signOutEverywhere = useCallback(async () => {
    try {
      await authApi.logoutAll();
    } catch {
      /* same */
    }
    await secureStore.clearSession();
    setRequestContext({});
    setContexts(null);
    setStatus('signed_out');
  }, []);

  const unlock = useCallback(async () => {
    const ok = await biometrics.authenticate('Unlock EvoSys Pro');
    if (!ok) return false;
    try {
      await loadContexts();
      setStatus('signed_in');
    } catch {
      setStatus('signed_in');   // offline unlock is still an unlock
    }
    return true;
  }, [loadContexts]);

  const value = useMemo<AuthValue>(() => ({
    status, contexts, fullName, mustChangePassword, error,
    signIn, signOut, signOutEverywhere, unlock, reloadContexts,
  }), [status, contexts, fullName, mustChangePassword, error,
       signIn, signOut, signOutEverywhere, unlock, reloadContexts]);

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthValue {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error('useAuth must be used inside AuthProvider');
  return ctx;
}
