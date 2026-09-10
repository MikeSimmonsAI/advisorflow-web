/**
 * TURNING /auth/my-contexts INTO A TAB SET.
 *
 * THE SERVER DECIDES; THIS FILE RENDERS. Read that literally:
 *
 *   - There is no `if (email === ...)`. There is no hard-coded person.
 *   - There is no `if (role === 'executive') → all organisations`. Executive
 *     scope is portfolio assignment, resolved server-side, and this file never
 *     asks the question.
 *   - An experience absent from `contexts` does not exist to this app. Not
 *     hidden — absent. There is no code path that constructs one.
 *   - Nothing here grants anything. The switcher SELECTS among scopes the
 *     server already granted, and every request behind every tab is authorised
 *     again independently. Hiding a button and refusing a request are two
 *     separate answers to the same question, and the second one is the control.
 *
 * The one piece of interpretation this file does perform is splitting a
 * `platform` brand-sales context into SALES or MANAGER by its `role` field —
 * `sales_rep` vs `sales_manager`, the same two strings `sales_access.py` uses.
 * That is a presentation choice about which five tabs to draw, and it cannot
 * widen anything: a rep who edited this to say "manager" would get a Team tab
 * whose every fetch returns 403 from `require_sales_manager`.
 */

import type { AuthorizedContext, MyContexts } from '../api/types';
import { ROLE_SALES_MANAGER } from '../vocab';

export type ExperienceKind =
  | 'sales'
  | 'manager'
  | 'advisor'
  | 'executive'
  | 'owner';

export type Experience = {
  /** Stable identity for caching and for the switcher's selection. */
  key: string;
  kind: ExperienceKind;
  label: string;
  /** Sub-label shown under the name in the switcher, e.g. the brand. */
  detail?: string;
  /** The scope this experience operates in, if it has one. */
  brandSalesOrgId?: string;
  platformId?: string;
  organizationId?: string;
  /** The membership role the server reported, verbatim. */
  role?: string;
  /** The route group this experience owns. */
  route: string;
};

const ROUTES: Record<ExperienceKind, string> = {
  sales: '/(sales)/my-day',
  manager: '/(manager)/home',
  advisor: '/(advisor)/home',
  executive: '/(exec)/command',
  owner: '/(owner)/command',
};

function platformExperience(ctx: AuthorizedContext): Experience | null {
  // The owner's Platform Console. `path: '/god'` is how the server marks it.
  if (ctx.path === '/god') {
    return {
      key: 'owner',
      kind: 'owner',
      label: 'Owner Command',
      detail: 'Platform',
      role: ctx.role,
      route: ROUTES.owner,
    };
  }
  // A brand-sales membership. Manager is a superset of rep — a manager sells
  // personally too (sales_access.is_sales_member returns true for both), so the
  // manager tab set includes the rep screens rather than replacing them.
  const isManager = ctx.role === ROLE_SALES_MANAGER;
  const kind: ExperienceKind = isManager ? 'manager' : 'sales';
  return {
    key: `${kind}:${ctx.brand_sales_org_id ?? 'default'}`,
    kind,
    label: isManager ? 'Sales Manager' : 'Sales',
    detail: ctx.label,
    brandSalesOrgId: ctx.brand_sales_org_id,
    role: ctx.role,
    route: ROUTES[kind],
  };
}

function executiveExperience(ctx: AuthorizedContext): Experience {
  return {
    key: `executive:${ctx.platform_id ?? 'default'}`,
    kind: 'executive',
    label: 'Executive',
    detail: ctx.platform_name ?? ctx.label,
    platformId: ctx.platform_id,
    role: ctx.role,
    route: ROUTES.executive,
  };
}

function workspaceExperience(ctx: AuthorizedContext): Experience {
  return {
    key: `advisor:${ctx.organization_id}`,
    kind: 'advisor',
    label: ctx.organization_name ?? ctx.label ?? 'Workspace',
    detail: 'Customer workspace',
    organizationId: ctx.organization_id,
    role: ctx.role,
    route: ROUTES.advisor,
  };
}

/**
 * Every experience this caller may enter, derived from the server's list and
 * from nothing else. Order is the switcher's order: the day-to-day work first,
 * oversight after it, owner last.
 */
