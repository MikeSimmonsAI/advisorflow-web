/**
 * The active experience, and what changing it must drag along with it.
 *
 * SWITCHING IS FOUR THINGS, NOT ONE. Getting any of them wrong is a
 * cross-tenant bug rather than a cosmetic one:
 *
 *   1. THE TAB SET changes, because the destinations differ per experience.
 *   2. THE REQUEST CONTEXT changes: `X-Workspace-Id` is SET entering a customer
 *      workspace and CLEARED leaving it. An executive screen that still carried
 *      a workspace header would ask the server to scope a brand-wide read to
 *      one customer.
 *   3. THE QUERY CACHE IS DROPPED. This is a TENANT ISOLATION CONTROL, not a
 *      performance detail. Without it, entering Org B shows Org A's leads for
 *      as long as the stale data survives — real data, correctly fetched, shown
 *      under the wrong company's name.
 *   4. THE SERVER CONFIRMS MEMBERSHIP before the first workspace screen paints,
 *      via GET /auth/workspace/{id}. Typing a URL and tapping a button get the
 *      same answer, which is the only arrangement worth having.
 *
 * And the thing it never does: GRANT. The switcher chooses among scopes the
 * server already returned. Selecting one changes what is asked for, never what
 * is allowed.
 */

import React, {
  createContext, useCallback, useContext, useEffect, useMemo, useState,
} from 'react';
import { useQueryClient } from '@tanstack/react-query';

import { auth as authApi } from '../api/endpoints';
import { setRequestContext } from '../api/client';
import { useAuth } from '../auth/AuthContext';
import { secureStore } from '../auth/secureStore';
import {
  Experience, resolveDefaultExperience, resolveExperiences,
} from './resolve';

type ExperienceValue = {
  experiences: Experience[];
  active: Experience | null;
  switching: boolean;
  switchError: string | null;
  switchTo: (key: string) => Promise<boolean>;
};

const ExperienceContext = createContext<ExperienceValue | null>(null);

export function ExperienceProvider({ children }: { children: React.ReactNode }) {
  const { contexts, status } = useAuth();
  const queryClient = useQueryClient();

  const [activeKey, setActiveKey] = useState<string | null>(null);
  const [switching, setSwitching] = useState(false);
  const [switchError, setSwitchError] = useState<string | null>(null);

  const experiences = useMemo(() => resolveExperiences(contexts), [contexts]);

  const active = useMemo(
    () => experiences.find((e) => e.key === activeKey) ?? null,
    [experiences, activeKey],
  );

  /** Everything a request carries on this experience's behalf. Rebuilt whole
   *  on every switch — a partial update is how a stale workspace id survives
   *  into a context that must not send one. */
  const applyContext = useCallback((exp: Experience | null) => {
    setRequestContext({
      workspaceOrgId: exp?.kind === 'advisor' ? exp.organizationId ?? null : null,
      brandOverride: null,
    });
  }, []);

  // ── first landing after sign-in ───────────────────────────────────────────
  useEffect(() => {
    if (status !== 'signed_in' || !experiences.length) return;
    if (activeKey && experiences.some((e) => e.key === activeKey)) return;

    let cancelled = false;
    void (async () => {
      // A remembered experience is a CONVENIENCE, and it is re-validated
      // against the freshly fetched list every launch. An experience that was
      // revoked while the phone was in a drawer simply is not in `experiences`
      // and cannot be restored, which is the whole reason the remembered value
      // is a key and not a set of permissions.
      const remembered = await secureStore.getLastContext();
      const restored = remembered
        ? experiences.find((e) => e.key === remembered)
        : undefined;
      const chosen = restored ?? resolveDefaultExperience(contexts, experiences);
      if (cancelled || !chosen) return;
      applyContext(chosen);
      setActiveKey(chosen.key);
    })();
    return () => { cancelled = true; };
  }, [status, experiences, activeKey, contexts, applyContext]);

  // Signing out clears the selection along with everything else.
  useEffect(() => {
    if (status === 'signed_out') {
      setActiveKey(null);
      setRequestContext({});
      queryClient.clear();
    }
  }, [status, queryClient]);

  const switchTo = useCallback(async (key: string): Promise<boolean> => {
    const next = experiences.find((e) => e.key === key);
    if (!next) {
      // Not "permission denied" — this experience is not in the server's list,
      // so as far as this app is concerned it does not exist.
      setSwitchError('That experience is not available on this account.');
      return false;
    }
    if (next.key === activeKey) return true;

    setSwitching(true);
    setSwitchError(null);
    try {
      // DROP THE CACHE BEFORE ANYTHING ELSE. If the switch fails halfway, the
      // safe end state is "no data" rather than "the previous scope's data
      // under the new scope's heading".
      queryClient.clear();
      applyContext(next);

      if (next.kind === 'advisor' && next.organizationId) {
        // The server confirms the membership before the first screen paints.
        await authApi.enterWorkspace(next.organizationId);
      }

      setActiveKey(next.key);
      await secureStore.setLastContext(next.key);
      return true;
    } catch (err) {
      // Refused: fall back to the experience that was working, with its own
      // context restored and the cache still empty.
      applyContext(active);
      setSwitchError(
        err instanceof Error ? err.message : 'Could not enter that workspace.',
      );
      return false;
    } finally {
      setSwitching(false);
    }
  }, [experiences, activeKey, active, applyContext, queryClient]);

  const value = useMemo<ExperienceValue>(
    () => ({ experiences, active, switching, switchError, switchTo }),
    [experiences, active, switching, switchError, switchTo],
  );

  return (
    <ExperienceContext.Provider value={value}>{children}</ExperienceContext.Provider>
  );
}

export function useExperience(): ExperienceValue {
  const ctx = useContext(ExperienceContext);
  if (!ctx) throw new Error('useExperience must be used inside ExperienceProvider');
  return ctx;
}

/** The active experience, or a throw. Screens inside a tab group can rely on
 *  one existing — the group only renders when it does. */
export function useActiveExperience(): Experience {
  const { active } = useExperience();
  if (!active) throw new Error('No active experience');
  return active;
}
