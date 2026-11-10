@echo off
chcp 65001 >nul
cd /d "%~dp0"
python cloud_setup.py %*
echo.
pause
