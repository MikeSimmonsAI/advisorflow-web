# SCI manager role: permission matrix (synthetic, dependency-free)

Totals: 46 rows, PASS 42, FAIL 0, NEEDS-STAGING 4.

Evidence kinds: `code` = source/fixture executed here; `staging` = needs an authenticated staging session.

| Capability | Expected | Evidence | Result |
|---|---|---|---|
| Role vocabulary: 'manager' is a grantable workspace role | present | workspace_access.WORKSPACE_ROLES=('org_admin', 'advisor', 'viewer', 'super_admin', 'manager') | PASS |
| Role vocabulary: no god_admin / brand roles mintable by a workspace grant | absent | WORKSPACE_ROLES | PASS |
| Manager sees whole SCI workspace (leads/conversations/reports/program) | allowed | lead_scope.MANAGER_ROLES includes manager; Michael+X-Workspace-Id=SCI | PASS |
| Manager is NOT a workspace admin (users, org settings, credentials) | denied | require_org_admin; WORKSPACE_ADMIN_ROLES=('org_admin', 'super_admin') | PASS |
| Manager is not god_admin (no God Mode / platform-owner controls) | denied | effective_role != god_admin | PASS |
| Header naming an unrelated org is discarded (no membership) | denied | selected_workspace_id requires active membership | PASS |
| Header naming Restland workspace is discarded | denied | selected_workspace_id requires active membership | PASS |
| Unrelated org id never becomes the data scope | denied | active_workspace_org_id | PASS |
| SCI scope resolves only to the SCI org | SCI only | active_workspace_org_id | PASS |
| Without a header the manager gets no manager powers anywhere | denied | falls back to users.role=advisor | PASS |
| User with no SCI membership gets nothing in SCI | denied | no membership -> header ignored | PASS |
| Manager role does not satisfy super_admin/god guards | denied | deps.require_super_admin/require_god read users.role only | PASS |
| require_god reads users.role only (membership cannot grant it) | true | deps.require_god | PASS |
| require_super_admin reads users.role only | true | deps.require_super_admin | PASS |
| User administration (/admin users) requires org admin, not manager | denied | admin_router._USERS includes require_org_admin | PASS |
| Org settings / credentials / Twilio routes use require_org_admin (0 on bare require_admin) | 0 leaks | org_settings_router: 14 guarded routes, leaking=[] | PASS |
| Org Twilio / carrier configuration routes are org-admin only | denied for manager | routes=['update_org_twilio', 'update_org_twilio_phone', 'transfer_user_number_to_shared_org_number', 'assign_org_sending_number'] | PASS |
| Org email-sender (API key) route is org-admin only | denied for manager | update_email_sender | PASS |
| God/platform-owner routes (/god/*) all sit behind a god-class guard | 0 unguarded | AST scan of app/routers/god_*.py; unguarded=[] | PASS |
| /program routes: every tenant route has a tenant guard | 0 unguarded | 37 routes; unguarded=[] | PASS |
| /god/programs routes (program creation / provisioning) are require_god | all | 1 routes; non-god=[] | PASS |
| /program routes with no in-body manager gate are only the reviewed set | reviewed set | ungated=['router GET /me'] | PASS |
| Public program routes carry no tenant data guard but no org id param | informational | 1 public routes | PASS |
| API: GET /program/* reachable by a membership-only manager (organization_id NULL) | allowed (reads match the writes) | deps.require_tenant_or_observer honours X-Workspace-Id membership=True; require_tenant_user does | PASS |
| API: read guard and write guard accept the same callers | equal | 20 read routes vs 17 write routes | PASS |
| UI isWorkspaceManagerRole('manager') equals backend is_manager_here | backend=True | frontend/src/auth/workspaceRules.js executed under node: True | PASS |
| UI isWorkspaceManagerRole('org_admin') equals backend is_manager_here | backend=True | frontend/src/auth/workspaceRules.js executed under node: True | PASS |
| UI isWorkspaceManagerRole('super_admin') equals backend is_manager_here | backend=True | frontend/src/auth/workspaceRules.js executed under node: True | PASS |
| UI isWorkspaceManagerRole('god_admin') equals backend is_manager_here | backend=True | frontend/src/auth/workspaceRules.js executed under node: True | PASS |
| UI isWorkspaceManagerRole('advisor') equals backend is_manager_here | backend=False | frontend/src/auth/workspaceRules.js executed under node: False | PASS |
| UI isWorkspaceManagerRole('viewer') equals backend is_manager_here | backend=False | frontend/src/auth/workspaceRules.js executed under node: False | PASS |
| UI isManagerRole('manager') (admin) equals backend require_org_admin | backend=False | workspaceRules.js: False | PASS |
| UI isManagerRole('org_admin') (admin) equals backend require_org_admin | backend=True | workspaceRules.js: True | PASS |
| Nav '/users' hidden from manager (backend refuses via require_org_admin) | workspaceAdminOnly | { to: '/users', label: 'Users', icon: 'user-plus', adminOnly: true, workspaceAdminOnly: tr | PASS |
| Nav '/org-settings' hidden from manager (backend refuses via require_org_admin) | workspaceAdminOnly | { to: '/org-settings', label: 'Organization', icon: 'building', adminOnly: true, workspace | PASS |
| Nav '/tier-definitions' hidden from manager (backend refuses via require_org_admin) | workspaceAdminOnly | { to: '/tier-definitions', label: 'Tier Config', icon: 'layers', adminOnly: true, workspac | PASS |
| Nav '/reports' shown to manager (backend permits via require_admin) | adminOnly (manager passes) | { to: '/reports', label: 'Reports', icon: 'activity', adminOnly: true, featureKey: 'report | PASS |
| Nav '/campaigns' shown to manager (backend permits via require_admin) | adminOnly (manager passes) | { to: '/campaigns', label: 'Campaigns', icon: 'target', adminOnly: true, featureKey: 'camp | PASS |
| Nav '/imports' shown to manager (backend permits via require_admin) | adminOnly (manager passes) | { to: '/imports', label: 'Import Center', icon: 'upload', adminOnly: true, featureKey: 'im | PASS |
| Layout visibility applies workspaceAdminOnly with the admin predicate | present | Layout.jsx visible() | PASS |
| Family Service Center nav appears for Michael after login | visible | needs authenticated staging session (GET /program/status with X-Workspace-Id) | NEEDS-STAGING |
| Provisioning grants 'manager' on this workspace only, to an EXISTING login | true | program_setup.py --manager-email | PASS |
| Provisioning never touches users.role / production role assignments | true | program_setup.py | PASS |
| Michael's membership row exists in the SCI workspace | role=manager, active | SCI does not exist in production; grant runs at promotion (handoff 2026-10-06 §6) | NEEDS-STAGING |
| Michael denied by live API on /admin/users, /org-settings/*, /god/* | 403 | needs authenticated staging session | NEEDS-STAGING |
| DB-backed tenant-isolation test (second org rows invisible) | no rows | tests/ need fastapi+sqlalchemy; not installed here | NEEDS-STAGING |
