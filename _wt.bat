@echo off
REM ============================================================
REM _wt.bat - add a worktree for a new thread.
REM
REM Usage:  _wt.bat <path> <branch> [<start-point>]
REM   _wt.bat C:\Dev\advisorflow-godpricing feat/god-pricing-console 27f09d7
REM
REM WHY THE index.lock DELETE IS GONE
REM
REM This used to open with:
REM     if exist ".git\index.lock" del ".git\index.lock"
REM
REM index.lock exists because a git process is holding the index, or because
REM one died holding it. Deleting it blind cannot tell those apart, so it will
REM happily yank the lock out from under a `git add` or a checkout that is
REM running right now in another worktree or another session - which is how an
REM index gets corrupted and staged work disappears. With several threads live
REM at once that is not hypothetical.
REM
REM It now reports the lock and stops. Check that nothing owns it
REM (Get-Process git) before removing it by hand.
REM
REM The path, branch and start point are arguments now instead of being baked
REM in, so this cannot silently recreate one particular old thread's worktree.
REM ============================================================

setlocal

if "%~1"=="" goto :usage
if "%~2"=="" goto :usage

git rev-parse --is-inside-work-tree >nul 2>&1
if errorlevel 1 (
    echo ERROR: not inside a git repository.
    exit /b 1
)

for /f "delims=" %%R in ('git rev-parse --git-common-dir') do set "GITDIR=%%R"
if exist "%GITDIR%\index.lock" (
    echo ERROR: %GITDIR%\index.lock exists.
    echo.
    echo A git process is holding the index, or one died holding it. This
    echo script will not delete it - doing that while another worktree is
    echo mid-write corrupts the index and loses staged work.
    echo.
    echo Check first:   Get-Process git
    echo If nothing owns it, remove it by hand and run this again.
    exit /b 1
)

git worktree add "%~1" -b "%~2" %~3
if errorlevel 1 (
    echo WT_EXIT=1
    exit /b 1
)

echo WT_EXIT=0
git -C "%~1" -c core.pager=cat log --oneline -1
git -C "%~1" status --porcelain
exit /b 0

:usage
echo Usage: _wt.bat ^<path^> ^<branch^> [^<start-point^>]
echo   _wt.bat C:\Dev\advisorflow-feature feat/my-thread origin/main
exit /b 1
