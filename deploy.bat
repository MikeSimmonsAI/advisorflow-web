@echo off
REM ============================================================
REM AdvisorFlow Deploy  v3.0
REM
REM WHAT CHANGED IN v3.0, AND WHY
REM
REM 1. THE RENDER API KEY IS GONE FROM THIS FILE. It was written here in
REM    plaintext and committed, so anyone who could read the repository could
REM    read the key. It is now taken from RENDER_API_KEY in the environment.
REM    Removing it from HEAD does NOT revoke it - the old key is still in git
REM    history and MUST be rotated in the Render dashboard. See
REM    docs/DEPLOYMENT.md.
REM
REM 2. `git add .` IS GONE. It staged every change in the working tree,
REM    including other people's work-in-progress, throwaway probe databases,
REM    scratch output and any file that happened to contain a credential. A
REM    deploy script must ship what somebody decided to ship. This version
REM    stages tracked modifications only and REFUSES to continue when there
REM    are untracked files, naming them, so a new file reaches production
REM    because somebody added it rather than because a wildcard swept it up.
REM
REM 3. THE DEPLOY TRIGGER IS NOW OPTIONAL, because it always was. Both Render
REM    services auto-deploy on a push to main; the API call is a nudge, not
REM    the mechanism. With no key set, this script still deploys - it just
REM    says so instead of failing.
REM ============================================================

setlocal enabledelayedexpansion

echo.
echo ===================================
echo   AdvisorFlow Deploy v3.0
echo ===================================
echo.

git rev-parse --is-inside-work-tree >nul 2>&1
if errorlevel 1 (
    echo ERROR: This folder is not a git repository.
    echo Run deploy.bat from inside your advisorflow-web folder.
    echo.
    pause
    exit /b 1
)

REM ── Refuse to ship a credential ──────────────────────────────────────────
REM Runs BEFORE anything is staged, so the answer to "did we just commit a
REM key" is never "yes, and it is already on GitHub".
where python >nul 2>&1
if not errorlevel 1 (
    python scripts\_secret_audit.py
    if errorlevel 1 (
        echo.
        echo REFUSING TO DEPLOY: a live-shaped credential is in a tracked file.
        echo Remove it, move it to an environment variable, and rotate it.
        echo.
        pause
        exit /b 1
    )
)

REM ── What is actually going to be committed ───────────────────────────────
echo Tracked files you have changed:
echo -----------------------------------
git diff --name-status
echo -----------------------------------
echo.

REM Untracked files stop the deploy rather than riding along with it.
git ls-files --others --exclude-standard > "%TEMP%\af_untracked.txt"
for %%A in ("%TEMP%\af_untracked.txt") do set UNTRACKEDSIZE=%%~zA
if not "%UNTRACKEDSIZE%"=="0" (
    echo These files are NOT tracked by git and will NOT be deployed:
    echo -----------------------------------
    type "%TEMP%\af_untracked.txt"
    echo -----------------------------------
    echo.
    echo If any of them belong in this deploy, stage them deliberately:
    echo     git add ^<path^>
    echo If they are scratch files, add them to .gitignore.
    echo Then run deploy.bat again.
    echo.
    del "%TEMP%\af_untracked.txt"
    pause
    exit /b 1
)
del "%TEMP%\af_untracked.txt" >nul 2>&1

git diff --quiet
if not errorlevel 1 (
    git diff --cached --quiet
    if not errorlevel 1 (
        echo No changes to deploy.
        echo.
        pause
        exit /b 0
    )
)

set /p COMMITMSG="Describe what changed: "
if "%COMMITMSG%"=="" (
    echo A deploy with no description is a deploy nobody can explain later.
    echo.
    pause
    exit /b 1
)

echo.
echo Staging tracked changes only...
REM -u stages modifications and deletions of files git already knows about.
REM It cannot pick up an untracked file, which is the whole point.
git add -u
if errorlevel 1 (
    echo ERROR: staging failed.
    pause
    exit /b 1
)

echo Committing...
git commit -m "%COMMITMSG%"
if errorlevel 1 (
    echo.
    echo Nothing was committed.
    pause
    exit /b 0
)

echo.
echo Pushing to GitHub...
git push
if errorlevel 1 (
    echo.
    echo ERROR: git push failed. Your commit is safe locally - fix the
    echo connection or credentials and push again.
    pause
    exit /b 1
)

echo.
echo ===================================
echo   Pushed. Render auto-deploys both
echo   services from main.
echo ===================================
echo.

REM ── Optional nudge ───────────────────────────────────────────────────────
if "%RENDER_API_KEY%"=="" (
    echo RENDER_API_KEY is not set, so no deploy was triggered by hand.
    echo That is fine: the push above is what deploys. To set it once:
    echo     setx RENDER_API_KEY "your-key-here"
    echo See docs/DEPLOYMENT.md.
    echo.
    pause
    exit /b 0
)

echo Nudging the backend deploy...
curl -s -X POST ^
  "https://api.render.com/v1/services/srv-d8rsm2kvikkc738v8470/deploys" ^
  -H "Authorization: Bearer %RENDER_API_KEY%" ^
  -H "Content-Type: application/json" ^
  -d "{\"clearCache\": false}" > "%TEMP%\render_response.txt"

findstr /i "\"id\"" "%TEMP%\render_response.txt" >nul
if errorlevel 1 (
    echo   The manual trigger did not succeed - most likely RENDER_API_KEY is
    echo   expired or revoked. The push already deployed; check the Render
    echo   dashboard if you want to confirm.
) else (
    echo   Backend deploy triggered.
)
del "%TEMP%\render_response.txt" >nul 2>&1

echo.
echo ===================================
echo   Done. dashboard.render.com
echo ===================================
echo.
pause
endlocal
