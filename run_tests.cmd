@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
echo [农田气象时序分析 Agent] 正在运行测试...
if exist ".venv\Scripts\python.exe" (
  set "PYTHON=.venv\Scripts\python.exe"
) else (
  set "PYTHON=python"
)
%PYTHON% -m pytest -q
set "EXIT_CODE=%ERRORLEVEL%"
if not "%EXIT_CODE%"=="0" (
  echo.
  echo 测试未通过，请根据上方信息修复后重试。
) else (
  echo.
  echo 全部测试通过。
)
exit /b %EXIT_CODE%

