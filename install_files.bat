@echo off
REM ============================================================
REM  BookaBoost v2.6.0 Install Files
REM  Run from the folder where you extracted the zip.
REM  Then run deploy.bat to push to GitHub / Render.
REM ============================================================

SET REPO=C:\Users\simmo\OneDrive\Desktop\Web AvdvisorFlow Package\files\advisorflow-web

echo.
echo ===================================
echo   BookaBoost v2.6.0 Install
echo ===================================
echo.

echo Copying backend files...
copy /Y "app\models\models.py"                       "%REPO%\app\models\models.py"
copy /Y "app\services\voice_service.py"              "%REPO%\app\services\voice_service.py"
copy /Y "app\routers\voice_router.py"                "%REPO%\app\routers\voice_router.py"
copy /Y "app\services\ai_conversation_service.py"    "%REPO%\app\services\ai_conversation_service.py"
copy /Y "app\routers\ai_conversation_router.py"      "%REPO%\app\routers\ai_conversation_router.py"
copy /Y "app\routers\calendar_router.py"             "%REPO%\app\routers\calendar_router.py"
copy /Y "app\routers\leads_router.py"                "%REPO%\app\routers\leads_router.py"
copy /Y "app\routers\availability_router.py"         "%REPO%\app\routers\availability_router.py"
copy /Y "app\services\email_poller_service.py"       "%REPO%\app\services\email_poller_service.py"
copy /Y "app\jobs\run_ai_conversation_job.py"        "%REPO%\app\jobs\run_ai_conversation_job.py"
copy /Y "app\main.py"                                "%REPO%\app\main.py"
copy /Y "render.yaml"                                "%REPO%\render.yaml"
copy /Y "requirements.txt"                           "%REPO%\requirements.txt"

echo Copying frontend files...
copy /Y "frontend\src\App.jsx"                       "%REPO%\frontend\src\App.jsx"
copy /Y "frontend\src\components\Layout.jsx"         "%REPO%\frontend\src\components\Layout.jsx"
copy /Y "frontend\src\pages\LeadDetail.jsx"          "%REPO%\frontend\src\pages\LeadDetail.jsx"
copy /Y "frontend\src\pages\AIHub.jsx"               "%REPO%\frontend\src\pages\AIHub.jsx"
copy /Y "frontend\src\pages\AIHub.css"               "%REPO%\frontend\src\pages\AIHub.css"
copy /Y "frontend\package.json"                      "%REPO%\frontend\package.json"

echo.
echo ===================================
echo   Install complete!
echo ===================================
echo.
echo Next: run deploy.bat
echo.
pause
