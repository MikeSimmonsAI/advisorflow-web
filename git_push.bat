@echo off
REM ============================================================
REM git_push.bat - stage THE FILES YOU NAME, commit, push.
REM
REM Usage:  git_push.bat "what changed" <path> [<path> ...]
REM
REM Example:
REM   git_push.bat "fix SLA pause arithmetic" app\services\support_sla.py ^
REM                tests\test_support_engine.py
REM
REM This is the one helper here that is allowed to create a commit, and it may
REM stage ONLY the paths you pass it. There is no flag to stage everything,
REM because that flag is the whole bug:
REM
REM   * `git add -A` swept the ENTIRE working tree - other threads'
REM     work-in-progress, probe databases, scratch output, and any file that
REM     happened to hold a credential.
REM   * `git add -u` looked safer and was the same mistake one size smaller:
REM     the script still picked the change set, and it picked by wildcard.
REM   * The commit message used to be hardcoded, so every commit it ever made
REM     claimed to be about CRM integration regardless of what was in it.
REM   * A hardcoded `cd` meant running it from any worktree committed in a
REM     DIFFERENT one - a way to push somebody else's half-finished branch.
REM
REM It now works on the repository you are standing in, stages exactly what
REM you name, and requires you to say what changed.
REM
REM To ship to production, commit first and then run deploy.bat.
REM ============================================================

setlocal enabledelayedexpansion

git rev-parse --is-inside-work-tree >nul 2>&1
if errorlevel 1 (
    echo ERROR: not inside a git repository. cd to the worktree you mean.
    pause
    exit /b 1
)

if "%~1"=="" goto :usage
if "%~2"=="" goto :usage

set "MSG=%~1"
shift

echo Repository:
git rev-parse --show-toplevel
echo Branch:
git rev-parse --abbrev-ref HEAD
echo.
echo Staging only these paths:

:stageloop
if "%~1"=="" goto :staged
if not exist "%~1" (
    REM A deleted file is a legitimate thing to stage, so a missing path is
    REM only a problem when git does not know about it either.
    git ls-files --error-unmatch -- "%~1" >nul 2>&1
    if errorlevel 1 (
        echo.
        echo ERROR: "%~1" does not exist and git does not track it.
        echo Nothing has been staged or committed. Check the path.
        pause
        exit /b 1
    )
)
echo     + %~1
git add -- "%~1"
if errorlevel 1 (
    echo ERROR: could not stage "%~1". Nothing was committed.
    pause
    exit /b 1
)
shift
goto :stageloop

:staged
echo.
echo Staged:
echo -----------------------------------
git diff --cached --name-status
echo -----------------------------------
echo.

REM Everything you did NOT name is listed, and left exactly as it is.
git status --short --untracked-files=all > "%TEMP%\af_gp_rest.txt"
echo Left alone in this worktree ^(not part of this commit^):
type "%TEMP%\af_gp_rest.txt"
del "%TEMP%\af_gp_rest.txt" >nul 2>&1
echo.

git diff --cached --quiet
if not errorlevel 1 (
    echo Those paths hold no changes - nothing to commit.
    pause
    exit /b 0
)

git commit -m "%MSG%"
if errorlevel 1 (
    echo Nothing was committed.
    pause
    exit /b 1
)

git push
if errorlevel 1 (
    echo PUSH FAILED - your commit is safe locally. Reconcile with origin and
    echo push again. Do NOT force-push to get around this.
    pause
    exit /b 1
)

echo Done.
pause
exit /b 0

:usage
echo Usage: git_push.bat "what changed" ^<path^> [^<path^> ...]
echo.
echo Both a description and at least one path are required. This script does
echo not stage the whole tree - name the files that belong in the commit.
pause
exit /b 1
