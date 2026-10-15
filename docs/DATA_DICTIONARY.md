# 农田气象时序分析 Agent：数据字典

| 属性 | 值 |
|---|---|
| 版本 | v1.2.0 |
| 日期 | 2026-08-23 |
| 默认粒度 | 分析对象（地块/网格）× 日 |
| 时间口径 | 服务业务时间为 Asia/Shanghai；NASA POWER 日值为来源声明的 LST |
| 字符编码 | UTF-8；导出 CSV 可使用 UTF-8 BOM 兼容常见表格软件 |

## 1. 原始观测 CSV

### 1.1 字段定义

| 字段 | 类型 | 必填 | 单位/格式 | 示例 | 有效性与说明 |
|---|---|---:|---|---|---|
| `timestamp` | string/date | 是 | `YYYY-MM-DD`，也接受合法 ISO 8601 | `2026-07-18` | 与 `plot_id` 构成业务唯一键；含时间的输入按本地日期归一化 |
| `plot_id` | string | 是 | 1–64 字符 | `NASA-GM 光明网格` | 分析对象标识；默认正式数据使用光明/龙岗/大鹏三个 POWER 网格，用户导入也可使用自有地块编号 |
| `temperature_c` | number | 是 | °C | `28.4` | 日平均气温；工程合理范围 -50 至 60 |
| `precipitation_mm` | number | 是 | mm/day | `12.6` | 当日累计降水；工程合理范围 0 至 500；空值不等于 0 |
| `solar_radiation_wm2` | number | 是 | W/m² | `236.8` | 全天平均短波太阳辐射通量；工程合理范围 0 至 1400 |
| `humidity_pct` | number | 否 | % | `76.5` | 日平均相对湿度；有效范围 0 至 100 |
| `quality_flag` | enum/string | 否 | 见 1.2 | `observed` | 未提供时默认为 `observed`；导入规则可升级为疑似/拒绝状态 |

CSV 表头严格使用英文蛇形命名。未知列可随批次元数据记录，但不参与首版统计。

### 1.2 `quality_flag` 枚举

| 值 | 含义 | 默认参与聚合 | 备注 |
|---|---|---:|---|
| `valid` | 已通过平台入库必填、类型和工程范围检查 | 是 | NASA 获取脚本写入此值；不等同于上游质量认证或测量准确性评级 |
| `observed` | 原始观测且通过检查 | 是 | 默认值 |
| `corrected` | 有可追踪修正记录 | 是 | 必须保留修正说明 |
| `imputed` | 通过明确方法插补 | 是，但需披露 | 首版不自动生成该状态 |
| `suspect` | 统计或语义上可疑 | 否 | 可由用户复核后修正 |
| `rejected` | 结构或硬边界失败 | 否 | 不写入有效观测集或存入拒绝明细 |

当文件自带 `quality_flag` 与系统校验冲突时，采用更严格状态，且记录系统原因码。

### 1.3 最小 CSV 示例

```csv
timestamp,plot_id,temperature_c,precipitation_mm,solar_radiation_wm2,humidity_pct,quality_flag
2026-03-01,PLOT-A,15.8,0.0,183.2,63.4,observed
2026-03-02,PLOT-A,16.4,2.1,166.5,71.0,observed
2026-03-01,PLOT-B,14.9,0.0,176.8,66.2,observed
```

### 1.4 默认正式数据及来源映射

| 属性 | 当前值 |
|---|---|
| provider | `NASA Langley Research Center POWER Project` |
| dataset | `NASA/POWER Source Native Resolution Daily Data` |
| API | `POWER Daily API v2.9.7`，community=`AG` |
| 时间范围 | 2024-01-01 至 2024-12-31，共 366 日 |
| 网格 | `NASA-GM 光明网格` `(22.7489,113.9359)`；`NASA-LG 龙岗网格` `(22.7215,114.2469)`；`NASA-DP 大鹏网格` `(22.5963,114.4797)` |
| 记录数 | 3 × 366 = **1,098** |
| 归一化文件 | `data/nasa_power_shenzhen_2024.csv` |
| provenance | `data/nasa_power_shenzhen_2024.provenance.json` |
| CSV SHA-256 | `2d2c706261acdfc6ce49870813e60e1ada5305161ec64f8c208c743d0861b577` |

