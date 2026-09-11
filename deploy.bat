@echo off
REM ============================================================
REM AdvisorFlow Deploy  v4.0
REM
REM THIS SCRIPT DOES NOT DECIDE WHAT GOES INTO A COMMIT.
REM
REM You stage. You commit. Then you run this. It checks, pushes, and lets
REM Render deploy. It never runs `git add` in any form.
REM
REM WHY v4.0 STOPPED STAGING AT ALL
REM
REM v3.0 had already dropped `git add .` for `git add -u`, which stages every
REM modified TRACKED file. That is narrower, but it is the same mistake: the
REM deploy script was still the thing choosing the contents of a commit, and
REM it chose by wildcard. With several worktrees and threads live at once,
REM "everything I happen to have edited" is not a change set - it is whatever
REM state the tree was in when somebody typed deploy. A half-finished edit in
REM another file ships alongside the fix, under the fix's message, and the
REM commit history stops describing the work.
REM
REM So the rule is now absolute: no `git add .`, no `-A`, no `-u`, no
REM equivalent. A human or an agent selects the files, stages them by path,
REM writes a message, and commits. This script refuses to run until that has
REM happened.
REM
REM THE RENDER API KEY IS NOT IN THIS FILE and never will be. It comes from
REM RENDER_API_KEY in the environment. The key that used to live here is in
REM git history; removing it from HEAD did NOT revoke it. See
REM docs/DEPLOYMENT.md.
REM
REM THE API CALL IS A NUDGE, NOT THE MECHANISM. Both Render services
REM auto-deploy on a push to main. With no key set this script still deploys.
REM ============================================================

setlocal enabledelayedexpansion

echo.
echo ===================================
echo   AdvisorFlow Deploy v4.0
echo ===================================
echo.

git rev-parse --is-inside-work-tree >nul 2>&1
if errorlevel 1 (
    echo ERROR: This folder is not a git repository.
    echo Run deploy.bat from inside the worktree you mean to deploy.
    echo.
    pause
    exit /b 1
)

echo Repository:
git rev-parse --show-toplevel
echo Branch:
git rev-parse --abbrev-ref HEAD
echo.

REM -- Refuse to ship a credential -----------------------------------------
REM Runs first, so the answer to "did we just push a key" is never
REM "yes, and it is already on GitHub".
where python >nul 2>&1
if not errorlevel 1 (
    python scripts\_secret_audit.py
    if errorlevel 1 (
        echo.
        echo REFUSING TO DEPLOY: a live-shaped credential is in a tracked file.
        echo Remove it, move it to an environment variable, and ROTATE it -
        echo taking it out of HEAD does not revoke it.
        echo.
        pause
        exit /b 1
    )
) else (
    echo WARNING: python not found, so the credential audit did not run.
    echo.
)

REM -- The working tree must be clean ---------------------------------------
REM
REM NAMES ONLY. Nothing here stages, commits, resets, checks out or stashes
REM anything. A deploy script that "helpfully" tidies a dirty tree is a deploy
REM script that can destroy work, and one that silently includes a dirty tree
REM is how another thread's half-finished file reaches production.
git diff --quiet
set DIRTY=%errorlevel%
git diff --cached --quiet
set STAGED=%errorlevel%
git ls-files --others --exclude-standard > "%TEMP%\af_untracked.txt"
for %%A in ("%TEMP%\af_untracked.txt") do set UNTRACKEDSIZE=%%~zA

if not "%DIRTY%"=="0"  goto :notready
if not "%STAGED%"=="0" goto :notready
if not "%UNTRACKEDSIZE%"=="0" goto :notready
goto :ready

:notready
echo -----------------------------------
echo The working tree is not clean:
git status --short
echo -----------------------------------
echo.
echo This script deploys a commit you have already made. It will not stage,
echo commit or discard any of the above.
echo.
echo   Belongs in this deploy:   git add ^<path^> [^<path^> ...]
echo                             git commit -m "what changed"
echo   Scratch:                  add it to .gitignore
echo   Not ready:                leave it; deploy after it is committed
echo.
echo Then run deploy.bat again.
echo.
del "%TEMP%\af_untracked.txt" >nul 2>&1
pause
exit /b 1

:ready
del "%TEMP%\af_untracked.txt" >nul 2>&1

REM -- There has to be something to push ------------------------------------
for /f %%B in ('git rev-parse --abbrev-ref HEAD') do set BRANCH=%%B
git rev-parse --verify --quiet "origin/%BRANCH%" >nul 2>&1
if errorlevel 1 (
    echo Branch %BRANCH% has no upstream on origin yet. It will be created.
) else (
    for /f %%C in ('git rev-list --count "origin/%BRANCH%..HEAD"') do set AHEAD=%%C
    if "!AHEAD!"=="0" (
        echo Nothing to push: %BRANCH% is not ahead of origin/%BRANCH%.
        echo.
        echo If you want Render to rebuild the code that is already on main,
        echo that is deploy_force.bat.
        echo.
        pause
        exit /b 0
    )
    echo Commits to push: !AHEAD!
)

echo.
echo This is what will deploy:
echo -----------------------------------
git log --oneline -5
echo -----------------------------------
echo.

echo Pushing to GitHub...
git push
if errorlevel 1 (
    echo.
    echo ERROR: git push failed. Your commits are safe locally - fix the
    echo connection or credentials, reconcile with origin, and push again.
    echo Do NOT force-push to get around this.
    pause
    exit /b 1
)

echo.
echo ===================================
echo   Pushed. Render auto-deploys both
echo   services from main.
echo ===================================
echo.

REM -- Optional nudge -------------------------------------------------------
if "%RENDER_API_KEY%"=="" (
    echo RENDER_API_KEY is not set, so no deploy was triggered by hand.
    echo That is fine: the push above is what deploys. To set it once:
    echo     setx RENDER_API_KEY "your-key-here"
    echo Then open a new terminal. See docs/DEPLOYMENT.md.
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
