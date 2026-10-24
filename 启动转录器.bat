@echo off
cd /d "%~dp0"
where pythonw.exe >nul 2>nul
if errorlevel 1 (
  echo Python not found. Install Python 3.10 and add it to PATH,
  echo or run: python main.py
  pause
  exit /b 1
)
start "" pythonw.exe "%~dp0main.py"
