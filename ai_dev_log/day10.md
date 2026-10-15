# Day10 AI 开发记录 - Agent 调度、图表与容错

日期：2026-08-30  
学生：张轩茂（072024141）

## 今日目标

让自然语言问题真实触发后端 Agent，并把本地模型运行状态、证据引用、FastMCP 工具调用与分析结果回显到页面。

## AI 初稿与人工改造

- AI 初稿建议直接让模型回答；人工改为“后端检索结构化上下文 - 模型生成 JSON - 服务端证据校验 - 回填可信证据”。
- AI 初稿只记录文本日志；人工补充 `analysis_id`、`tool`、`arguments`、`latency_ms`、`audit_id` 和 runtime。
- AI 初稿依赖在线模型；人工保留 Ollama 本地模型，并用 Mock Transport 测试断连、超时、坏 JSON、证据不足和重试回退。

## 实际结果

- FastMCP 可发现并调用 3 个农业气象工具；
- Agent 回答显示 `local_model` 或真实 `rule_fallback` 状态；
- 指标数字由服务端证据拥有，模型不参与重算；
- 前端展示工具调用、风险证据、月度与季节图表。

## 验证

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests\test_fastmcp.py tests\test_ollama_agent.py tests\test_frontend_contract.py
```
