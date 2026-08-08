@echo off
cd /d "C:\Users\simmo\OneDrive\Desktop\Web AvdvisorFlow Package\files\advisorflow-web"
git add -A
git commit -m "Security sweep + feature batch: remove hardcoded Restland from voice_router, nav breakpoint fix, email queue status labels, AI industry context per org"
git push
echo GIT_EXIT_CODE=%ERRORLEVEL%
