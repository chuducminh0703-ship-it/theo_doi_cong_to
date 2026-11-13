@echo off
setlocal
cd /d "%~dp0"
where py >nul 2>nul
if %errorlevel%==0 (
  py -3 -m pip install --upgrade pyinstaller selenium
  py -3 -m PyInstaller --noconfirm --clean --onefile --windowed --name TheoDoiMatTinHieu monitor_signal_tool.py
) else (
  python -m pip install --upgrade pyinstaller selenium
  python -m PyInstaller --noconfirm --clean --onefile --windowed --name TheoDoiMatTinHieu monitor_signal_tool.py
)
echo.
echo Da tao ban dong goi: dist\TheoDoiMatTinHieu.exe
pause
endlocal
