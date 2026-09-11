@echo off
REM ============================================================
REM git_push.bat - stage YOUR changes, commit, push.
REM
REM Usage:  git_push.bat "what changed"
REM
REM WHAT THIS USED TO DO, AND WHY IT WAS DANGEROUS
REM
REM   cd /d "C:\Dev\advisorflow-web"
REM   git add -A
REM   git commit -m "<a message from a session in 2025>"
REM   git push
REM
REM Three separate hazards in four lines:
REM
REM   * `git add -A` swept the ENTIRE working tree - including other threads'
REM     work-in-progress, probe databases, scratch output, and any file that
REM     happened to hold a credential.
REM   * The commit message was hardcoded, so every commit it ever made claimed
REM     to be about CRM integration regardless of what was in it.
REM   * The hardcoded `cd` meant running it from any worktree silently
REM     committed in a DIFFERENT one. With several worktrees live at once that
REM     is not a convenience, it is a way to push somebody else's half-finished
REM     branch.
REM
REM It now works on the repository you are standing in, stages tracked
REM modifications only, and requires you to say what changed.
REM ============================================================

setlocal

git rev-parse --is-inside-work-tree >nul 2>&1
if errorlevel 1 (
    echo ERROR: not inside a git repository. cd to the worktree you mean.
    pause
    exit /b 1
)

if "%~1"=="" (
    echo Usage: git_push.bat "what changed"
    echo.
    echo A commit needs a description written by whoever made the change.
    pause
    exit /b 1
)

echo Repository: 
git rev-parse --show-toplevel
echo Branch:
git rev-parse --abbrev-ref HEAD
echo.
echo Tracked files you have changed:
echo -----------------------------------
git diff --name-status
echo -----------------------------------
echo.

git ls-files --others --exclude-standard > "%TEMP%\af_gp_untracked.txt"
for %%A in ("%TEMP%\af_gp_untracked.txt") do set UNTRACKEDSIZE=%%~zA
if not "%UNTRACKEDSIZE%"=="0" (
    echo NOT tracked, and therefore NOT included:
    type "%TEMP%\af_gp_untracked.txt"
    echo.
    echo Stage anything that belongs here with:  git add ^<path^>
    echo.
)
del "%TEMP%\af_gp_untracked.txt" >nul 2>&1

git add -u
git commit -m "%~1"
if errorlevel 1 (
    echo Nothing was committed.
    pause
    exit /b 0
)

git push
if errorlevel 1 (
    echo PUSH FAILED - your commit is safe locally.
    pause
    exit /b 1
)

echo Done.
pause
endlocal
