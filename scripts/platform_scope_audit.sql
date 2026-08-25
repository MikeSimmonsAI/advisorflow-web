-- =====================================================================
-- platform_scope_audit.sql  —  READ ONLY. Changes nothing.
-- Run this FIRST, in Render → advisorflow-backend → Shell → psql $DATABASE_URL
-- Paste the output back before running platform_scope_apply.sql.
-- =====================================================================

\echo '--- 1. Platforms (seeded by main.py on every startup) ---'
SELECT id, name, slug, domain, is_active FROM platforms ORDER BY id;

\echo ''
\echo '--- 2. Organizations and their platform assignment ---'
\echo '    Any row with platform_id NULL is UNASSIGNED and will not be'
\echo '    visible to a platform-scoped super_admin once scoping is on.'
SELECT o.id,
       o.name,
       o.slug,
       o.domain,
       o.platform_id,
       p.slug AS platform_slug,
       (SELECT count(*) FROM users u WHERE u.organization_id = o.id) AS user_count
FROM organizations o
LEFT JOIN platforms p ON p.id = o.platform_id
ORDER BY p.slug NULLS FIRST, o.name;

\echo ''
\echo '--- 3. super_admin accounts and their platform assignment ---'
\echo '    Any row with platform_id NULL currently sees EVERY org on every'
\echo '    platform. That is the leak the migration closes.'
SELECT u.id,
       u.email,
       u.role,
       u.organization_id,
       u.platform_id           AS user_platform_id,
       o.platform_id           AS org_platform_id,
       p.slug                  AS org_platform_slug
FROM users u
LEFT JOIN organizations o ON o.id = u.organization_id
LEFT JOIN platforms p     ON p.id = o.platform_id
WHERE u.role IN ('super_admin', 'god_admin')
ORDER BY u.role, u.email;

\echo ''
\echo '--- 4. Summary counts ---'
SELECT
  (SELECT count(*) FROM organizations WHERE platform_id IS NULL) AS orgs_unassigned,
  (SELECT count(*) FROM organizations WHERE platform_id IS NOT NULL) AS orgs_assigned,
  (SELECT count(*) FROM users WHERE role = 'super_admin' AND platform_id IS NULL) AS super_admins_unassigned,
  (SELECT count(*) FROM users WHERE role = 'super_admin' AND platform_id IS NOT NULL) AS super_admins_assigned;
