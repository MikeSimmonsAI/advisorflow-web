@echo off
REM clean_wupa_spam.bat
REM Launcher for clean_wupa_spam.py - run this instead of typing python
REM commands directly in CMD, per Mike's stated preference (CMD breaks
REM multi-line Python syntax, so .py + .bat pairing avoids that entirely).
REM
REM Usage: double-click this file, or run from CMD:
REM   clean_wupa_spam.bat dry      (preview only)
REM   clean_wupa_spam.bat execute  (actually delete)

set MODE=%1
if "%MODE%"=="" set MODE=dry

if "%MODE%"=="execute" (
    python clean_wupa_spam.py --execute
) else (
    python clean_wupa_spam.py --dry-run
)

pause
