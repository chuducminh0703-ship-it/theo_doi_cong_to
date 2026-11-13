@echo off
setlocal
cd /d "%~dp0"
where py >nul 2>nul
if %errorlevel%==0 (
  py -3 -m pip install -r requirements.txt
  py -3 monitor_signal_tool.py
) else (
  python -m pip install -r requirements.txt
  python monitor_signal_tool.py
)
endlocal
