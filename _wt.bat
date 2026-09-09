@echo off
cd /d C:\Dev\advisorflow-web
if exist ".git\index.lock" del ".git\index.lock"
git worktree add C:\Dev\advisorflow-godpricing -b feat/god-pricing-console 27f09d7
echo WT_EXIT=%ERRORLEVEL%
cd /d C:\Dev\advisorflow-godpricing
git -c core.pager=cat log --oneline -1
git status --porcelain
