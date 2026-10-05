@echo off
rem Dry run: lists the SCI location addresses it would add to support@evosyspro.live. Changes nothing.
rem To make the change, run:  m365_location_aliases.bat apply
if /I "%1"=="apply" (
  powershell -ExecutionPolicy Bypass -File "%~dp0m365_location_aliases.ps1" -Apply
) else (
  powershell -ExecutionPolicy Bypass -File "%~dp0m365_location_aliases.ps1"
)
pause