| POWER 参数 | 来源单位 | 平台字段 | 转换 |
|---|---|---|---|
| `T2M` | °C | `temperature_c` | 原值 |
| `PRECTOTCORR` | mm/day | `precipitation_mm` | 原值 |
| `ALLSKY_SFC_SW_DWN` | MJ/m²/day | `solar_radiation_wm2` | `value × 1,000,000 ÷ 86,400`，得到日均 W/m² |
| `RH2M` | % | `humidity_pct` | 原值 |

三个 `data/raw/nasa_power_2024/*.json` 保存逐网格完整 API JSON 内容；provenance 保存查询 URL、请求/响应坐标、参数/单位、获取时间、源文件/归一化文件哈希。现有 2026-08-22 文件是响应经解析后重排版的 `normalized_json_snapshot`，哈希覆盖本地快照而非原 HTTP 响应字节；2026-08-23 重新请求三个 URL 后，4 个参数 × 366 日 × 3 点共 4,392 个值全部一致。更新后的获取脚本使用 `http_response_body` 模式，后续直接保存 HTTP 响应体。它们是指定坐标的 NASA POWER 网格化卫星/再分析产品，不应解释为地面气象站或田间传感器实测；三个标签是代表请求点而非行政区面平均，龙岗与大鹏的四项 2024 序列完全相同，符合来源原生空间分辨率效应，不应当作两套独立站点观测。

## 2. 派生时间字段

派生字段不得要求用户在 CSV 中提供，由服务层统一计算。

| 字段 | 类型 | 规则 | 示例 |
|---|---|---|---|
| `observation_date` | date | `timestamp` 归一化后的本地日期 | `2026-01-15` |
| `year` | integer | 日历年 | `2026` |
| `month` | integer | 1–12 | `1` |
| `day_of_year` | integer | 1–365/366 | `15` |
| `season` | enum | `spring`/`summer`/`autumn`/`winter` | `winter` |
| `season_label` | string | 春季/夏季/秋季/冬季 | `冬季` |
| `season_year` | integer | 春夏秋取日历年；12 月加 1，1–2 月取日历年 | `2026` |
| `radiation_mj_m2_day` | number | `solar_radiation_wm2 × 0.0864` | `15.28` |

### 2.1 季节映射

```text
3 <= month <= 5   → spring
6 <= month <= 8   → summer
9 <= month <= 11  → autumn
month in {12,1,2} → winter
season_year = year + 1 when month == 12 else year
```

## 3. 导入批次与数据来源

### 3.1 导入批次数据

| 字段 | 类型 | 说明 |
|---|---|---|
| `import_id` | UUID/string | 导入批次唯一标识 |
| `source_name` | string | 原始文件名的安全化展示值 |
| `source_sha256` | string | 文件内容哈希，用于识别重复导入 |
| `created_at` | datetime | 导入开始时间，ISO 8601 |
| `completed_at` | datetime/null | 导入完成时间 |
| `status` | enum | `pending`、`running`、`succeeded`、`failed`、`partial` |
| `total_rows` | integer | 不含表头的总行数 |
| `accepted_rows` | integer | 进入有效观测集的行数 |
| `rejected_rows` | integer | 被拒绝的行数 |
| `duplicate_rows` | integer | 完全重复和冲突重复之和 |
| `warning_rows` | integer | 有警告但未拒绝的行数 |
| `error_summary` | object/text | 原因码到数量的映射 |

### 3.2 `dataset_sources` 活动来源

| 字段 | 类型 | 说明 |
|---|---|---|
| `source_id` | integer | SQLite 自增来源 ID |
| `created_at` | datetime | provenance 的获取/创建时间 |
| `source_key` | string | 唯一来源键，例如 `nasa-power-shenzhen-daily-20240101-20241231` |
| `provider` | string | 数据提供方 |
| `dataset_name` | string | 数据集正式名称 |
| `landing_page` | URL | 来源主页 |
| `query_urls` | array<URL> | 逐网格可复现 API 请求 |
| `active` | boolean | 当前数据集活动来源；保存新来源时旧行置为 false |
| `provenance` | object | API 版本、参数、坐标、期间、原始文件、转换、哈希等完整快照 |

`GET /api/data/source` 返回上述对象；`GET /api/health` 返回 `source_id/provider/dataset_name/retrieved_at` 摘要；`GET /api/overview` 在 `dataset.source` 中返回活动来源。

用户 CSV 实际写入新记录后，活动来源同步切换：整库替换使用 `source_type=user_csv`，追加到既有数据使用 `source_type=mixed_dataset`。无新增记录的导入不改变活动来源。该兼容策略复用现有 `dataset_sources` 表，不要求数据库迁移。

