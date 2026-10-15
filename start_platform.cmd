@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"

set "AGRI_AGENT_PROVIDER=ollama"
set "AGRI_OLLAMA_BASE_URL=http://127.0.0.1:11434"
set "AGRI_OLLAMA_MODEL=qwen2.5:3b"
set "AGRI_OLLAMA_TIMEOUT_SECONDS=60"
set "AGRI_AGENT_FALLBACK=rules"

echo [1/4] 检查 Python 运行环境...
if not exist ".venv\Scripts\python.exe" (
  echo 正在创建项目虚拟环境 .venv...
  python -m venv .venv
)
set "PYTHON=.venv\Scripts\python.exe"
%PYTHON% -c "import fastapi, uvicorn, fastmcp" 2>nul
if errorlevel 1 (
  echo 缺少运行依赖，开始安装 requirements.txt...
  %PYTHON% -m pip install -r requirements.txt
  if errorlevel 1 exit /b 1
)

echo [2/4] 检查本地 Ollama 与 %AGRI_OLLAMA_MODEL%...
powershell -NoProfile -ExecutionPolicy Bypass -File "scripts\ensure_local_model.ps1"
if errorlevel 1 (
  echo 本地模型检查未通过，平台将以规则回退模式继续启动。
)

echo [3/4] 运行快速回归测试...
%PYTHON% -m pytest -q
if errorlevel 1 (
  echo 测试未通过，服务未启动。
  exit /b 1
)

echo [4/4] 启动 http://127.0.0.1:8003 ...
start "" powershell -NoProfile -WindowStyle Hidden -Command "Start-Sleep -Seconds 2; Start-Process 'http://127.0.0.1:8003/'"
%PYTHON% -m uvicorn app:app --host 0.0.0.0 --port 8003
