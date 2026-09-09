/**
 * THE EXPERIENCE RESOLVER — the file where an authorization mistake would be
 * cheapest to make and most expensive to have.
 *
 * Every test below is a way the app could accidentally invent access the server
 * never granted: a hard-coded person, a role that implies a portfolio, a
 * remembered context that outlives its membership. None of them are theoretical
 * — each one is a shortcut somebody would reasonably reach for.
 */

import {
  resolveDefaultExperience, resolveExperiences, TABS,
} from '../experience/resolve';
import type { MyContexts } from '../api/types';

function contexts(partial: Partial<MyContexts>): MyContexts {
  return {
    contexts: [],
    platform_contexts: [],
    executive_contexts: [],
    workspace_contexts: [],
    has_back_office: false,
    workspace_count: 0,
    default_context: { type: 'legacy_tenant', path: '/' },
    ...partial,
  };
}

describe('what the app believes you may do', () => {
  it('gives a plain rep exactly one experience', () => {
    const exps = resolveExperiences(contexts({
      platform_contexts: [{
        type: 'platform', label: 'EvoSys Pro Sales', role: 'sales_rep',
        brand_sales_org_id: 'bso-1', path: '/sales',
      }],
    }));
    expect(exps).toHaveLength(1);
    expect(exps[0].kind).toBe('sales');
  });

  it('reads sales_manager as the manager tab set', () => {
    const exps = resolveExperiences(contexts({
      platform_contexts: [{
        type: 'platform', label: 'EvoSys Pro Sales', role: 'sales_manager',
        brand_sales_org_id: 'bso-1', path: '/sales',
      }],
    }));
    expect(exps[0].kind).toBe('manager');
  });

  it('invents nothing from an empty context list', () => {
    expect(resolveExperiences(contexts({}))).toEqual([]);
    expect(resolveExperiences(null)).toEqual([]);
  });

  it('never conjures an executive experience from a workspace membership', () => {
    // The shortcut this guards against: "they administer an org, so they can
    // see the brand". Executive scope is portfolio assignment and nothing else.
    const exps = resolveExperiences(contexts({
      workspace_contexts: [{
        type: 'workspace', organization_id: 'org-1',
        organization_name: 'Greenland Memorial', role: 'org_admin',
        path: '/workspace/org-1',
      }],
    }));
    expect(exps.map((e) => e.kind)).toEqual(['advisor']);
  });

  it('never conjures an owner experience from an executive one', () => {
    const exps = resolveExperiences(contexts({
      executive_contexts: [{
        type: 'executive', platform_id: 'plat-1', platform_name: 'EvoSys Pro',
        role: 'brand_executive', path: '/executive',
      }],
    }));
    expect(exps.map((e) => e.kind)).toEqual(['executive']);
  });

  it('carries only the scope ids the server sent', () => {
    const exps = resolveExperiences(contexts({
      workspace_contexts: [{
        type: 'workspace', organization_id: 'org-9', organization_name: 'Acme',
        role: 'advisor', path: '/workspace/org-9',
      }],
    }));
    expect(exps[0].organizationId).toBe('org-9');
    expect(exps[0].platformId).toBeUndefined();
    expect(exps[0].brandSalesOrgId).toBeUndefined();
  });

  it('gives a multi-role person every experience the server listed, once each', () => {
    const exps = resolveExperiences(contexts({
      platform_contexts: [
        { type: 'platform', label: 'Platform Console', role: 'god_admin', path: '/god' },
        { type: 'platform', label: 'EvoSys Pro Sales', role: 'sales_manager',
          brand_sales_org_id: 'bso-1', path: '/sales' },
      ],
      executive_contexts: [{
        type: 'executive', platform_id: 'plat-1', platform_name: 'EvoSys Pro',
        role: 'brand_executive', path: '/executive',
      }],
      workspace_contexts: [{
        type: 'workspace', organization_id: 'org-1', organization_name: 'We Epic Game',
        role: 'org_admin', path: '/workspace/org-1',
      }],
    }));
    expect(exps.map((e) => e.kind)).toEqual(['manager', 'advisor', 'executive', 'owner']);
    expect(new Set(exps.map((e) => e.key)).size).toBe(exps.length);
  });
});

describe('where sign-in lands', () => {
  it('sends the owner to the platform console, not to an alphabetically-first brand', () => {
    // workspace_access.authorized_contexts is explicit about this: god's home
    // is /god regardless of what executive contexts exist.
    const ctx = contexts({
      platform_contexts: [
        { type: 'platform', label: 'Platform Console', role: 'god_admin', path: '/god' },
      ],
      executive_contexts: [
        { type: 'executive', platform_id: 'aaa', platform_name: 'AAA Brand',
          role: 'brand_executive', path: '/executive' },
      ],
      default_context: { type: 'platform', path: '/god' },
    });
    const exps = resolveExperiences(ctx);
    expect(resolveDefaultExperience(ctx, exps)?.kind).toBe('owner');
  });

  it('honours an executive default, including which brand', () => {
    const ctx = contexts({
      executive_contexts: [
        { type: 'executive', platform_id: 'p1', platform_name: 'One',
          role: 'brand_executive', path: '/executive' },
        { type: 'executive', platform_id: 'p2', platform_name: 'Two',
          role: 'brand_executive', path: '/executive' },
      ],
      default_context: { type: 'executive', path: '/executive', platform_id: 'p2' },
    });
    const exps = resolveExperiences(ctx);
    expect(resolveDefaultExperience(ctx, exps)?.platformId).toBe('p2');
  });

  it('lands in the named workspace when the server points at one', () => {
    const ctx = contexts({
      workspace_contexts: [
        { type: 'workspace', organization_id: 'org-a', organization_name: 'A',
          role: 'advisor', path: '/workspace/org-a' },
        { type: 'workspace', organization_id: 'org-b', organization_name: 'B',
          role: 'advisor', path: '/workspace/org-b' },
      ],
      default_context: { type: 'workspace', path: '/workspace/org-b' },
    });
    const exps = resolveExperiences(ctx);
    expect(resolveDefaultExperience(ctx, exps)?.organizationId).toBe('org-b');
  });

  it('returns nothing when there is nothing to return to', () => {
    expect(resolveDefaultExperience(contexts({}), [])).toBeNull();
  });
});

describe('tab sets', () => {
  it('keeps every experience at five destinations or fewer', () => {
    for (const [kind, tabs] of Object.entries(TABS)) {
      expect(tabs.length).toBeLessThanOrEqual(5);
      expect(tabs.length).toBeGreaterThan(2);
      // The last one is always More, so the fifth tap target is predictable.
      expect(tabs[tabs.length - 1].name).toBe('more');
      expect(kind).toBeTruthy();
    }
  });
});
