@echo off
REM Local Sales Workspace demo. Development only - SQLite, never production.
cd /d "C:\Dev\advisorflow-web"
set DATABASE_URL=sqlite:///./demo_sales.db
set JWT_SECRET=demo000000000000000000000000000000000000000000000000000000000000
set SECRET_KEY=demo000000000000000000000000000000000000000000000000000000000000
python scripts\demo_local_scheduling.py
python -m uvicorn app.main:app --port 8010 --host 127.0.0.1
