@echo off
setlocal
chcp 65001 >nul
for /f "tokens=5" %%P in ('netstat -ano ^| findstr /R /C:":8003 .*LISTENING"') do (
  echo 正在停止端口 8003 的进程 %%P...
  taskkill /PID %%P /F >nul 2>&1
)
echo 农田气象时序分析 Agent 已停止。

