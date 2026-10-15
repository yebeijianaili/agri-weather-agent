# 工程化验收矩阵

本矩阵把课程 D3-D11 要求与参考工程总结中的可复用做法落到当前项目；只记录当前仓库可核验的事实，不复制他人的项目数量、提交次数或评分。

## 1. 课程要求映射

| 阶段 | 当前实现 | 证据 |
|---|---|---|
| D3 REST/上传/CORS/容错 | FastAPI、CSV multipart、结构化错误、CORS | `app.py`、`tests/test_api.py` |
| D4 分析/绘图/报告 | 分析服务、Canvas 图表数据、三格式报告 | `services/analysis.py`、`services/exporter.py` |
| D5-D6 前端与联调 | 上传、筛选、结果、图表、下载、状态反馈 | `static/`、`tests/test_frontend_contract.py` |
| D7/D9 FastMCP | 三个领域工具和真实调用网关 | `mcp_server.py`、`services/fastmcp_gateway.py` |
| D8 需求与正式数据 | 需求、字段、调研、NASA 来源与哈希 | `docs/`、`data/*.provenance.json` |
| D10 Agent 闭环 | NL - 本地模型 - 证据 - 回答 - runtime | `services/ollama_agent.py`、Agent 测试 |
| D11 Excel/回归/README | XLSX 四工作表、原生图表、闭环脚本、运行文档 | `/api/export/report.xlsx`、`scripts/verify_release.py` |

## 2. 工程质量映射

| 工程做法 | 落地状态 |
|---|---|
| 分层架构 | `domain / repositories / services / static / tests / scripts` |
| 工程三件套 | `README.md / .gitignore / requirements.txt` |
| 配置模板 | `.env.example` |
| 部署入口 | `start_platform.cmd / stop_platform.cmd / Dockerfile` |
| 规格与日志 | `docs/SPEC.md / docs/DEVLOG.md` |
| AI 协作约束 | `AGENTS.md / docs/TEST_PROMPTS.md` |
| 离线自动测试 | 47 项 pytest，隔离数据库与 Mock Transport |
| 数据真实性 | NASA POWER 查询 URL、原始 JSON、参数单位、哈希、来源接口 |
| 可审计交付 | 导入、分析、问答和三种导出均写审计或验证证据 |

## 3. 发布门禁

```powershell
cd agri-weather-agent
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe scripts\verify_release.py
```

通过标准：测试无失败；`api_verification.json` 工作流含 Excel export；`analysis_report.xlsx` 可由 `openpyxl` 重读且包含四个工作表和图表。

