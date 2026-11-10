@echo off
chcp 65001 >nul
cd /d "%~dp0"
if exist "runtime\pythonw.exe" (
  start "" "runtime\pythonw.exe" "main.py"
  exit /b 0
)
where python >nul 2>nul
if %errorlevel%==0 (
  start "" pythonw "main.py"
  exit /b 0
)
echo 找不到运行的 Python 环境，请确认「runtime」文件夹还在。
pause
