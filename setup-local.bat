@echo off
echo ========================================
echo  AdvisorFlow — First-Time Setup
echo ========================================
echo.
echo Installing Python dependencies...
REM requirements-dev.txt starts with "-r requirements.txt", so this single
REM command installs the full production set PLUS the local-only tools
REM (pytest, alembic). Render installs requirements.txt only.
pip install -r requirements-dev.txt
echo.
echo ✅ Done! Now run start-backend.bat and start-frontend.bat
echo.
pause
