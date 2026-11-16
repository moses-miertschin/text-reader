@echo off
title Text Reader setup
cd /d "%~dp0"
where py >nul 2>nul
if %errorlevel%==0 (
    py -3 "%~dp0setup.py"
) else (
    python "%~dp0setup.py"
)
echo.
pause
