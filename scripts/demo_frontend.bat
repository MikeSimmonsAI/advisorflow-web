@echo off
REM Vite dev server pointed at the LOCAL demo backend on 8098.
REM
REM Port 5174 on purpose: it is already in app/main.py's CORS allowlist, and an
REM ordinary dev session sits on 5173. Adding a new port to that allowlist to
REM suit a local script would widen a production setting for no reason.
cd /d "%~dp0..\frontend"
set "VITE_API_BASE_URL=http://localhost:8098"
npx vite --port 5174 --strictPort
