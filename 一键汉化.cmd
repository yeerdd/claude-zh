@echo off
chcp 65001 >nul
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1

echo.
echo   ========================================
echo       Claude Desktop 简体中文一键汉化
echo   ========================================
echo.
echo   脚本将自动关闭 Claude、修补核心语言支持、
echo   铺设中文语言包并清除 Chromium 旧缓存。
echo.

python install-zh.py %*
if errorlevel 1 (
  echo.
  echo   [!] 汉化过程遇到错误，请查看上方提示信息。
  echo.
  pause
  exit /b 1
)

echo.
echo   ========================================
echo   汉化已完成！
echo   ========================================
echo.
set /p LAUNCH="是否现在立即启动 Claude Desktop？(Y/N，默认 Y): "
if /i "%LAUNCH%"=="" set LAUNCH=Y
if /i "%LAUNCH%"=="Y" (
  start "" "%LOCALAPPDATA%\AnthropicClaude\claude.exe"
  echo 已启动 Claude Desktop。
)
echo.
pause
