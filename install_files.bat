@echo off
REM ============================================================
REM  BookaBoost — install_files.bat
REM  Double-click this file to copy all build outputs to their
REM  correct locations before running deploy.bat.
REM
REM  Main repo:   C:\Users\simmo\OneDrive\Desktop\Web AvdvisorFlow Package\files\advisorflow-web
REM  Vercel app:  C:\Users\simmo\OneDrive\Desktop\advisorflow_complete
REM ============================================================

SET REPO=C:\Users\simmo\OneDrive\Desktop\Web AvdvisorFlow Package\files\advisorflow-web
SET VERCEL=C:\Users\simmo\OneDrive\Desktop\advisorflow_complete

echo.
echo ============================================================
echo  BookaBoost File Installer
echo ============================================================
echo.

REM -- Verify repo folder exists --
IF NOT EXIST "%REPO%" (
    echo ERROR: Repo folder not found:
    echo   %REPO%
    echo Check the path and try again.
    pause
    exit /b 1
)

REM -- Verify Vercel folder exists --
IF NOT EXIST "%VERCEL%" (
    echo ERROR: Vercel app folder not found:
    echo   %VERCEL%
    echo Check the path and try again.
    pause
    exit /b 1
)

echo Copying main repo files...
echo.

REM ── Frontend ──────────────────────────────────────────────────
echo   [1/6] LeadDetail.jsx  →  frontend/src/pages/
copy /Y "LeadDetail.jsx" "%REPO%\frontend\src\pages\LeadDetail.jsx"
IF ERRORLEVEL 1 ( echo   FAILED: LeadDetail.jsx && pause && exit /b 1 )

REM ── Backend routers ───────────────────────────────────────────
echo   [2/6] leads_router.py  →  app/routers/
copy /Y "leads_router.py" "%REPO%\app\routers\leads_router.py"
IF ERRORLEVEL 1 ( echo   FAILED: leads_router.py && pause && exit /b 1 )

echo   [3/6] calendar_router.py  →  app/routers/
copy /Y "calendar_router.py" "%REPO%\app\routers\calendar_router.py"
IF ERRORLEVEL 1 ( echo   FAILED: calendar_router.py && pause && exit /b 1 )

echo   [4/6] email_router.py  →  app/routers/
copy /Y "email_router.py" "%REPO%\app\routers\email_router.py"
IF ERRORLEVEL 1 ( echo   FAILED: email_router.py && pause && exit /b 1 )

REM ── Backend services ──────────────────────────────────────────
echo   [5/6] email_poller_service.py  →  app/services/
copy /Y "email_poller_service.py" "%REPO%\app\services\email_poller_service.py"
IF ERRORLEVEL 1 ( echo   FAILED: email_poller_service.py && pause && exit /b 1 )

REM ── Backend jobs ──────────────────────────────────────────────
echo   [6/6] run_email_poller.py  →  app/jobs/
copy /Y "run_email_poller.py" "%REPO%\app\jobs\run_email_poller.py"
IF ERRORLEVEL 1 ( echo   FAILED: run_email_poller.py && pause && exit /b 1 )

REM ── Render config ─────────────────────────────────────────────
echo   [+]   render.yaml  →  repo root
copy /Y "render.yaml" "%REPO%\render.yaml"
IF ERRORLEVEL 1 ( echo   FAILED: render.yaml && pause && exit /b 1 )

echo.
echo Copying Vercel booking app files...
echo.

REM ── Vercel: opt-in page ───────────────────────────────────────
echo   [V1] optin.html  →  advisorflow_complete/
copy /Y "optin.html" "%VERCEL%\optin.html"
IF ERRORLEVEL 1 ( echo   FAILED: optin.html && pause && exit /b 1 )

REM ── Vercel: serverless API ────────────────────────────────────
echo   [V2] index.py  →  advisorflow_complete/api/
copy /Y "index.py" "%VERCEL%\api\index.py"
IF ERRORLEVEL 1 ( echo   FAILED: index.py && pause && exit /b 1 )

echo.
echo ============================================================
echo  All files installed successfully.
echo ============================================================
echo.
echo  Next steps:
echo    1. Run deploy.bat  (pushes repo to GitHub + triggers Render)
echo    2. cd to advisorflow_complete and run: vercel deploy --prod
echo    3. In Render, add env vars to advisorflow-email-poller cron:
echo       DATABASE_URL, JWT_SECRET, ENCRYPTION_KEY,
echo       MICROSOFT_CLIENT_ID, MICROSOFT_CLIENT_SECRET
echo    4. Submit Twilio A2P campaign with opt-in URL:
echo       https://advisorflow-booking.vercel.app/optin
echo.
pause
