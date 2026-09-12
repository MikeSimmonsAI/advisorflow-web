@echo off
REM The actual regression command, on disk rather than inline.
REM
REM Inline was one bug: `cmd /c` mangles a command line that both starts with a
REM quote and carries redirection. Buffered stdout was the other: python only
REM flushes a redirected stream every 8KB, so a run in progress looks stalled
REM and a run that dies loses everything since the last flush. `-u` fixes that.
REM
REM --junitxml is the durable evidence. Stdout can be lost to a dropped shell,
REM a detached console or a buffer; a results file pytest writes itself cannot.
cd /d "%~dp0.."
set OUT=%~1
if "%OUT%"=="" set OUT=C:\Dev\_m2_full.txt
set XML=%~2
if "%XML%"=="" set XML=C:\Dev\_m2_junit.xml
C:\Dev\advisorflow-web\.venv\Scripts\python.exe -u -m pytest tests -q --no-header -p no:cacheprovider --tb=line -W ignore::DeprecationWarning --junitxml="%XML%" > "%OUT%" 2>&1
echo REGRESSION_EXIT=%ERRORLEVEL% >> "%OUT%"
