@echo off
chcp 65001 >nul
cd /d "%~dp0"
python "daily_push.py" %*
echo.
echo Done. See reports\ and logs\push.log
pause
