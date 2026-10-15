# 农田气象时序分析 Agent：HTTP API 实现契约

| 属性 | 当前值 |
|---|---|
| 应用版本 | `1.2.0` |
| 核对日期 | 2026-08-23 |
| 默认地址 | `http://127.0.0.1:8003` |
| API 前缀 | `/api` |
| 交互式文档 | `/docs` |
| OpenAPI | `/openapi.json` |

本文同步自当前 `app.py`、`repositories/database.py`、`services/*` 和 FastAPI 实际 OpenAPI，不描述尚未落地的提案。

## 1. 通用契约

### 1.1 媒体类型与标识

- JSON：`application/json`。
- CSV：`text/csv; charset=utf-8`，正文含 UTF-8 BOM。
- Markdown：`text/markdown; charset=utf-8`。
- `analysis_id`、`import_id`、`query_id`、`audit_id` 均为 SQLite 自增整数；筛选预览导出的 `analysis_id` 例外，为字符串 `filtered`。
- 日期为 `YYYY-MM-DD`；服务端业务时间为带时区 ISO 8601 字符串。

### 1.2 通用筛选

以下字段用于概览、观测、分析、Agent 和导出；多个条件按 AND 组合。

| 参数 | 类型/范围 | 已实现语义 |
|---|---|---|
| `plot_id` | string/null | 单个地块精确匹配；不接受数组或逗号列表 |
| `year` | integer/null，1900–2100 | 日历年 |
| `season_year` | integer/null，1900–2101 | 12 月计入下一季节年，其余月份保持日历年 |
| `season` | string/null | 接受 `spring/summer/autumn/fall/winter`、`春/夏/秋/冬`、中文全称；响应统一为中文全称 |
| `start_date` | date/null | 含开始日 |
| `end_date` | date/null | 含结束日 |

开始日晚于结束日、季节非法或范围非法时返回 `422`。

### 1.3 分页

历史/列表响应统一为：

```json
{
  "items": [],
  "total": 0,
  "limit": 20,
  "offset": 0
}
```

| 路径 | `limit` 默认/最大 | `offset` |
|---|---:|---:|
| `/api/observations` | 200/5000 | 默认 0，最小 0 |
| `/api/imports/history` | 20/100 | 同左 |
| `/api/analysis/history` | 20/100 | 同左 |
| `/api/agent/history` | 20/100 | 同左 |
| `/api/audit/events` | 50/200 | 同左 |

`limit` 最小为 1。

### 1.4 错误结构与状态码

当前没有统一的 `error/code/request_id` 信封。业务错误通常为：

```json
{
  "detail": "分析任务不存在"
}
```

需要回传业务记录时为：

```json
{
  "detail": {
    "message": "筛选范围内没有观测数据",
    "analysis_id": 3,
    "status": "failed"
  }
}
```

一般 FastAPI 校验错误为：

```json
{
  "detail": [
    {
      "type": "greater_than_equal",
      "loc": ["query", "limit"],
      "msg": "Input should be greater than or equal to 1",
      "input": "0",
      "ctx": {"ge": 1}
    }
  ]
}
```

| 状态 | 当前触发场景 |
|---:|---|
| 200 | 查询、导入、同步分析、问答和导出成功 |
| 404 | 分析/结果不存在或筛选范围无数据 |
| 409 | 失败分析不能问答或导出 |
| 413 | CSV 大于 20 MiB |
| 415 | 文件名不是 `.csv` |
| 422 | 参数/模型校验或 CSV 文件级结构校验失败 |
| 503 | 健康检查访问数据库失败 |

同步实现不返回 `201/202`；相同文件 SHA-256 不触发 `409`。路由没有声明细粒度响应模型，所以生成的 OpenAPI 主要列出 `200` 和自动 `422`；代码显式产生的 `404/409/413/415/503` 仍是实际契约。

### 1.5 Agent runtime

标准启动脚本与直接创建应用均默认启用本地 Ollama；未设置 `AGRI_AGENT_PROVIDER` 时仍会请求 `qwen2.5:3b`，只有模型调用或输出校验失败才自动返回规则答案。代码默认值、配置模板和标准启动脚本的取值如下：

| 环境变量 | 代码默认值（无环境变量） | `.env.example` | `start_platform.cmd` | 语义 |
|---|---|---|---|---|
| `AGRI_AGENT_PROVIDER` | `ollama` | `ollama` | `ollama` | `ollama` 请求本地模型；仅显式设为 `rules/rule/disabled/none` 才跳过模型 |
| `AGRI_OLLAMA_BASE_URL` | `http://127.0.0.1:11434` | 同代码默认值 | 同代码默认值 | Ollama API 根地址 |
| `AGRI_OLLAMA_MODEL` | `qwen2.5:3b` | 同代码默认值 | 同代码默认值 | 基于结构化上下文生成回答的本地模型 |
| `AGRI_OLLAMA_TIMEOUT_SECONDS` | `60` | `60` | `60` | 单次模型请求超时秒数；非法或不在 0.1–300 范围时回到代码默认值 |
| `AGRI_AGENT_FALLBACK` | `rules` | `rules` | `rules` | 模型异常时返回确定性规则答案 |

