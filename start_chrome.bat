@echo off
setlocal
cd /d "%~dp0"

python -c "import websocket" >nul 2>nul
if errorlevel 1 (
  echo Installing GOAT Gauge dependencies...
  python -m pip install -r "%~dp0requirements.txt"
  if errorlevel 1 (
    echo Dependency installation failed.
    pause
    exit /b 1
  )
)

where pythonw.exe >nul 2>nul
if %errorlevel%==0 (
  start "" pythonw.exe "%~dp0entry.py" --chrome
  exit /b 0
)

where pyw.exe >nul 2>nul
if %errorlevel%==0 (
  start "" pyw.exe -3 "%~dp0entry.py" --chrome
  exit /b 0
)

echo Python 3 was not found on PATH.
pause
exit /b 1
