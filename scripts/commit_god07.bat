@echo off
cd /d C:\Dev\advisorflow-web
git add frontend/src/pages/GodCustomers.jsx
git diff --cached --stat
git commit -m "feat(GOD-07): seed GodCustomers filters from URL search params on mount" -m "Navigating to /god/customers?status=active&platform_id=... now pre-filters the list without a manual re-select. Same useSearchParams pattern as GodImplementations.jsx." -m "Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>" -m "Claude-Session: https://claude.ai/code/session_01De8hPPn7iSWhtv1QoAnWSA"
git push origin main
echo PUSH_OK
