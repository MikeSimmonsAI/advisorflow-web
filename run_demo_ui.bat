@echo off
REM Local Sales Workspace demo UI. Points at the local demo backend on :8010.
REM Port 5173 and host "localhost" matter: they are in the backend's
REM ALLOWED_ORIGINS list, and any other origin is refused by CORS.
cd /d "C:\Users\simmo\OneDrive\Desktop\Web AvdvisorFlow Package\files\advisorflow-web\frontend"
set VITE_API_BASE_URL=http://localhost:8010
npx.cmd vite --port 5173 --strictPort --host localhost
