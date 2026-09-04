@echo off
cd /d "C:\Dev\advisorflow-web"
git add -A
git commit -m "CRM integration; domain detection login; reports_router registered; evosyspro CORS; crm_connections table; CRM push/pull/two-way with GHL and HubSpot direct API; nav link icon"
git push
echo Done.
pause
