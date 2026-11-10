@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo Starting Show Radar... Open http://127.0.0.1:8765 in your browser.
python "app\server.py"
pause
