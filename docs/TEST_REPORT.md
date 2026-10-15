# 农田气象时序分析 Agent：测试与验收报告

| 项目 | 结果 |
|---|---|
| 工程版本 | **v1.2.0** |
| 验证日期 | 2026-08-22 |
| 验证环境 | Windows，Python 3.13.2，Chrome Headless |
| 自动化测试 | **47 passed in 2.20s** |
| 默认正式数据 | **PASS：NASA POWER 2024 深圳三网格，1,098 条** |
| 来源追溯 | **PASS：POWER Daily API v2.9.7 / 查询 URL / JSON 来源快照 / SHA-256** |
| 本地模型动态问答 | **PASS：qwen2.5:3b / local_model / fallback=false** |
| Ollama 处理器 | **100% GPU** |
| 浏览器运行异常 / 控制台错误 | **0 / 0** |
| 正式来源截图 / 动态回答截图 | **PASS** |

本报告区分三套验证数据：默认运行库使用 NASA POWER 正式公开数据；`pytest` 使用隔离临时数据库和合成夹具；`verify_release.py` 使用专门含缺测的 CSV 验证导入、回滚和导出闭环。测试夹具不得被描述为页面当前正式数据。

## 1. 验证命令

本轮实际执行或由证据脚本记录的命令：

```powershell
cd agri-weather-agent
python -m pytest -q
python scripts\verify_release.py
python scripts\verify_local_agent.py
python .runtime\capture_contextual_official.py
```

主要机器可读证据：

- `docs/evidence/local_model_verification.json`
- `docs/evidence/contextual_ai_official_data_verification.json`
- `docs/evidence/contextual_official_browser_verification.json`
- `docs/evidence/api_verification.json`

## 2. 自动化回归

本轮重新运行结果：

```text
.................................                                        [100%]
47 passed in 2.20s
```

覆盖范围：

- v1.2.0 健康检查、1,098 条隔离播种数据、筛选、分页和空结果；
- 正式来源保存、活动来源读取、`/api/data/source` 和概览来源关联；
- CSV 必填表头/数值、可选湿度、物理范围、重复冲突、失败审计；
- `replace=true` 的事务故障回滚；
- 四季/月份聚合、跨年 `season_year`、趋势、IQR 异常、风险事件；
- NASA POWER 响应字段归一化以及 `MJ/m²/day → W/m²` 换算；
- 问题相关上下文、月度排行榜、证据 ID 映射和数据来源证据；
- Ollama JSON Schema、动态回答、普通对话 100 字限制、时间标识剔除、风险语义与指标数字护栏，以及断连/超时/坏 JSON 回退；
- `agent_runtime` 顶层、`context.agent_runtime` 和历史持久化一致性；
- 分析成功/失败隔离、CSV/Markdown 下载和筛选预览不污染历史。

当前 47 项测试分布在 `test_api.py`、`test_domain_rules.py`、`test_import_transaction.py`、`test_nasa_power.py`、`test_ollama_agent.py`。测试不访问默认运行库、NASA 网络 API 或真实 Ollama。

## 3. NASA POWER 正式数据验证

### 3.1 来源与数量

| 项目 | 实际值 |
|---|---|
| provider | NASA Langley Research Center POWER Project |
| dataset | NASA/POWER Source Native Resolution Daily Data |
| API | POWER Daily API v2.9.7 |
| 参数 | T2M、PRECTOTCORR、ALLSKY_SFC_SW_DWN、RH2M |
| 时段 | 2024-01-01 至 2024-12-31，366 日 |
| 网格 | 光明、龙岗、大鹏 |
| 数量 | 3 × 366 = **1,098** |
| CSV SHA-256 | `2d2c706261acdfc6ce49870813e60e1ada5305161ec64f8c208c743d0861b577` |

`GET /api/health` 已返回 `version=1.2.0`、`records=1098` 和 NASA 来源摘要；`GET /api/data/source` 已返回三个查询 URL、请求/响应坐标、参数/单位、获取时间、源文件哈希和归一化文件哈希。现有 2026-08-22 文件已明确标注为解析后重排版的 `normalized_json_snapshot`，不是原 HTTP 字节；2026-08-23 重新请求三个官方 URL 并比较 4,392 个参数日值，差异为 0。后续抓取使用 `http_response_body` 模式。`contextual_ai_official_data_verification.json` 中 `api_version_1_2`、`official_source_registered`、`official_records_loaded` 均为 `true`。

### 3.2 数据产物与单位换算

