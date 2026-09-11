@echo off
setlocal
cd /d "%~dp0"
python "%~dp0entry.py" --serve --port 18927
if errorlevel 1 pause
