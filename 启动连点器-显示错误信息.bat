@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo ============================================
echo   连点器 调试启动（窗口里会显示报错信息）
echo ============================================
echo.
if exist "runtime\python.exe" (
  "runtime\python.exe" "main.py"
) else (
  python "main.py"
)
echo.
echo ---- 程序已退出（上面如果有红字就是出错原因）----
pause