- `data/nasa_power_shenzhen_2024.csv`
- `data/nasa_power_shenzhen_2024.provenance.json`
- `data/raw/nasa_power_2024/nasa-gm_20240101_20241231.json`
- `data/raw/nasa_power_2024/nasa-lg_20240101_20241231.json`
- `data/raw/nasa_power_2024/nasa-dp_20240101_20241231.json`

太阳辐射来源单位为 MJ/m²/day，脚本按 `value × 1,000,000 ÷ 86,400` 转为日均 W/m²；单元测试使用 8.64 MJ/m²/day 验证结果为 100 W/m²。三个点是 NASA POWER 网格化卫星/再分析产品，不等同于地面站或田间传感器实测。

## 4. v1.2.0 动态本地模型 Agent

### 4.1 已实现链路

```text
completed 分析结果
  → 按问题检索概览 / 季节 / 月度 / 排名 / 指定月份区间 / 趋势 / 风险 / 质量 / 来源
  → 生成紧凑 analysis_context + 服务端 evidence_catalog + 规则回退正文
  → qwen2.5:3b 自主生成 answer + evidence_ids
  → JSON Schema、主题、月份、风险语义、引用和指标数字校验
  → 首次失败自动反馈原因并重试一次
  → 通过则服务端回填可信 evidence，mode=local_model
  → 调用或两次校验仍失败才返回规则正文，mode=rule_fallback
```

| 层次 | 当前职责 | 保护措施 |
|---|---|---|
| 确定性分析层 | 计算指标、范围、排名、区间汇总、风险和来源 | 模型不重算或写回统计 |
| 问题相关检索层 | 只选本题所需事实和证据目录 | 避免把全量结果机械塞给 3B 模型 |
| 本地生成层 | `qwen2.5:3b` 理解问题并组织一至三句自然回答 | JSON Schema 只允许 `answer/evidence_ids` |
| 服务端证据层 | 映射最多 4 个可信证据，验证主题、月份、风险语义和指标数字 | 指标数字只能来自服务端证据 value；年份、月份、日期仅作为时间标识处理 |
| 自动回退 | 覆盖 provider、模型缺失、断连、超时、HTTP/传输、JSON/结构/证据/指标数字失败，以及 `too_verbose`、`inconsistent_risk_claim` | 问答保持 HTTP 200，原因写入 runtime |

### 4.2 真实动态回答

正式数据现场验证的代表性结果：

1. **跨 12 个月排行并询问来源**

   > 最热的三个月是7月、8月和9月，平均气温分别为29.49°C、29.11°C和28.80°C。正式数据来自NASA/POWER Source Native Resolution Daily Data。

   返回 7/8/9 月三条 `month.*.avg_temperature_c` 证据和一条 `data.source` 证据。

2. **指定 9—11 月综合提问**

   > 9月到11月的平均气温为25.53°C，累计降水为419.61mm，平均太阳辐射为175.58W/m²。这个时期最长连续少雨天数为32天，光照条件相对较好。

   返回四条服务端证据，覆盖区间温度、降水、辐射和最长少雨。

3. **普通能力对话**

   > 这套数据可以回答关于降水、温度、光照和风险的问题。

   普通对话不伪造数据证据，`evidence=[]`。

上述三次均为 `mode=local_model`、`fallback_used=false`。`contextual_ai_official_data_verification.json` 的八项检查全部为 `true`，总结果 `passed=true`。

### 4.3 Ollama 与 GPU 现场状态

`scripts/verify_local_agent.py` 在本机访问 `127.0.0.1:11434` 与 `127.0.0.1:8003`，当前证据写入 `docs/evidence/local_model_verification.json`：

| 现场项目 | 实际结果 |
|---|---|
| Ollama | `0.32.15` |
| 模型 | `qwen2.5:3b`，3.1B，Q4_K_M |
| 模型 VRAM | 2,159,374,499 bytes |
| Ollama processor | **100% GPU** |
| GPU | NVIDIA GeForce RTX 4060 Laptop GPU，8,188 MiB |
| 平台 | `version=1.2.0`，NASA source registered，1,098 records |
| 动态查询 | `local_model`，`fallback_used=false`，四条月度/来源证据 |

该 JSON 的 14 项检查全部为 `true`，`passed=true`。

## 5. 隔离式发布闭环

`scripts/verify_release.py` 使用 `.runtime/release_verify.db` 和 `data/sample_weather_2024.csv`，在 rules provider 下验证工程链路：

```text
CSV 导入 → 质量校验 → 时序分析 → 规则回退问答 → CSV 导出 → Markdown 导出 → Excel 导出 → 审计回读
```

