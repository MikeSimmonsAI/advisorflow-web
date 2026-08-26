@echo off
cd /d "%~dp0.."
set PYTHONIOENCODING=utf-8
(
python -X utf8 scripts\smoke_test.py
python -X utf8 scripts\smoke_requests.py
python -X utf8 scripts\smoke_tenancy.py
python -X utf8 scripts\smoke_sales_workspace.py
python -X utf8 scripts\smoke_scheduling.py
python -X utf8 scripts\smoke_manager_workspace.py
python -X utf8 scripts\smoke_retell_bridge.py
python -X utf8 scripts\smoke_tenant_bridge.py
) 1> "%TEMP%\reg.txt" 2>&1
echo DONE