概览中的 `quality.scope=ingestion_quality`。其分数只依据缺测、工程范围、质量标记和重复计算，是“入库质量分”，不评价 NASA POWER 或用户文件的测量准确性、空间代表性和现场适用性。

## 4. 质量原因码

| 原因码 | 层级 | 含义 | 默认处理 |
|---|---|---|---|
| `MISSING_REQUIRED_COLUMN` | 文件 | 缺必填列 | 整批拒绝 |
| `EMPTY_FILE` | 文件 | 文件无数据行 | 整批拒绝 |
| `FILE_TOO_LARGE` | 文件 | 超过默认 20 MB | 整批拒绝 |
| `INVALID_ENCODING` | 文件 | 不能按支持编码读取 | 整批拒绝 |
| `INVALID_DATE` | 行 | 日期不可解析 | 拒绝该行 |
| `MISSING_REQUIRED_VALUE` | 行 | 必填值为空 | 拒绝该行 |
| `INVALID_NUMBER` | 行 | 数值列不可解析 | 拒绝该行 |
| `OUT_OF_RANGE` | 行/字段 | 超过合理范围 | 排除对应记录或字段，并计数 |
| `DUPLICATE_EXACT` | 时序 | 唯一键与数值完全相同 | 保留第一条，后续去重 |
| `DUPLICATE_CONFLICT` | 时序 | 唯一键相同但关键数值不同 | 不自动覆盖，标为冲突 |
| `DATE_GAP` | 时序 | 预期日序列存在间断 | 警告，影响覆盖率和连续事件 |
| `IQR_OUTLIER` | 统计 | 超出同地块同季节 IQR 界限 | 标为疑似，不自动删除原值 |
| `INSUFFICIENT_SAMPLE` | 统计 | 不满足趋势或异常最小样本 | 不计算对应结论 |

质量明细至少包含：`import_id`、CSV 原始行号、`plot_id`（可解析时）、`timestamp`（可解析时）、字段、原始值、原因码和可读说明。

## 5. 分析任务

| 字段 | 类型 | 说明 |
|---|---|---|
| `analysis_id` | UUID/string | 一次分析唯一标识 |
| `status` | enum | `pending`、`running`、`succeeded`、`failed` |
| `created_at` | datetime | 创建时间 |
| `started_at` | datetime/null | 开始时间 |
| `completed_at` | datetime/null | 完成时间 |
| `plot_ids` | array | 本次地块范围；空数组代表全部 |
| `start_date` | date | 分析开始日期 |
| `end_date` | date | 分析结束日期 |
| `season` | enum/null | 可选季节筛选 |
| `threshold_version` | string | 风险规则版本，例如 `v1-default` |
| `source_record_count` | integer | 进入分析的有效观测数 |
| `excluded_record_count` | integer | 因质量被排除的记录数 |
| `duration_ms` | integer | 运行耗时 |
| `error_code` | string/null | 失败原因码 |
| `error_message` | string/null | 面向用户的安全错误摘要 |

## 6. 季节聚合结果

唯一组合建议为 `(analysis_id, plot_id, season_year, season)`。

| 字段 | 类型 | 单位 | 定义 |
|---|---|---|---|
| `analysis_id` | string | — | 来源分析 |
| `plot_id` | string | — | 地块 |
| `season_year` | integer | 年 | 季节所属年 |
| `season` | enum | — | `spring`/`summer`/`autumn`/`winter` |
| `period_start` | date | — | 实际纳入数据的最早日期 |
| `period_end` | date | — | 实际纳入数据的最晚日期 |
| `expected_days` | integer | day | 按筛选范围应有天数 |
| `valid_days` | integer | day | 至少核心字段均有效的天数 |
| `coverage_pct` | number | % | `valid_days / expected_days × 100` |
| `temperature_avg_c` | number/null | °C | 有效日平均温度均值 |
| `temperature_max_c` | number/null | °C | 有效日平均温度最大值 |
| `temperature_min_c` | number/null | °C | 有效日平均温度最小值 |
| `temperature_trend_c_30d` | number/null | °C/30d | 线性回归斜率换算 |
| `precipitation_total_mm` | number/null | mm | 有效日降水之和 |
| `rainy_days` | integer | day | 降水 ≥0.1 mm 的天数 |
| `longest_dry_spell_days` | integer | day | 连续降水 <1 mm 的最长天数 |
| `solar_radiation_avg_wm2` | number/null | W/m² | 有效日平均辐射均值 |
| `low_light_days` | integer | day | 辐射 <100 W/m² 的天数 |
| `humidity_avg_pct` | number/null | % | 有效日相对湿度均值 |
| `outlier_count` | integer | count | 统计异常记录数 |
| `risk_count` | integer | count | 关联风险事件数 |