应用不会自动加载 `.env.example`；配置由启动脚本或父进程写入环境。`agent_runtime` 用同一结构出现在健康检查、问答响应以及问答历史的 `context.agent_runtime`：

```json
{
  "mode": "local_model",
  "provider": "ollama",
  "model": "qwen2.5:3b",
  "ready": true,
  "fallback_used": false,
  "fallback_reason": null,
  "latency_ms": 286
}
```

`mode` 只有 `local_model`、`rule_fallback`。`latency_ms` 始终存在，为大于等于 0 的整数。回退原因可能为 `provider_disabled`、`unsupported_provider`、`model_unavailable`、`unreachable`、`timeout`、`http_error`、`transport_error`、`invalid_json`、`invalid_response`、`missing_citations`、`incomplete_answer`、`unsupported_numbers`、`too_verbose`、`inconsistent_risk_claim`。其中 `too_verbose` 表示重试后最终普通对话正文仍超过 100 字；`inconsistent_risk_claim` 表示风险陈述与引用的 `risk.*` 状态或触发条件矛盾。

## 2. 路由总览

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/health` | 服务、数据库、记录数与 Agent runtime |
| GET | `/api/data/source` | 当前活动数据来源及完整 provenance |
| GET | `/api/overview` | 数据集、KPI、质量概览 |
| GET | `/api/observations` | 观测分页查询 |
| POST | `/api/observations/import` | CSV 导入/整库替换 |
| GET | `/api/imports/history` | 导入历史 |
| POST | `/api/analysis/run` | 同步分析并保存 |
| GET | `/api/analysis/latest` | 最新成功分析 |
| GET | `/api/analysis/history` | 全部分析历史 |
| GET | `/api/analysis/{analysis_id}` | 指定分析详情 |
| POST | `/api/agent/query` | 问题相关结构化上下文 + 本地模型动态生成；失败时规则回退 |
| GET | `/api/agent/history` | 问答历史及 runtime 上下文 |
| GET | `/api/export/seasonal.csv` | CSV 下载 |
| GET | `/api/export/report.md` | Markdown 下载 |
| GET | `/api/export/report.xlsx` | Excel 工程工作簿下载 |
| GET | `/api/audit/events` | 导出审计 |

`/` 返回静态前端且不进 OpenAPI；`/static/*` 提供资源。

## 3. 健康、概览与观测

### `GET /api/health`

```json
{
  "status": "ok",
  "service": "农田气象时序分析 Agent",
  "version": "1.2.0",
  "database": "connected",
  "records": 1098,
  "data_source": {
    "source_id": 1,
    "provider": "NASA Langley Research Center POWER Project",
    "dataset_name": "NASA/POWER Source Native Resolution Daily Data",
    "retrieved_at": "2026-08-22T18:03:19+08:00"
  },
  "agent_runtime": {
    "mode": "local_model",
    "provider": "ollama",
    "model": "qwen2.5:3b",
    "ready": true,
    "fallback_used": false,
    "fallback_reason": null,
    "latency_ms": 18
  }
}
```

Ollama 离线不会把主健康状态改为失败，因为规则回退仍可服务：

```json
{
  "status": "ok",
  "service": "农田气象时序分析 Agent",
  "version": "1.2.0",
  "database": "connected",
  "records": 1098,
  "data_source": {
    "source_id": 1,
    "provider": "NASA Langley Research Center POWER Project",
    "dataset_name": "NASA/POWER Source Native Resolution Daily Data",
    "retrieved_at": "2026-08-22T18:03:19+08:00"
  },
  "agent_runtime": {
    "mode": "rule_fallback",
    "provider": "ollama",
    "model": "qwen2.5:3b",
    "ready": false,
    "fallback_used": true,
    "fallback_reason": "unreachable",
    "latency_ms": 2
  }
}
```

`status/database/records` 只反映主服务与 SQLite；`data_source` 是当前活动来源的摘要，未登记时为 `null`；`agent_runtime` 独立反映模型可用性。配置为 `rules` 时回退原因为 `provider_disabled`；Ollama 已启动但未拉取指定模型时为 `model_unavailable`。

数据库异常返回 `503`：

```json
{
  "status": "degraded",
  "service": "农田气象时序分析 Agent",
  "database": "disconnected",
  "error": "OperationalError",
  "agent_runtime": {
    "mode": "rule_fallback",
    "provider": "ollama",
    "model": "qwen2.5:3b",
    "ready": false,
    "fallback_used": true,
    "fallback_reason": "unreachable",
    "latency_ms": 2
  }
}
```

### `GET /api/data/source`

返回当前活动数据来源。默认运行库已登记 NASA POWER 2024 深圳三点网格化数据；`provenance` 保存 API 名称和版本、获取时间、时间标准、参数与单位、三个查询 URL/坐标/JSON 快照哈希，以及归一化 CSV 的记录数和 SHA-256。现有 2026-08-22 快照的 `raw_capture_mode=normalized_json_snapshot`，表示内容经过 JSON 解析和重排版、不是原 HTTP 字节；2026-08-23 已复核 4,392 个参数日值且差异为 0。更新后的获取脚本后续写入 `raw_capture_mode=http_response_body` 并直接哈希 HTTP 响应体。该接口不接受筛选参数。

```json
{
  "source": {
    "source_id": 1,
    "created_at": "2026-08-22T18:03:19+08:00",
    "source_key": "nasa-power-shenzhen-daily-20240101-20241231",
    "provider": "NASA Langley Research Center POWER Project",
    "dataset_name": "NASA/POWER Source Native Resolution Daily Data",
    "landing_page": "https://power.larc.nasa.gov/",
    "query_urls": ["https://power.larc.nasa.gov/api/temporal/daily/point?..."],
    "active": true,
    "provenance": {
      "api_name": "POWER Daily API",
      "api_version": "v2.9.7",
      "time_standard": "LST",
      "raw_capture_mode": "normalized_json_snapshot",
      "community": "AG",
      "period": {"start": "2024-01-01", "end": "2024-12-31", "days": 366},
      "normalized_file": {
        "path": "data\\nasa_power_shenzhen_2024.csv",
        "records": 1098,
        "sha256": "2d2c706261acdfc6ce49870813e60e1ada5305161ec64f8c208c743d0861b577"
      }
    }
  }
}
```

未登记外部来源时仍返回 `200`：`{"source":null,"message":"当前数据库未登记外部数据来源"}`。POWER 三个坐标返回的是网格化卫星/再分析产品，不代表地面气象站或田间传感器实测。

### `GET /api/overview`

支持全部通用筛选。默认 NASA POWER 正式数据响应（为便于阅读，`dataset.source.provenance` 详见 `/api/data/source`）：

```json
{
  "filters": {},
  "dataset": {
    "total_records": 1098,
    "plot_count": 3,
    "date_start": "2024-01-01",
    "date_end": "2024-12-31",
    "plots": ["NASA-DP 大鹏网格", "NASA-GM 光明网格", "NASA-LG 龙岗网格"],
    "years": [2024],
    "last_import": {
      "import_id": 1,
      "filename": "nasa_power_shenzhen_2024.csv",
      "sha256": "2d2c706261acdfc6ce49870813e60e1ada5305161ec64f8c208c743d0861b577",
      "total_rows": 1098,
      "valid_rows": 1098,
      "imported": 1098,
      "rejected": 0,
      "warning_rows": 0
    },
    "last_analysis": {
      "analysis_id": 16,
      "created_at": "2026-08-22T19:08:30+08:00",
      "status": "completed"
    },
    "source": {
      "source_id": 1,
      "source_key": "nasa-power-shenzhen-daily-20240101-20241231",
      "provider": "NASA Langley Research Center POWER Project",
      "dataset_name": "NASA/POWER Source Native Resolution Daily Data",
      "active": true
    }
  },
  "kpis": {
    "avg_temperature_c": 24.09,
    "min_temperature_c": 8.32,
    "max_temperature_c": 30.61,
    "total_precipitation_mm": 2279.91,
    "avg_solar_radiation_wm2": 165.24,
    "avg_humidity_pct": 80.54,
    "rainy_days": 261,
    "longest_dry_spell_days": 35,
    "analyzed_records": 1098,
    "excluded_non_valid_records": 0
  },
  "quality": {
    "scope": "ingestion_quality",
    "scope_label": "入库质量",
    "score_label": "入库质量得分",
    "interpretation": "仅评价字段完整性、类型、工程范围、缺测、质量标记和重复；不代表上游数据的测量准确性或现场代表性。",
    "score": 100.0,
    "grade": "优秀",
    "missing_values": 0,
    "missing_by_field": {
      "temperature_c": 0,
      "precipitation_mm": 0,
      "solar_radiation_wm2": 0,
      "humidity_pct": 0
    },
    "suspect_records": 0,
    "out_of_range_values": 0,
    "duplicate_records": 0,
    "valid_records": 1098,
    "total_records": 1098,
    "processing": {
      "missing_policy": "按指标剔除缺测单元，不丢弃整行；结果保留有效样本数",
      "duplicate_policy": "timestamp + plot_id 唯一键去重；默认保留库内原记录，冲突进入导入审计；replace=true 仅用于整库替换",
      "anomaly_policy": "物理范围先校验，统计异常采用 1.5×IQR 标记但保留原值"
    },
    "coverage_pct": {
      "temperature_c": 100.0,
      "precipitation_mm": 100.0,
      "solar_radiation_wm2": 100.0,
      "humidity_pct": 100.0
    }
  }
}
```

`last_import/last_analysis` 是全局最新摘要。无匹配观测仍返回 `200`，此时 `kpis={}`。多地块累计降水先算区域日均再跨日累计。

### `GET /api/observations`

支持通用筛选及分页，按 `timestamp, plot_id` 升序：

```json
{
  "total": 1098,
  "limit": 1,
  "offset": 0,
  "items": [
    {
      "id": 1831,
      "timestamp": "2024-01-01",
      "plot_id": "NASA-DP 大鹏网格",
      "temperature_c": 19.33,
      "precipitation_mm": 0.04,
      "solar_radiation_wm2": 157.64,
      "humidity_pct": 80.14,
      "quality_flag": "valid"
    }
  ]
}
```

响应不回显 `filters`；数值可为 `null`。

## 4. CSV 导入与历史

### `POST /api/observations/import`

- 请求：`multipart/form-data`，文件字段 `file`。
- 查询参数：`replace=false`。`true` 时，存在可解析行才清空观测表并插入本批有效唯一行。
- 只检查文件名 `.csv`，不检查 MIME；编码为 UTF-8/UTF-8-BOM/GB18030。
- 必填列：`timestamp,plot_id,temperature_c,precipitation_mm,solar_radiation_wm2`；湿度和质量标记可选；导入器支持中英文字段别名。
- 温度范围 -50..60、降水 0..500、辐射 0..1400、湿度 0..100。
- 只要导入实际改变数据集，活动来源同步更新：`replace=true` 登记 `source_type=user_csv`；追加到已有记录登记 `source_type=mixed_dataset`。无新增记录时保留原来源。

混合有效/错误行返回 `200`，有效行写入：

```json
{
  "import_id": 1,
  "created_at": "2026-08-22T15:17:00+08:00",
  "filename": "weather.csv",
  "sha256": "11483df07dc2fea3ac81baa83718fd53cf942c42c208d2e275976ae616d476e8",
  "replace": false,
  "total_rows": 2,
  "valid_rows": 1,
  "imported": 1,
  "updated": 0,
  "rejected": 1,
  "warning_rows": 0,
  "exact_duplicates": 0,
  "conflict_duplicates": 0,
  "duplicates": 0,
  "reason_counts": {"invalid_date": 1},
  "warning_counts": {},
  "errors": [
    {"line": 3, "code": "invalid_date", "message": "日期格式无效: bad-date"}
  ],
  "warnings": [],
  "truncated_errors": 0,
  "truncated_warnings": 0,
  "batch_duplicate_exact": 0,
  "batch_duplicate_conflicts": 0,
  "data_source": {
    "provider": "混合来源数据集",
    "provenance": {"source_type": "mixed_dataset"}
  },
  "quality": {
    "scope": "ingestion_quality",
    "score": 100.0,
    "grade": "优秀",
    "valid_records": 1099,
    "total_records": 1099
  },
  "message": "导入完成：新增 1 条，更新 0 条，拒绝 1 条。"
}
```

`errors/warnings` 各最多 20 条。库内/批内同键同值为 `duplicate_exact` 警告；同键异值为 `duplicate_conflict` 错误，默认不覆盖。SHA-256 仅记录、不拦截重传；`updated` 当前恒为 0。`quality.score` 是入库质量分，只覆盖缺测、工程范围、质量标记和重复，不表示数据已通过外部真实性或测量精度认证。

文件结构错误会保存导入批次并返回 `422`：

```json
{
  "detail": {
    "message": "CSV 缺少必填列: precipitation_mm, solar_radiation_wm2",
    "import_id": 2
  }
}
```

非 CSV 后缀返回 `415`：

```json
{
  "detail": "仅支持 CSV 文件"
}
```

超过 20 MiB 返回 `413`，`detail` 为 `CSV 文件不能超过 20 MB`。

### `GET /api/imports/history`

`limit` 默认 20/最大 100，按 ID 降序：

```json
{
  "items": [
    {
      "import_id": 1,
      "created_at": "2026-08-22T15:17:00+08:00",
      "filename": "weather.csv",
      "sha256": "11483df07dc2fea3ac81baa83718fd53cf942c42c208d2e275976ae616d476e8",
      "replace": false,
      "total_rows": 2,
      "valid_rows": 1,
      "imported": 1,
      "rejected": 1,
      "warning_rows": 0,
      "exact_duplicates": 0,
      "conflict_duplicates": 0,
      "reason_counts": {"invalid_date": 1},
      "errors": [],
      "warnings": []
    }
  ],
  "total": 1,
  "limit": 20,
  "offset": 0
}
```

历史项不含即时响应中的 `updated/duplicates/warning_counts/truncated_*/quality/message`。

## 5. 分析

### `POST /api/analysis/run`

请求体必需，内部字段均可选，字段仅为通用筛选字段（是单值 `plot_id`，不是 `plot_ids`）。下方成功响应使用隔离测试夹具展示完整结构，不代表默认 NASA 运行库的当前数值：

```json
{
  "year": 2024,
  "season_year": null,
  "season": "summer",
  "start_date": "2024-06-01",
  "end_date": "2024-08-31"
}
```

同步成功返回 `200` 并持久化：

```json
{
  "analysis_id": 2,
  "created_at": "2026-08-22T15:17:00+08:00",
  "finished_at": "2026-08-22T15:17:00+08:00",
  "status": "completed",
  "filters": {"year": 2024, "season": "夏季"},
  "summary": "完成 275 条、3 个地块、92 天气象记录分析；平均气温 28.44°C，累计区域降水 448.97 mm，数据质量 99.9 分。 高优先级风险：连续高温。",
  "record_count": 275,
  "risk_count": 14,
  "result": {
    "filters": {"year": 2024, "season": "夏季"},
    "data_scope": {
      "source_records": 276,
      "analyzed_records": 275,
      "excluded_non_valid_records": 1,
      "quality_policy": "统计、异常与风险使用 valid/observed/corrected/imputed；suspect/rejected 不进入聚合，全部记录仍进入质量摘要"
    },
    "methodology": {
      "season_definition": "北半球气象季节：春季3–5月、夏季6–8月、秋季9–11月、冬季12–2月；12月归入下一 season_year",
      "filter_calendar_note": "year 筛选使用日历年；跨年冬季请查看 seasonal_by_year 的 season_year",
      "precipitation_method": "多地块先计算区域日均降水，再跨日累加，避免地块数导致累计量膨胀",
      "trend_method": "区域逐日均值的一元线性回归，斜率折算为每30天变化量",
      "anomaly_method": "同一地块、season_year、季节内按指标使用1.5×IQR（n≥8），仅标记、不删除",
      "risk_threshold_version": "agri-weather-default-v1",
      "risk_thresholds": {
        "HEAT_DAY": ">=35°C",
        "HEAT_STREAK": "连续3日>=35°C",
        "COLD_DAY": "<=0°C",
        "HEAVY_RAIN": ">=50 mm/day",
        "DRY_SPELL": "连续7日<1 mm/day",
        "LOW_LIGHT_STREAK": "连续3日<100 W/m²",
        "TEMP_SWING": "相邻日温差>=10°C"
      },
      "risk_note": "规则提示用于田间巡查排序，需结合土壤、作物与现场信息复核"
    },
    "overview": {
      "record_count": 275,
      "plot_count": 3,
      "day_count": 92,
      "date_start": "2024-06-01",
      "date_end": "2024-08-31",
      "avg_temperature_c": 28.44,
      "min_temperature_c": 19.5,
      "max_temperature_c": 37.8,
      "total_precipitation_mm": 448.97,
      "rainy_days": 66,
      "avg_solar_radiation_wm2": 455.61,
      "avg_humidity_pct": 67.7,
      "longest_dry_spell_days": 6
    },
    "quality": {},
    "seasonal": [],
    "seasonal_by_year": [],
    "seasonal_by_plot": [],
    "monthly": [],
    "trends": {},
    "anomalies": {},
    "risks": [],
    "risk_events": [],
    "conclusions": []
  }
}
```

末尾空集合用于展示顶层形状；运行时实际填充：`seasonal` 固定四季、`seasonal_by_year`、`seasonal_by_plot`、`monthly`、四指标 `trends`、IQR `anomalies`、固定 7 条规则摘要 `risks`、全部 `risk_events` 和 5 条 `conclusions`。顶层 `risk_count` 是事件数，不是风险规则种类数。

典型 `seasonal_by_plot` 项：

```json
{
  "season": "夏季",
  "season_year": 2024,
  "season_years": [2024],
  "record_count": 91,
  "day_count": 91,
  "avg_temperature_c": 27.67,
  "min_temperature_c": 19.5,
  "max_temperature_c": 37.8,
  "total_precipitation_mm": 529.3,
  "rainy_days": 30,
  "longest_dry_spell_days": 8,
  "avg_solar_radiation_wm2": 471.36,
  "low_light_days": 0,
  "avg_humidity_pct": 68.37,
  "plot_id": "A-01 北区麦田",
  "period_start": "2024-06-01",
  "period_end": "2024-08-31",
  "expected_days": 92,
  "valid_days": 91,
  "coverage_pct": 98.91,
  "temperature_trend_c_30d": 0.527,
  "outlier_count": 24,
  "risk_count": 7
}
```

无数据时保存失败任务并返回 `404`；请求模型失败保存失败任务并返回定制 `422`：

```json
{
  "detail": {
    "message": "请求参数校验失败",
    "analysis_id": 4,
    "status": "failed",
    "errors": [
      {
        "type": "greater_than_equal",
        "loc": ["body", "year"],
        "msg": "Input should be greater than or equal to 1900",
        "input": 1800,
        "ctx": {"ge": 1900}
      }
    ]
  }
}
```

### `GET /api/analysis/latest`

无查询参数。返回最新 `completed` 记录的完整结构；失败任务不会遮挡之前成功任务。没有成功记录时 `404`：

```json
{
  "detail": "暂无分析记录"
}
```

### `GET /api/analysis/history`

只支持分页、没有 `status` 筛选；按 ID 降序，含完整结果及失败任务：

```json
{
  "items": [
    {
      "analysis_id": 3,
      "created_at": "2026-08-22T15:17:00+08:00",
      "finished_at": "2026-08-22T15:17:00+08:00",
      "status": "failed",
      "filters": {"year": 2030},
      "summary": "筛选范围内没有观测数据",
      "record_count": 0,
      "risk_count": 0,
      "result": {}
    }
  ],
  "total": 3,
  "limit": 20,
  "offset": 0
}
```

### `GET /api/analysis/{analysis_id}`

成功/失败记录都返回 `200`；失败记录的 `result={}`。不存在返回 `404`：

```json
{
  "detail": "分析任务不存在"
}
```

## 6. Agent

### `POST /api/agent/query`

请求含全部可选分析筛选，以及必填 `question`（1–500 字符）和可选 `analysis_id`（整数，最小 1）：

```json
{
  "question": "请你自己比较12个月，告诉我最热的三个月，并说明正式数据来自哪里。",
  "analysis_id": 14
}
```

分析选择顺序：显式指定 `analysis_id` 时只查询该记录，不存在返回 `404`、状态不是 `completed` 返回 `409`；未指定 ID 但存在筛选时同步新建分析；ID 和筛选都没有时使用最新成功分析。

问答先由确定性分析服务构建规则回退正文，同时根据问题检索一份紧凑的只读上下文和证据目录。上下文可包含概览、相关指标的季节/月度值、趋势、风险、质量、数据来源、月度排行榜，以及用户指定月份区间的服务端汇总；无关字段不会全部塞给小模型。Ollama `qwen2.5:3b` 读取 `question + analysis_context + evidence_catalog` 后自主组织正文，并按 JSON Schema 返回 `answer` 和最多 4 个 `evidence_ids`，不是对固定正文做同义改写。

服务端拥有最终证据：它会把模型 ID 映射回目录项，检查问题主题、排名类问题所需月份和风险语义是否被覆盖。指标数字只能来自服务端证据 value；年份、月份、日期仅作为时间标识处理。时间标识会从量化主张 token 中剔除，用户问题里出现的普通数值也不会自动成为可用指标证据。首次校验失败会把具体原因反馈给模型重试一次；调用或两次输出仍未通过时，才返回规则正文与规则证据。任何模型故障仍保持 HTTP `200`，并以 `agent_runtime.mode=rule_fallback` 标记。

```json
{
  "answer": "最热的三个月是7月、8月和9月，平均气温分别为29.49°C、29.11°C和28.80°C。正式数据来自NASA/POWER Source Native Resolution Daily Data。",
  "evidence": [
    {
      "metric": "month.2024-07.avg_temperature_c",
      "label": "2024-07 平均气温",
      "value": 29.49,
      "source": "月度聚合",
      "unit": "°C",
      "plot_scope": "全部地块",
      "period_start": "2024-01-01",
      "period_end": "2024-12-31",
      "analysis_id": 14
    },
    {
      "metric": "month.2024-08.avg_temperature_c",
      "label": "2024-08 平均气温",
      "value": 29.11,
      "source": "月度聚合",
      "unit": "°C",
      "plot_scope": "全部地块",
      "period_start": "2024-01-01",
      "period_end": "2024-12-31",
      "analysis_id": 14
    },
    {
      "metric": "month.2024-09.avg_temperature_c",
      "label": "2024-09 平均气温",
      "value": 28.8,
      "source": "月度聚合",
      "unit": "°C",
      "plot_scope": "全部地块",
      "period_start": "2024-01-01",
      "period_end": "2024-12-31",
      "analysis_id": 14
    },
    {
      "metric": "data.source",
      "label": "正式数据来源",
      "value": "NASA/POWER Source Native Resolution Daily Data",
      "source": "NASA Langley Research Center POWER Project",
      "unit": "",
      "landing_page": "https://power.larc.nasa.gov/",
      "retrieved_at": "2026-08-22T18:03:19+08:00",
      "analysis_id": 14
    }
  ],
  "analysis_id": 14,
  "intent": "temperature",
  "context": {
    "analysis_id": 14,
    "plot_scope": "全部地块",
    "date_start": "2024-01-01",
    "date_end": "2024-12-31",
    "record_count": 1098,
    "agent_runtime": {
      "mode": "local_model",
      "provider": "ollama",
      "model": "qwen2.5:3b",
      "ready": true,
      "fallback_used": false,
      "fallback_reason": null,
      "latency_ms": 1620
    }
  },
  "suggestions": ["哪个季节降水最多？", "是否存在高温或干旱风险？", "这批数据质量可靠吗？"],
  "disclaimer": "Agent 的结论以当前筛选数据为依据，农业决策还需结合土壤、作物品种与现场巡查。",
  "agent_runtime": {
    "mode": "local_model",
    "provider": "ollama",
    "model": "qwen2.5:3b",
    "ready": true,
    "fallback_used": false,
    "fallback_reason": null,
    "latency_ms": 1620
  },
  "query_id": 36,
  "queried_at": "2026-08-22T19:03:37+08:00"
}
```

后端问题路由覆盖 `precipitation/temperature/radiation/humidity/quality/risk/source/overview` 等事实组，普通对话还识别 `greeting/capability/thanks/farewell`。该路由用于检索上下文和证据，不预先固定模型正文。显式指定不存在的 `analysis_id` 立即返回 `404`，不会再尝试按筛选条件生成分析，也不会回退最新分析；筛选无数据或无任何分析结果同样为 `404`，失败分析为 `409`。Ollama 故障本身不返回 5xx，而是 `rule_fallback`。

典型断连回退片段：

```json
{
  "answer": "基于分析 #16（全部地块，2024-01-01 至 2024-12-31，1098 条记录）：夏季降水最集中：累计 1034.96 mm、雨日 92 天。全期最长连续少雨 35 天，规则状态为触发。",
  "evidence": [
    {
      "metric": "wettest_season",
      "value": "夏季",
      "analysis_id": 16
    }
  ],
  "agent_runtime": {
    "mode": "rule_fallback",
    "provider": "ollama",
    "model": "qwen2.5:3b",
    "ready": false,
    "fallback_used": true,
    "fallback_reason": "unreachable",
    "latency_ms": 2
  }
}
```

### `GET /api/agent/history`

分页、按 ID 降序。项目保存问题、回答、上下文和证据；`agent_runtime` 会复制到 `context.agent_runtime` 持久化，便于区分本地模型回答与规则回退。历史不保存即时响应的 `intent/suggestions/disclaimer`：

```json
{
  "items": [
    {
      "query_id": 44,
      "created_at": "2026-08-22T19:10:31+08:00",
      "analysis_id": 16,
      "question": "请你自己比较12个月，告诉我最热的三个月，并说明正式数据来自哪里。",
      "answer": "最热的三个月是7月、8月和9月，平均气温分别为29.49°C、29.11°C和28.80°C。正式数据来自NASA/POWER Source Native Resolution Daily Data。",
      "context": {
        "analysis_id": 16,
        "plot_scope": "全部地块",
        "date_start": "2024-01-01",
        "date_end": "2024-12-31",
        "record_count": 1098,
        "agent_runtime": {
          "mode": "local_model",
          "provider": "ollama",
          "model": "qwen2.5:3b",
          "ready": true,
          "fallback_used": false,
          "fallback_reason": null,
          "latency_ms": 4824
        }
      },
      "evidence": [
        {"metric": "month.2024-07.avg_temperature_c", "value": 29.49, "unit": "°C"},
        {"metric": "month.2024-08.avg_temperature_c", "value": 29.11, "unit": "°C"},
        {"metric": "month.2024-09.avg_temperature_c", "value": 28.8, "unit": "°C"},
        {"metric": "data.source", "value": "NASA/POWER Source Native Resolution Daily Data", "unit": ""}
      ]
    }
  ],
  "total": 1,
  "limit": 20,
  "offset": 0
}
```

## 7. 导出与审计

三个导出接口均接受 `analysis_id` 和通用筛选，优先级为：

1. 有 `analysis_id`：使用该任务，其他筛选被忽略；
2. 无 ID 但有筛选：内存生成 `analysis_id="filtered"` 预览，不写分析历史；
3. 无 ID/筛选：使用最新成功分析。

不存在/无数据为 `404`，失败分析为 `409`。成功导出才写审计。

### `GET /api/export/seasonal.csv`

```text
Content-Type: text/csv; charset=utf-8
Content-Disposition: attachment; filename=seasonal-analysis-2.csv
X-Audit-Id: 1
```

筛选预览文件名为 `seasonal-analysis-filtered.csv`。固定表头：

```text
analysis_id,plot_id,season_year,season,period_start,period_end,expected_days,valid_days,coverage_pct,temperature_avg_c,temperature_max_c,temperature_min_c,temperature_trend_c_30d,precipitation_total_mm,rainy_days,longest_dry_spell_days,solar_radiation_avg_wm2,low_light_days,humidity_avg_pct,outlier_count,risk_count
```

正常结果优先导出 `seasonal_by_plot`。API 内存生成正文，不直接写 `exports/`；`scripts/verify_release.py` 会把响应落盘。

### `GET /api/export/report.md`

```text
Content-Type: text/markdown; charset=utf-8
Content-Disposition: attachment; filename=agri-weather-report-2.md
X-Audit-Id: 2
```

筛选预览文件名为 `agri-weather-report-filtered.md`。报告包含元数据、摘要、核心指标、四季对比、结论、7 条风险摘要、质量与方法。

### `GET /api/export/report.xlsx`

返回 OOXML 工作簿，媒体类型为 `application/vnd.openxmlformats-officedocument.spreadsheetml.sheet`。工作簿固定包含：

- `执行摘要`：分析编号、时间范围、地块数、记录数、核心指标与摘要；
- `季节统计`：与 CSV 相同的可筛选明细，并包含季节平均气温原生柱状图；
- `风险清单`：风险名称、等级、状态、证据与建议；
- `方法与口径`：季节、降水、趋势、异常与决策边界。

响应包含 `Content-Disposition` 下载文件名及 `X-Audit-Id`，文件在内存生成，不修改运行数据。

### `GET /api/audit/events`

只有成功的 CSV、Markdown 或 Excel 导出写事件：

```json
{
  "items": [
    {
      "audit_id": 2,
      "created_at": "2026-08-22T15:17:00+08:00",
      "event_type": "export_markdown_report",
      "reference_id": "2",
      "details": {"filters": {"year": 2024, "season": "夏季"}}
    }
  ],
  "total": 2,
  "limit": 50,
  "offset": 0
}
```

事件类型为 `export_seasonal_csv`、`export_markdown_report`、`export_excel_report`；`reference_id` 是字符串，预览为 `filtered`。

## 8. 最小闭环验收

```powershell
$base = "http://127.0.0.1:8003"