export function resolveExperiences(contexts: MyContexts | null): Experience[] {
  if (!contexts) return [];
  const out: Experience[] = [];

  for (const ctx of contexts.platform_contexts ?? []) {
    const exp = platformExperience(ctx);
    if (exp) out.push(exp);
  }
  for (const ctx of contexts.executive_contexts ?? []) {
    out.push(executiveExperience(ctx));
  }
  for (const ctx of contexts.workspace_contexts ?? []) {
    out.push(workspaceExperience(ctx));
  }

  const rank: Record<ExperienceKind, number> = {
    sales: 0, manager: 1, advisor: 2, executive: 3, owner: 4,
  };
  return out.sort((a, b) => rank[a.kind] - rank[b.kind]);
}

/**
 * Where sign-in lands.
 *
 * `default_context.path` is the server's answer and it is honoured first —
 * including god → `/god`, which `workspace_access.authorized_contexts` is
 * explicit about: the owner's home is the Platform Console regardless of what
 * executive contexts exist, and alphabetical platform ordering must never
 * decide it.
 */
export function resolveDefaultExperience(
  contexts: MyContexts | null,
  experiences: Experience[],
): Experience | null {
  if (!experiences.length) return null;
  const path = contexts?.default_context?.path;

  if (path === '/god') {
    return experiences.find((e) => e.kind === 'owner') ?? experiences[0];
  }
  if (path === '/executive') {
    const platformId = contexts?.default_context?.platform_id;
    return experiences.find(
      (e) => e.kind === 'executive' && (!platformId || e.platformId === platformId),
    ) ?? experiences.find((e) => e.kind === 'executive') ?? experiences[0];
  }
  if (path === '/sales') {
    return experiences.find((e) => e.kind === 'sales' || e.kind === 'manager')
      ?? experiences[0];
  }
  if (path?.startsWith('/workspace/')) {
    const orgId = path.slice('/workspace/'.length);
    return experiences.find((e) => e.organizationId === orgId) ?? experiences[0];
  }
  return experiences[0];
}

/** Tab sets. Five destinations, the fifth always More — a phone with seven
 *  tabs has no tab a thumb can reliably hit. */
export const TABS: Record<ExperienceKind, Array<{ name: string; title: string }>> = {
  sales: [
    { name: 'my-day', title: 'My Day' },
    { name: 'leads', title: 'Leads' },
    { name: 'pipeline', title: 'Pipeline' },
    { name: 'calendar', title: 'Calendar' },
    { name: 'more', title: 'More' },
  ],
  manager: [
    { name: 'home', title: 'Today' },
    { name: 'pipeline', title: 'Pipeline' },
    { name: 'team', title: 'Team' },
    { name: 'approvals', title: 'Approvals' },
    { name: 'more', title: 'More' },
  ],
  advisor: [
    { name: 'home', title: 'Home' },
    { name: 'leads', title: 'Leads' },
    { name: 'calendar', title: 'Calendar' },
    { name: 'more', title: 'More' },
  ],
  executive: [
    { name: 'command', title: 'Command' },
    { name: 'organizations', title: 'Orgs' },
    { name: 'revenue', title: 'Revenue' },
    { name: 'more', title: 'More' },
  ],
  owner: [
    { name: 'command', title: 'Command' },
    { name: 'customers', title: 'Customers' },
    { name: 'activity', title: 'Activity' },
    { name: 'more', title: 'More' },
  ],
};

/**
 * ROUTES THAT EXIST BUT ARE NOT TABS.
 *
 * A PERMANENT TAB IS A PROMISE THAT SOMETHING IS ALWAYS THERE. Issues held one
 * of the owner's four, and it was empty almost every day — so a quarter of the
 * navigation was reserved for nothing, while Activity (which always has
 * content) had nowhere to live. Issues is now reached from the exception rows
 * on Command, which is where somebody is when they care about it.
 *
 * Compensation left the salesperson's tab bar for the same reason and the
 * opposite cause: it is important but it is not a place you go five times a
 * day. It sits in More, one tap away.
 *
 * These still have to be DECLARED. Expo Router registers every file in a group
 * as a tab unless told otherwise, so an undeclared screen would reappear in the
 * bar with a default label — which is how you get a fifth tab called "issues"
 * that nobody chose.
 */
export const HIDDEN_ROUTES: Record<ExperienceKind, string[]> = {
  sales: ['compensation'],
  manager: [],
  advisor: [],
  executive: [],
  owner: ['issues'],
};