数值存储保留足够精度，API 默认可返回 2 位小数；CSV 和页面的舍入必须一致。无可计算数据时使用 `null`，禁止用 0 伪装缺失。

## 7. 风险事件

| 字段 | 类型 | 说明 |
|---|---|---|
| `risk_id` | string | 风险事件唯一标识 |
| `analysis_id` | string | 来源分析 |
| `plot_id` | string | 地块 |
| `risk_code` | enum | 见 7.1 |
| `severity` | enum | `low`、`medium`、`high` |
| `start_date` | date | 起始日期 |
| `end_date` | date | 结束日期 |
| `duration_days` | integer | 持续日数 |
| `observed_value` | number/null | 命中核心值或期间统计值 |
| `observed_unit` | string | 例如 `°C`、`mm/day`、`W/m²` |
| `threshold_text` | string | 可读阈值，必须随结果保存 |
| `evidence` | object | 峰值、均值、累计值、日期列表等结构化证据 |
| `message` | string | 面向用户的环境风险提示 |

### 7.1 风险代码

`HEAT_DAY`、`HEAT_STREAK`、`COLD_DAY`、`HEAVY_RAIN`、`DRY_SPELL`、`LOW_LIGHT_STREAK`、`TEMP_SWING`。

## 8. Agent 问答记录

| 字段 | 类型 | 说明 |
|---|---|---|
| `query_id` | integer | SQLite 自增问答标识 |
| `analysis_id` | integer | 取证来源；问答前必须已有或同步生成 completed 分析 |
| `question` | string | 用户原问题，去除首尾空白，限制长度 |
| `answer` | string | 最终中文答复；本地模型成功时为上下文动态生成，失败时为规则正文 |
| `context` | object | `analysis_id/plot_scope/date_start/date_end/record_count` 及 `agent_runtime` |
| `evidence` | array<object> | 服务端拥有的指标键、标签、值、单位、来源、地块、期间和分析 ID，最多按回答选择 4 项（规则回退可用规则证据集） |
| `created_at` | datetime | 回答时间 |

即时响应另含 `intent/suggestions/disclaimer`，历史表不单独保存这些字段。模型内部输入使用问题相关 `analysis_context` 和 `evidence_catalog`；模型按 JSON Schema 输出 `answer/evidence_ids`，服务端再映射证据并校验主题、月份、风险语义与指标数字。指标数字只能来自服务端证据 value；年份、月份、日期仅作为时间标识处理。

`context.agent_runtime` 字段：

| 字段 | 类型 | 说明 |
|---|---|---|
| `mode` | enum | `local_model` 或 `rule_fallback` |
| `provider` | string | 标准运行为 `ollama` |
| `model` | string | 默认 `qwen2.5:3b` |
| `ready` | boolean | 本次探测/调用是否就绪 |
| `fallback_used` | boolean | 是否使用规则回退正文 |
| `fallback_reason` | string/null | 调用或校验失败原因；包括普通对话重试后仍超过 100 字的 `too_verbose`，以及风险陈述与 `risk.*` 证据矛盾的 `inconsistent_risk_claim` |
| `latency_ms` | integer | 本次探测/生成耗时，非负 |

## 9. 季节统计 CSV 导出表头

```text
analysis_id,plot_id,season_year,season,period_start,period_end,
expected_days,valid_days,coverage_pct,
temperature_avg_c,temperature_max_c,temperature_min_c,temperature_trend_c_30d,
precipitation_total_mm,rainy_days,longest_dry_spell_days,
solar_radiation_avg_wm2,low_light_days,humidity_avg_pct,
outlier_count,risk_count
```

导出规则：

1. 文件首行必须为字段名，使用逗号分隔并正确转义。
2. 中文显示值可附加，但机器字段保持稳定英文名。
3. `null` 输出为空字段，不输出字符串 `null` 或数值 0。
4. 行排序为 `plot_id, season_year, season_order`，季节顺序春、夏、秋、冬。
5. 文件元数据（筛选、阈值、生成时间）写入配套 Markdown 报告，不在 CSV 前增加非表格行。