这是导入边界夹具，不是默认 NASA 正式数据。当前结果：

| 指标 | 实际值 |
|---|---:|
| CSV 总行数 | 1,098 |
| 接受并写入 | 1,095 |
| 核心光照缺测拒绝 | 3 |
| 可选湿度缺测告警 | 2 |
| 分析有效记录 | 1,093 |
| 地块 | 3 |
| 数据质量得分 | 99.9 |
| 已触发风险事件 | 7 |
| Agent 规则证据 | 3 |
| 季节导出行 | 15 |
| 审计事件回读 | 2 |

实际产物：

- `exports/seasonal_statistics.csv`，2,129 bytes，SHA-256 `a1f4cf1eaae8080014e391c03c0e7e1c0b889fcf23e39d092f6a6b4bea318a9d`
- `exports/analysis_report.md`，4,064 bytes，SHA-256 `57586a796222fa5f0cacdefa6319d9a139dd744bc2f2ff111b4c9095ee86a463`
- `docs/evidence/api_verification.json`
- `docs/evidence/06_release_closed_loop.png`

## 6. 浏览器端到端验证

`.runtime/capture_contextual_official.py` 完成：

1. 打开 `http://127.0.0.1:8003/`；
2. 核对 Hero 区显示“NASA POWER · 正式公开数据”，链接为 NASA POWER 官方主页；
3. 提问“请你自己比较12个月，告诉我最热的三个月，并说明正式数据来自哪里。”；
4. 核对动态回答包含 7、8、9 月和三个温度；
5. 核对三张月度证据卡和一张正式来源证据卡；
6. 核对 Agent 徽标为 `local_model`；
7. 收集页面运行异常和控制台错误。

结果：来源可见且可点击；回答正确；证据卡 4 张；运行异常 0；控制台错误 0。机器可读结果 `contextual_official_browser_verification.json` 全部七项检查为 `true`，`passed=true`。

关键截图：

- `docs/evidence/10_official_data_source.png`
- `docs/evidence/11_contextual_ai_answer.png`

全站布局与移动端的既有证据仍保存在 `01_dashboard_desktop.png`、`02_analysis_risks.png`、`03_agent_evidence.png`、`04_dashboard_mobile.png`、`07_local_model_agent.png`、`08_local_model_mobile.png`。

## 7. 验收矩阵

| 验收项 | 结果 | 证据 |
|---|---|---|
| v1.2.0 健康检查 | 通过 | HTTP 200，version=1.2.0 |
| NASA 正式数据 | 通过 | 三网格×366 日=1,098 条 |
| 来源可追溯 | 通过 | `/api/data/source`、provenance、查询 URL、SHA-256 |
| CSV 导入与事务回滚 | 通过 | 47 项测试、`api_verification.json` |
| 地块/年份/日期/季节筛选 | 通过 | API 测试与前端联动 |
| 跨年冬季 | 通过 | `season_year` 单元测试 |
| 时序、月份与四季统计 | 通过 | 分析结果、Canvas 图表、季节 CSV |
| 风险事件证据 | 通过 | 地块、起止、持续天数、峰值、阈值 |
| 动态本地模型回答 | 通过 | 排行/区间/对话三类问题，均 local_model |
| 模型证据和数字护栏 | 通过 | JSON Schema、证据映射、时间标识剔除、`too_verbose`、`inconsistent_risk_claim`、重试/回退测试 |
| Ollama 目标机器现场 runtime | 通过 | qwen2.5:3b，100% GPU，fallback=false |
| CSV/Markdown/Excel 导出 | 通过 | 文件重读、行数、字节数与哈希 |
| 正式来源网页展示 | 通过 | `10_official_data_source.png` |
| 动态回答网页展示 | 通过 | `11_contextual_ai_answer.png`，4 张证据卡 |
| 浏览器零错误门禁 | 通过 | runtime_exceptions=0，console_errors=0 |

## 8. 结论

v1.2.0 已形成“NASA 正式数据获取与追溯—质量校验—持久化—分析—风险—问题相关上下文检索—本地模型动态回答—服务端证据校验/失败回退—导出—审计—历史复核”的工程闭环。当前 **47 项自动化测试通过**；默认运行库为 NASA POWER 2024 深圳三网格 **1,098 条正式公开数据**；`qwen2.5:3b` 已真实生成月度排行和区间分析，现场为 `local_model`、`fallback_used=false`；API、GPU、浏览器 JSON 与截图证据相互印证。





