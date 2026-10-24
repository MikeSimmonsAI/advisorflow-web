/** SCI cockpit appears only when the SERVER grants a program workspace.
 * Cross-tenant isolation is not based on organization name or user email.
 */
import { resolveDefaultExperience, resolveExperiences, TABS } from '../experience/resolve';
import type { MyContexts, AuthorizedContext } from '../api/types';

function contexts(workspaces: AuthorizedContext[]): MyContexts {
  return { contexts: workspaces, platform_contexts: [], executive_contexts: [],
    workspace_contexts: workspaces, has_back_office: false, workspace_count: workspaces.length,
    default_context: workspaces[0] ?? { type: 'legacy_tenant', path: '/' } };
}
describe('outreach-program experience', () => {
  it('gives a program-only SCI seat just the program cockpit', () => {
    const authorized: AuthorizedContext = { type: 'workspace', organization_id: 'sci-1',
      organization_name: 'Service Corporation International', is_program: true,
      role: 'org_admin', path: '/workspace/sci-1' };
    const all = resolveExperiences(contexts([authorized]));
    expect(all).toHaveLength(1);
    expect(all[0]).toMatchObject({ kind: 'program', key: 'program:sci-1',
      organizationId: 'sci-1', route: '/(program)/home' });
    expect(resolveDefaultExperience(contexts([authorized]), all)?.kind).toBe('program');
    expect(TABS.program.map(t => t.name)).toEqual(['home', 'inbox', 'visits', 'more']);
  });
  it('does not mistake another customer for a program, even with an SCI-like name', () => {
    const all = resolveExperiences(contexts([{ type: 'workspace', organization_id: 'regular-1',
      organization_name: 'SCI Example', role: 'org_admin',
      path: '/workspace/regular-1' }]));
    expect(all.map(e => e.kind)).toEqual(['advisor']);
  });
  it('does not show Wholesale, Atlantis or other workspaces without membership', () => {
    const all = resolveExperiences(contexts([{ type: 'workspace', organization_id: 'sci-1',
      organization_name: 'SCI', role: 'org_admin', is_program: true,
      path: '/workspace/sci-1' }]));
    expect(all).toHaveLength(1);
    expect(all[0].organizationId).toBe('sci-1');
  });
  it('keeps separate programs in separate workspace identities', () => {
    const all = resolveExperiences(contexts([
      { type: 'workspace', organization_id: 'sci-1', organization_name: 'SCI',
        is_program: true, path: '/workspace/sci-1' },
      { type: 'workspace', organization_id: 'program-2', organization_name: 'Other program',
        is_program: true, path: '/workspace/program-2' },
    ]));
    expect(all.map(e => e.key)).toEqual(['program:sci-1','program:program-2']);
    expect(new Set(all.map(e => e.organizationId)).size).toBe(2);
  });
  it('never creates a program from back-office sales alone', () => {
    const c = contexts([]);
    c.platform_contexts = [{ type: 'platform', role: 'sales_rep', path: '/sales',
      brand_sales_org_id: 'brand-1' }];
    expect(resolveExperiences(c).map(e => e.kind)).toEqual(['sales']);
  });
});
