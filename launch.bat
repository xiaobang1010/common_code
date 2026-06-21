@echo off
chcp 65001 >nul 2>&1
cd /d "%~dp0"
echo Building frontend...
cd frontend
call npm run build
cd ..
echo Starting...
cd electron
call npx electron .