$health = Invoke-RestMethod "$base/api/health"
$source = Invoke-RestMethod "$base/api/data/source"
if ($health.status -ne "ok" -or $health.version -ne "1.2.0" -or
    $health.records -ne 1098 -or $source.source.provider -notlike "NASA*") {
  throw "health/version/正式来源契约未满足"
}
Invoke-RestMethod "$base/api/overview?year=2024"
Invoke-RestMethod "$base/api/observations?year=2024&limit=3&offset=0"

$run = Invoke-RestMethod -Method Post -Uri "$base/api/analysis/run" `
  -ContentType "application/json; charset=utf-8" `
  -Body (@{year=2024; season="summer"} | ConvertTo-Json)
Invoke-RestMethod "$base/api/analysis/latest"
Invoke-RestMethod "$base/api/analysis/history?limit=5&offset=0"
Invoke-RestMethod "$base/api/analysis/$($run.analysis_id)"

$agent = Invoke-RestMethod -Method Post -Uri "$base/api/agent/query" `
  -ContentType "application/json; charset=utf-8" `
  -Body (@{question="请比较夏季三个月的温度"; analysis_id=$run.analysis_id} | ConvertTo-Json)
if ($agent.agent_runtime.mode -notin @("local_model", "rule_fallback") -or
    $agent.context.agent_runtime.mode -ne $agent.agent_runtime.mode -or
    $agent.evidence.Count -lt 1) {
  throw "Agent runtime/evidence 契约未满足"
}
Invoke-RestMethod "$base/api/agent/history?limit=5&offset=0"

Invoke-WebRequest "$base/api/export/seasonal.csv?analysis_id=$($run.analysis_id)" `
  -OutFile ".\exports\seasonal_api_check.csv"
Invoke-WebRequest "$base/api/export/report.md?analysis_id=$($run.analysis_id)" `
  -OutFile ".\exports\report_api_check.md"
Invoke-WebRequest "$base/api/export/report.xlsx?analysis_id=$($run.analysis_id)" `
  -OutFile ".\exports\report_api_check.xlsx"
Import-Csv ".\exports\seasonal_api_check.csv" | Select-Object -First 3
Get-Content ".\exports\report_api_check.md" -Encoding UTF8 -TotalCount 20
Invoke-RestMethod "$base/api/audit/events?limit=20&offset=0"
```

项目内置隔离数据库闭环：

```powershell
python .\scripts\verify_release.py
```

它执行 CSV 导入 → 质量校验 → 分析 → Agent → CSV/Markdown/Excel 导出 → 审计回读，并写出 `exports/seasonal_statistics.csv`、`exports/analysis_report.md`、`exports/analysis_report.xlsx` 和 `docs/evidence/api_verification.json`。
