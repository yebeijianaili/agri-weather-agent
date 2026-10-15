from __future__ import annotations

import math
import re
import statistics
from collections import defaultdict
from datetime import date, datetime
from typing import Any, Iterable

from domain.models import METRIC_LABELS, METRIC_UNITS, SEASONS, season_for_month
from repositories.database import WeatherRepository


VALID_QUALITY_FLAGS = {"valid", "observed", "corrected", "imputed"}

_DOMAIN_QUESTION_WORDS = (
    "降水", "下雨", "干旱", "雨季", "温度", "高温", "低温", "冻", "热",
    "光照", "辐射", "太阳", "质量", "缺失", "异常", "可靠", "风险", "建议",
    "管理", "农事", "地块", "季节", "数据", "记录", "趋势", "分析",
)


def _normalized_message(value: str) -> str:
    """Normalize short conversational messages without changing domain queries."""
    punctuation = "，。！？!?、,.；;：:~～…—-（）()【】[]\"'"
    return "".join(character for character in value.casefold().strip() if not character.isspace() and character not in punctuation)


def _conversation_reply(question: str) -> tuple[str, str, list[str]] | None:
    """Return a human conversation reply when the message is not a data question."""
    text = _normalized_message(question)
    greetings = {"你好", "您好", "你好呀", "您好呀", "嗨", "哈喽", "hello", "hi", "在吗", "早上好", "下午好", "晚上好"}
    capabilities = {"你是谁", "你叫什么", "能做什么", "你能做什么", "可以做什么", "怎么问", "怎么用", "帮助", "帮帮我", "介绍一下", "介绍下你", "有什么功能"}
    thanks = {"谢谢", "谢谢你", "多谢", "感谢", "感谢你", "辛苦了", "麻烦你了", "太好了"}
    farewells = {"再见", "拜拜", "回头见", "下次见", "bye", "goodbye"}
    suggestions = ["哪个季节降水最多？", "目前有哪些气象风险？", "这批数据质量可靠吗？"]

    if text in capabilities or any(phrase in text for phrase in ("能回答哪些问题", "能问哪些问题", "可以问什么", "能帮我看什么")):
        return (
            "capability",
            "我是你的农田气象分析搭档。你可以直接问降水、温度、光照、风险或数据质量，我会结合当前数据给你结论和依据。",
            suggestions,
        )
    if any(word in question for word in _DOMAIN_QUESTION_WORDS):
        return None
    if text in greetings:
        return (
            "greeting",
            "你好呀，我在。你可以把我当作农田气象分析搭档：想先看降水、温度、光照、风险，还是数据质量？",
            suggestions,
        )
    if text in thanks:
        return (
            "thanks",
            "不客气，很高兴帮上忙。还想继续看哪个地块、时段或气象指标？",
            ["继续看看气象风险", "对比不同季节", "检查数据质量"],
        )
    if text in farewells:
        return (
            "farewell",
            "好呀，先到这里。下次打开项目后继续问我就行。",
            ["查看数据概览", "分析气象风险", "检查数据质量"],
        )
    return None


def _question_intent(question: str) -> str:
    conversation = _conversation_reply(question)
    if conversation is not None:
        return conversation[0]
    patterns = (
        ("precipitation", ("降水", "下雨", "干旱", "雨季")),
        ("temperature", ("温度", "高温", "低温", "冻", "热")),
        ("radiation", ("光照", "辐射", "太阳")),
        ("quality", ("质量", "缺失", "异常", "可靠")),
        ("risk", ("风险", "建议", "管理", "农事")),
    )
    return next((intent for intent, words in patterns if any(word in question for word in words)), "analysis")


def _requested_metric_groups(question: str) -> set[str]:
    groups: set[str] = set()
    patterns = {
        "temperature": ("温度", "气温", "高温", "低温", "冷", "热", "冻"),
        "precipitation": ("降水", "下雨", "雨季", "干旱", "干日", "积涝"),
        "radiation": ("光照", "辐射", "太阳"),
        "humidity": ("湿度", "潮湿"),
        "quality": ("质量", "缺失", "异常", "可靠", "完整"),
        "risk": ("风险", "建议", "管理", "农事", "预警"),
        "source": ("来源", "哪来", "正式数据", "官方", "网上", "真实数据", "NASA"),
    }
    for group, words in patterns.items():
        if any(word.casefold() in question.casefold() for word in words):
            groups.add(group)
    return groups or {"overview"}


def _requested_month_numbers(question: str) -> set[int]:
    range_match = re.search(r"(1[0-2]|0?[1-9])\s*月\s*(?:到|至|—|–|-)\s*(1[0-2]|0?[1-9])\s*月", question)
    if range_match:
        start, end = int(range_match.group(1)), int(range_match.group(2))
        if start <= end:
            return set(range(start, end + 1))
        return set(range(start, 13)) | set(range(1, end + 1))
    return {int(value) for value in re.findall(r"(1[0-2]|0?[1-9])\s*月", question)}


def _requested_period_summary(result: dict[str, Any], months: set[int]) -> dict[str, Any] | None:
    if not months:
        return None
    ordered = sorted(months)
    canonical = {
        frozenset({3, 4, 5}): "春季",
        frozenset({6, 7, 8}): "夏季",
        frozenset({9, 10, 11}): "秋季",
        frozenset({12, 1, 2}): "冬季",
    }
    season_name = canonical.get(frozenset(months))
    if season_name:
        season = next((item for item in result.get("seasonal", []) if item.get("season") == season_name), None)
        if season:
            return {
                "label": f"{','.join(str(month) for month in ordered)}月（{season_name}）",
                "months": ordered,
                "method": "匹配完整气象季节，直接使用季节聚合",
                **{
                    key: season.get(key)
                    for key in (
                        "record_count", "day_count", "avg_temperature_c", "min_temperature_c", "max_temperature_c",
                        "total_precipitation_mm", "rainy_days", "longest_dry_spell_days",
                        "avg_solar_radiation_wm2", "low_light_days", "avg_humidity_pct",
                    )
                },
            }
    selected = [item for item in result.get("monthly", []) if int(str(item.get("month", "00"))[-2:]) in months]
    if not selected:
        return None
    return {
        "label": f"{','.join(str(month) for month in ordered)}月",
        "months": ordered,
        "method": "月度聚合再汇总；降水和雨日求和，其余指标取所选月份均值",
        "avg_temperature_c": _round(_mean(item.get("avg_temperature_c") for item in selected)),
        "total_precipitation_mm": _round(sum(float(item.get("total_precipitation_mm") or 0) for item in selected)),
        "rainy_days": sum(int(item.get("rainy_days") or 0) for item in selected),
        "avg_solar_radiation_wm2": _round(_mean(item.get("avg_solar_radiation_wm2") for item in selected)),
        "avg_humidity_pct": _round(_mean(item.get("avg_humidity_pct") for item in selected)),
    }


def _compact_agent_context(analysis: dict[str, Any], question: str) -> dict[str, Any]:
    """Retrieve only question-relevant facts so a small local model can reason reliably."""
    result = analysis["result"]
    groups = _requested_metric_groups(question)
    requested_months = _requested_month_numbers(question)
    needs_monthly = bool(requested_months) or "月" in question
    needs_seasonal = any(token in question for token in ("季节", "春季", "夏季", "秋季", "冬季", "春天", "夏天", "秋天", "冬天"))
    field_groups = {
        "temperature": ("avg_temperature_c", "min_temperature_c", "max_temperature_c"),
        "precipitation": ("total_precipitation_mm", "rainy_days", "longest_dry_spell_days"),
        "radiation": ("avg_solar_radiation_wm2", "low_light_days"),
        "humidity": ("avg_humidity_pct",),
    }
    selected_metric_groups = [group for group in field_groups if group in groups]
    if not selected_metric_groups:
        selected_metric_groups = list(field_groups)
    selected_fields = {field for group in selected_metric_groups for field in field_groups[group]}
    seasonal = [
        {
            key: value
            for key, value in item.items()
            if key in {"season", "season_year", "record_count", "day_count"} | selected_fields
        }
        for item in result.get("seasonal", [])
    ] if not (needs_monthly and not needs_seasonal) else []
    all_monthly = result.get("monthly", [])
    monthly = [
        {key: value for key, value in item.items() if key == "month" or key in selected_fields}
        for item in all_monthly
        if not requested_months or int(str(item.get("month", "00"))[-2:]) in requested_months
    ] if needs_monthly else []
    trend_metric_names = {
        "temperature": "temperature_c",
        "precipitation": "precipitation_mm",
        "radiation": "solar_radiation_wm2",
        "humidity": "humidity_pct",
    }
    selected_trends = {
        metric: result.get("trends", {}).get(metric)
        for group, metric in trend_metric_names.items()
        if group in selected_metric_groups and metric in result.get("trends", {})
    }
    risk_codes = {
        "temperature": {"HEAT_DAY", "HEAT_STREAK", "COLD_DAY", "TEMP_SWING"},
        "precipitation": {"HEAVY_RAIN", "DRY_SPELL"},
        "radiation": {"LOW_LIGHT_STREAK"},
    }
    selected_codes: set[str] = set()
    if "risk" in groups:
        selected_codes = set().union(*(risk_codes.get(group, set()) for group in selected_metric_groups))
        if not selected_codes:
            selected_codes = {str(item.get("code")) for item in result.get("risks", [])}
    context: dict[str, Any] = {
        "requested_groups": sorted(groups),
        "analysis": {
            "analysis_id": analysis["analysis_id"],
            "filters": analysis.get("filters", {}),
            "summary": analysis.get("summary", ""),
        },
        "overview": result.get("overview", {}),
        "seasonal": seasonal,
        "monthly": monthly,
        "trends": selected_trends,
        "risks": [
            {
                key: item.get(key)
                for key in ("code", "name", "severity", "status", "metric", "threshold", "evidence", "recommendation")
            }
            for item in result.get("risks", [])
            if item.get("code") in selected_codes
        ],
        "risk_events": [
            {
                key: item.get(key)
                for key in ("code", "name", "severity", "plot_id", "start_date", "end_date", "duration_days", "evidence", "threshold")
            }
            for item in result.get("risk_events", [])
            if item.get("code") in selected_codes
        ][:12],
        "data_scope": result.get("data_scope", {}),
    }
    rankings: dict[str, Any] = {}
    if needs_monthly and "temperature" in selected_metric_groups:
        rankings["hottest_months"] = sorted(
            ({"month": item.get("month"), "avg_temperature_c": item.get("avg_temperature_c")} for item in all_monthly),
            key=lambda item: item["avg_temperature_c"] if item["avg_temperature_c"] is not None else -999,
            reverse=True,
        )[:3]
    if needs_monthly and "precipitation" in selected_metric_groups:
        rankings["wettest_months"] = sorted(
            ({"month": item.get("month"), "total_precipitation_mm": item.get("total_precipitation_mm")} for item in all_monthly),
            key=lambda item: item["total_precipitation_mm"] if item["total_precipitation_mm"] is not None else -1,
            reverse=True,
        )[:3]
    if needs_monthly and "radiation" in selected_metric_groups:
        rankings["brightest_months"] = sorted(
            ({"month": item.get("month"), "avg_solar_radiation_wm2": item.get("avg_solar_radiation_wm2")} for item in all_monthly),
            key=lambda item: item["avg_solar_radiation_wm2"] if item["avg_solar_radiation_wm2"] is not None else -1,
            reverse=True,
        )[:3]
    if rankings:
        context["rankings"] = rankings
    requested_period = _requested_period_summary(result, requested_months)
    if requested_period:
        context["requested_period"] = requested_period
    if "quality" in groups or "overview" in groups:
        context["quality"] = {
            key: result.get("quality", {}).get(key)
            for key in ("score", "grade", "missing_values", "suspect_records", "out_of_range_values", "duplicate_records", "total_records")
        }
        context["anomalies"] = {
            "method": result.get("anomalies", {}).get("method"),
            "total": result.get("anomalies", {}).get("total"),
            "counts": result.get("anomalies", {}).get("counts", {}),
            "items": result.get("anomalies", {}).get("items", [])[:6],
        }
    return context


def _agent_evidence_catalog(analysis: dict[str, Any], context: dict[str, Any]) -> dict[str, dict[str, Any]]:
    result = analysis["result"]
    overview = result["overview"]
    plot_scope = context["plot_scope"]
    common = {
        "plot_scope": plot_scope,
        "period_start": overview["date_start"],
        "period_end": overview["date_end"],
        "analysis_id": analysis["analysis_id"],
    }
    catalog: dict[str, dict[str, Any]] = {}

    def add(key: str, label: str, value: Any, unit: str, source: str) -> None:
        catalog[key] = {
            "metric": key,
            "label": label,
            "value": value,
            "unit": unit,
            "source": source,
            **common,
        }

    overview_fields = {
        "record_count": ("分析记录数", "record"),
        "plot_count": ("地块数量", "plot"),
        "day_count": ("覆盖天数", "day"),
        "avg_temperature_c": ("平均气温", "°C"),
        "min_temperature_c": ("最低气温", "°C"),
        "max_temperature_c": ("最高气温", "°C"),
        "total_precipitation_mm": ("累计区域降水", "mm"),
        "rainy_days": ("雨日数量", "day"),
        "avg_solar_radiation_wm2": ("平均太阳辐射", "W/m²"),
        "avg_humidity_pct": ("平均相对湿度", "%"),
        "longest_dry_spell_days": ("最长连续少雨", "day"),
    }
    for field, (label, unit) in overview_fields.items():
        add(f"overview.{field}", label, overview.get(field), unit, "当前分析概览")

    seasonal_fields = {
        "avg_temperature_c": ("平均气温", "°C"),
        "total_precipitation_mm": ("累计降水", "mm"),
        "rainy_days": ("雨日", "day"),
        "longest_dry_spell_days": ("最长连续少雨", "day"),
        "avg_solar_radiation_wm2": ("平均太阳辐射", "W/m²"),
        "avg_humidity_pct": ("平均相对湿度", "%"),
    }
    for season in result.get("seasonal", []):
        season_name = str(season.get("season") or "未知季节")
        for field, (label, unit) in seasonal_fields.items():
            add(
                f"season.{season_name}.{field}",
                f"{season_name}{label}",
                season.get(field),
                unit,
                "季节聚合",
            )

    monthly_fields = {
        "avg_temperature_c": ("平均气温", "°C"),
        "total_precipitation_mm": ("累计降水", "mm"),
        "avg_solar_radiation_wm2": ("平均太阳辐射", "W/m²"),
        "avg_humidity_pct": ("平均相对湿度", "%"),
        "rainy_days": ("雨日", "day"),
    }
    for month in result.get("monthly", []):
        month_name = str(month.get("month") or "未知月份")
        for field, (label, unit) in monthly_fields.items():
            add(
                f"month.{month_name}.{field}",
                f"{month_name} {label}",
                month.get(field),
                unit,
                "月度聚合",
            )

    for metric, trend in result.get("trends", {}).items():
        add(
            f"trend.{metric}",
            f"{trend.get('label', metric)}30日趋势",
            trend.get("slope_per_30_days"),
            f"{trend.get('unit', '')}/30day",
            f"线性趋势；方向={trend.get('direction')}；样本日={trend.get('sample_days')}",
        )

    for risk in result.get("risks", []):
        add(
            f"risk.{risk.get('code')}",
            str(risk.get("name") or risk.get("code")),
            risk.get("metric"),
            "event",
            f"{risk.get('threshold')}；状态={risk.get('status')}；{risk.get('evidence')}",
        )

    quality = result.get("quality", {})
    add("quality.score", "入库质量评分", quality.get("score"), "score", "入库校验规则")
    add("quality.missing_values", "缺测单元", quality.get("missing_values"), "cell", "字段完整性检查")
    add("quality.anomalies", "IQR异常记录", result.get("anomalies", {}).get("total"), "event", "1.5×IQR")
    return catalog


def _focus_evidence_catalog(catalog: dict[str, dict[str, Any]], question: str) -> dict[str, dict[str, Any]]:
    groups = _requested_metric_groups(question)
    requested_months = _requested_month_numbers(question)
    needs_monthly = bool(requested_months) or "月" in question
    needs_seasonal = any(token in question for token in ("季节", "春季", "夏季", "秋季", "冬季", "春天", "夏天", "秋天", "冬天"))
    always = {"overview.record_count", "overview.plot_count", "overview.day_count"}
    if groups == {"overview"}:
        return {
            key: item
            for key, item in catalog.items()
            if key in always or key.startswith("overview.") or key.startswith("quality.") or key.startswith("risk.")
        }
    tokens = {
        "temperature": ("temperature",),
        "precipitation": ("precipitation", "rainy", "dry_spell"),
        "radiation": ("solar", "low_light"),
        "humidity": ("humidity",),
        "quality": ("quality.",),
        "risk": ("risk.",),
    }
    selected_tokens = tuple(token for group in groups for token in tokens.get(group, ()))
    focused = {
        key: item
        for key, item in catalog.items()
        if key in always or any(token.casefold() in key.casefold() for token in selected_tokens)
    }
    if "risk" in groups:
        risk_codes = {
            "temperature": {"HEAT_DAY", "HEAT_STREAK", "COLD_DAY", "TEMP_SWING"},
            "precipitation": {"HEAVY_RAIN", "DRY_SPELL"},
            "radiation": {"LOW_LIGHT_STREAK"},
        }
        metric_groups = {group for group in groups if group in risk_codes}
        allowed_risk_codes = set().union(*(risk_codes[group] for group in metric_groups)) if metric_groups else set()
        if allowed_risk_codes:
            focused = {
                key: item
                for key, item in focused.items()
                if not key.startswith("risk.") or key.split(".", 1)[1] in allowed_risk_codes
            }
    if requested_months:
        focused = {
            key: item
            for key, item in focused.items()
            if not key.startswith("month.") or int(key.split(".", 2)[1][-2:]) in requested_months
        }
    if not needs_monthly:
        focused = {key: item for key, item in focused.items() if not key.startswith("month.")}
    if needs_monthly and not needs_seasonal:
        focused = {key: item for key, item in focused.items() if not key.startswith("season.")}
    return focused


def _round(value: float | None, digits: int = 2) -> float | None:
    return round(value, digits) if value is not None and math.isfinite(value) else None


def _mean(values: Iterable[float | None]) -> float | None:
    clean = [float(v) for v in values if v is not None and math.isfinite(float(v))]
    return statistics.fmean(clean) if clean else None


def _quantile(sorted_values: list[float], q: float) -> float:
    if not sorted_values:
        raise ValueError("empty values")
    position = (len(sorted_values) - 1) * q
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return sorted_values[lower]
    weight = position - lower
    return sorted_values[lower] * (1 - weight) + sorted_values[upper] * weight


def _daily(rows: list[dict[str, Any]], subset: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in subset if subset is not None else rows:
        grouped[row["timestamp"]].append(row)
    result: list[dict[str, Any]] = []
    for timestamp in sorted(grouped):
        items = grouped[timestamp]
        result.append(
            {
                "timestamp": timestamp,
                "temperature_c": _mean(i["temperature_c"] for i in items),
                "precipitation_mm": _mean(i["precipitation_mm"] for i in items),
                "solar_radiation_wm2": _mean(i["solar_radiation_wm2"] for i in items),
                "humidity_pct": _mean(i["humidity_pct"] for i in items),
            }
        )
    return result


def _longest_dry_spell(daily_rows: list[dict[str, Any]], threshold: float = 0.1) -> int:
    """ETCCDI-style CDD. Missing or non-consecutive dates terminate a spell."""
    longest = current = 0
    previous: date | None = None
    for row in daily_rows:
        current_date = date.fromisoformat(row["timestamp"])
        rain = row["precipitation_mm"]
        is_consecutive = previous is None or (current_date - previous).days == 1
        if rain is not None and rain < threshold and is_consecutive:
            current += 1
            longest = max(longest, current)
        elif rain is not None and rain < threshold:
            current = 1
            longest = max(longest, current)
        else:
            current = 0
        previous = current_date
    return longest


def _season_year(timestamp: str) -> int:
    year = int(timestamp[:4])
    month = int(timestamp[5:7])
    return year + 1 if month == 12 else year


def _slope(values: list[tuple[int, float]]) -> float | None:
    if len(values) < 2:
        return None
    mean_x = statistics.fmean(v[0] for v in values)
    mean_y = statistics.fmean(v[1] for v in values)
    denominator = sum((x - mean_x) ** 2 for x, _ in values)
    if denominator == 0:
        return None
    return sum((x - mean_x) * (y - mean_y) for x, y in values) / denominator


class AnalysisService:
    def __init__(self, repository: WeatherRepository):
        self.repository = repository

    def quality_report(self, rows: list[dict[str, Any]]) -> dict[str, Any]:
        fields = ("temperature_c", "precipitation_mm", "solar_radiation_wm2", "humidity_pct")
        missing_by_field = {field: sum(row[field] is None for row in rows) for field in fields}
        missing_values = sum(missing_by_field.values())
        suspect_records = sum(row["quality_flag"] not in VALID_QUALITY_FLAGS for row in rows)
        seen: set[tuple[str, str]] = set()
        duplicates = 0
        for row in rows:
            key = (row["timestamp"], row["plot_id"])
            duplicates += int(key in seen)
            seen.add(key)
        out_of_range = 0
        ranges = {
            "temperature_c": (-50, 60),
            "precipitation_mm": (0, 500),
            "solar_radiation_wm2": (0, 1400),
            "humidity_pct": (0, 100),
        }
        for row in rows:
            for field, (minimum, maximum) in ranges.items():
                value = row[field]
                out_of_range += int(value is not None and not (minimum <= value <= maximum))
        total_cells = max(1, len(rows) * len(fields))
        penalty = missing_values / total_cells * 55 + suspect_records / max(1, len(rows)) * 25 + out_of_range / total_cells * 70 + duplicates / max(1, len(rows)) * 60
        score = max(0.0, 100.0 - penalty)
        return {
            "scope": "ingestion_quality",
            "scope_label": "入库质量",
            "score_label": "入库质量得分",
            "interpretation": "仅评价字段完整性、类型、工程范围、缺测、质量标记和重复；不代表上游数据的测量准确性或现场代表性。",
            "score": round(score, 1),
            "grade": "优秀" if score >= 95 else "良好" if score >= 85 else "需治理",
            "missing_values": missing_values,
            "missing_by_field": missing_by_field,
            "suspect_records": suspect_records,
            "out_of_range_values": out_of_range,
            "duplicate_records": duplicates,
            "valid_records": max(0, len(rows) - suspect_records),
            "total_records": len(rows),
            "processing": {
                "score_scope": "ingestion_only",
                "missing_policy": "按指标剔除缺测单元，不丢弃整行；结果保留有效样本数",
                "duplicate_policy": "timestamp + plot_id 唯一键去重；默认保留库内原记录，冲突进入导入审计；replace=true 仅用于整库替换",
                "anomaly_policy": "物理范围先校验，统计异常采用 1.5×IQR 标记但保留原值",
            },
            "coverage_pct": {
                field: round((len(rows) - missing_by_field[field]) / max(1, len(rows)) * 100, 2)
                for field in fields
            },
        }

    def _overview(self, rows: list[dict[str, Any]]) -> dict[str, Any]:
        daily = _daily(rows)
        temperatures = [r["temperature_c"] for r in rows if r["temperature_c"] is not None]
        precipitation = [r["precipitation_mm"] for r in daily if r["precipitation_mm"] is not None]
        solar = [r["solar_radiation_wm2"] for r in rows if r["solar_radiation_wm2"] is not None]
        humidity = [r["humidity_pct"] for r in rows if r["humidity_pct"] is not None]
        return {
            "record_count": len(rows),
            "plot_count": len({r["plot_id"] for r in rows}),
            "day_count": len(daily),
            "date_start": min((r["timestamp"] for r in rows), default=None),
            "date_end": max((r["timestamp"] for r in rows), default=None),
            "avg_temperature_c": _round(_mean(temperatures)),
            "min_temperature_c": _round(min(temperatures)) if temperatures else None,
            "max_temperature_c": _round(max(temperatures)) if temperatures else None,
            "total_precipitation_mm": _round(sum(precipitation)),
            "rainy_days": sum(value >= 0.1 for value in precipitation),
            "avg_solar_radiation_wm2": _round(_mean(solar)),
            "avg_humidity_pct": _round(_mean(humidity)),
            "longest_dry_spell_days": _longest_dry_spell(daily, threshold=1.0),
        }

    def _season_stats(self, subset: list[dict[str, Any]], season_name: str, season_year: int | None = None) -> dict[str, Any]:
        daily = _daily(subset)
        temperatures = [r["temperature_c"] for r in subset if r["temperature_c"] is not None]
        rain = [r["precipitation_mm"] for r in daily if r["precipitation_mm"] is not None]
        solar = [r["solar_radiation_wm2"] for r in subset if r["solar_radiation_wm2"] is not None]
        humidity = [r["humidity_pct"] for r in subset if r["humidity_pct"] is not None]
        return {
            "season": season_name,
            "season_year": season_year,
            "season_years": sorted({_season_year(r["timestamp"]) for r in subset}),
            "record_count": len(subset),
            "day_count": len(daily),
            "avg_temperature_c": _round(_mean(temperatures)),
            "min_temperature_c": _round(min(temperatures)) if temperatures else None,
            "max_temperature_c": _round(max(temperatures)) if temperatures else None,
            "total_precipitation_mm": _round(sum(rain)),
            "rainy_days": sum(value >= 0.1 for value in rain),
            "longest_dry_spell_days": _longest_dry_spell(daily, threshold=1.0),
            "avg_solar_radiation_wm2": _round(_mean(solar)),
            "low_light_days": sum(r["solar_radiation_wm2"] is not None and r["solar_radiation_wm2"] < 100 for r in daily),
            "avg_humidity_pct": _round(_mean(humidity)),
        }

    def _seasonal(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for season_name in SEASONS:
            subset = [r for r in rows if season_for_month(int(r["timestamp"][5:7])) == season_name]
            result.append(self._season_stats(subset, season_name))
        return result

    def _seasonal_by_year(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        grouped: dict[tuple[int, str], list[dict[str, Any]]] = defaultdict(list)
        for row in rows:
            season = season_for_month(int(row["timestamp"][5:7]))
            grouped[(_season_year(row["timestamp"]), season)].append(row)
        season_order = {name: index for index, name in enumerate(SEASONS)}
        return [
            self._season_stats(grouped[key], key[1], key[0])
            for key in sorted(grouped, key=lambda value: (value[0], season_order[value[1]]))
        ]

    def _seasonal_by_plot(
        self,
        source_rows: list[dict[str, Any]],
        analysis_rows: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Produce export-grade per-plot, per-season-year statistics and coverage."""
        source_groups: dict[tuple[str, int, str], list[dict[str, Any]]] = defaultdict(list)
        valid_groups: dict[tuple[str, int, str], list[dict[str, Any]]] = defaultdict(list)
        for row in source_rows:
            season = season_for_month(int(row["timestamp"][5:7]))
            source_groups[(row["plot_id"], _season_year(row["timestamp"]), season)].append(row)
        for row in analysis_rows:
            season = season_for_month(int(row["timestamp"][5:7]))
            valid_groups[(row["plot_id"], _season_year(row["timestamp"]), season)].append(row)
        season_order = {name: index for index, name in enumerate(SEASONS)}
        output: list[dict[str, Any]] = []
        for key in sorted(source_groups, key=lambda value: (value[0], value[1], season_order[value[2]])):
            plot_id, season_year, season = key
            source = sorted(source_groups[key], key=lambda row: row["timestamp"])
            valid = sorted(valid_groups.get(key, []), key=lambda row: row["timestamp"])
            stats = self._season_stats(valid, season, season_year)
            period_start, period_end = source[0]["timestamp"], source[-1]["timestamp"]
            expected_days = (date.fromisoformat(period_end) - date.fromisoformat(period_start)).days + 1
            valid_days = len({row["timestamp"] for row in valid})
            trends = self._trends(valid) if valid else {}
            stats.update(
                {
                    "plot_id": plot_id,
                    "period_start": period_start,
                    "period_end": period_end,
                    "expected_days": expected_days,
                    "valid_days": valid_days,
                    "coverage_pct": round(valid_days / expected_days * 100, 2) if expected_days else 0.0,
                    "temperature_trend_c_30d": trends.get("temperature_c", {}).get("slope_per_30_days"),
                    "outlier_count": 0,
                    "risk_count": 0,
                }
            )
            output.append(stats)
        return output

    def _monthly(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in rows:
            groups[row["timestamp"][:7]].append(row)
        result: list[dict[str, Any]] = []
        for month in sorted(groups):
            subset = groups[month]
            daily = _daily(rows, subset)
            rain = [r["precipitation_mm"] for r in daily if r["precipitation_mm"] is not None]
            result.append(
                {
                    "month": month,
                    "avg_temperature_c": _round(_mean(r["temperature_c"] for r in subset)),
                    "total_precipitation_mm": _round(sum(rain)),
                    "avg_solar_radiation_wm2": _round(_mean(r["solar_radiation_wm2"] for r in subset)),
                    "avg_humidity_pct": _round(_mean(r["humidity_pct"] for r in subset)),
                    "rainy_days": sum(value >= 0.1 for value in rain),
                }
            )
        return result

    def _trends(self, rows: list[dict[str, Any]]) -> dict[str, Any]:
        daily = _daily(rows)
        output: dict[str, Any] = {}
        for metric in METRIC_LABELS:
            points = [(index, float(row[metric])) for index, row in enumerate(daily) if row[metric] is not None]
            slope_day = _slope(points)
            slope_30 = slope_day * 30 if slope_day is not None else None
            tolerance = {"temperature_c": 0.1, "precipitation_mm": 0.1, "solar_radiation_wm2": 1.0, "humidity_pct": 0.2}[metric]
            direction = "样本不足" if len(points) < 30 or slope_30 is None else "上升" if slope_30 > tolerance else "下降" if slope_30 < -tolerance else "平稳"
            output[metric] = {
                "label": METRIC_LABELS[metric],
                "unit": METRIC_UNITS[metric],
                "slope_per_30_days": _round(slope_30, 3),
                "direction": direction,
                "sample_days": len(points),
            }
        return output

    def _anomalies(self, rows: list[dict[str, Any]]) -> dict[str, Any]:
        items: list[dict[str, Any]] = []
        counts: dict[str, int] = {metric: 0 for metric in METRIC_LABELS}
        group_counts: dict[str, int] = defaultdict(int)
        grouped: dict[tuple[str, int, str], list[dict[str, Any]]] = defaultdict(list)
        for row in rows:
            season = season_for_month(int(row["timestamp"][5:7]))
            grouped[(row["plot_id"], _season_year(row["timestamp"]), season)].append(row)
        for (plot_id, season_year, season), group_rows in grouped.items():
            for metric in METRIC_LABELS:
                values = sorted(float(r[metric]) for r in group_rows if r[metric] is not None)
                if len(values) < 8:
                    continue
                q1 = _quantile(values, 0.25)
                q3 = _quantile(values, 0.75)
                iqr = q3 - q1
                if iqr == 0:
                    continue
                lower = q1 - 1.5 * iqr
                upper = q3 + 1.5 * iqr
                for row in group_rows:
                    value = row[metric]
                    if value is None or lower <= value <= upper:
                        continue
                    distance = (lower - value) if value < lower else (value - upper)
                    counts[metric] += 1
                    group_counts[f"{plot_id}|{season_year}|{season}"] += 1
                    items.append(
                        {
                            "timestamp": row["timestamp"],
                            "plot_id": plot_id,
                            "season": season,
                            "season_year": season_year,
                            "metric": metric,
                            "metric_label": METRIC_LABELS[metric],
                            "value": value,
                            "unit": METRIC_UNITS[metric],
                            "lower_bound": _round(lower),
                            "upper_bound": _round(upper),
                            "severity": "高" if iqr and distance > iqr else "中",
                            "distance": distance,
                        }
                    )
        items.sort(key=lambda item: item["distance"], reverse=True)
        for item in items:
            item.pop("distance", None)
        return {
            "method": "同一地块×季节年×季节内 1.5×IQR（每组每指标至少8条）",
            "total": sum(counts.values()),
            "counts": counts,
            "group_counts": dict(group_counts),
            "items": items[:40],
        }

    def _risk_events(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        configs = {
            "HEAT_DAY": ("高温日", "中", "temperature_c ≥ 35°C", "加强土壤墒情监测，优先安排早晚灌溉。"),
            "HEAT_STREAK": ("连续高温", "高", "连续3日日均气温 ≥ 35°C", "连续高温期启动遮阳、补水和田间巡查。"),
            "COLD_DAY": ("低温冻害", "高", "temperature_c ≤ 0°C", "必要时覆盖保温，并避开清晨低温时段灌溉。"),
            "HEAVY_RAIN": ("强降水积涝", "高", "precipitation_mm ≥ 50 mm/day", "疏通田间沟渠，雨后巡查根区积水和病害。"),
            "DRY_SPELL": ("连续干日", "中", "连续7日 precipitation_mm < 1 mm/day", "结合土壤墒情安排分区灌溉。"),
            "LOW_LIGHT_STREAK": ("连续低光照", "中", "连续3日 solar_radiation_wm2 < 100 W/m²", "设施农业复核遮阳与补光策略。"),
            "TEMP_SWING": ("日际温度突变", "中", "相邻有效日日均温差绝对值 ≥ 10°C", "关注敏感生育期，检查防寒或通风措施。"),
        }
        by_plot: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in rows:
            by_plot[row["plot_id"]].append(row)
        events: list[dict[str, Any]] = []

        def add_event(
            code: str,
            plot_id: str,
            selected: list[dict[str, Any]],
            metric: str,
            value: float,
            unit: str,
            *,
            value_label: str = "观测值",
        ) -> None:
            name, severity, threshold, recommendation = configs[code]
            start = selected[0]["timestamp"]
            end = selected[-1]["timestamp"]
            duration = (date.fromisoformat(end) - date.fromisoformat(start)).days + 1
            evidence = f"{plot_id} 在 {start}" + (f" 至 {end}" if end != start else "") + f" 命中 {duration} 天，{value_label} {round(value, 2)} {unit}"
            events.append(
                {
                    "event_id": f"{code}:{plot_id}:{start}:{end}",
                    "code": code,
                    "name": name,
                    "severity": severity,
                    "plot_id": plot_id,
                    "start_date": start,
                    "end_date": end,
                    "duration_days": duration,
                    "metric": metric,
                    "metric_value": round(value, 2),
                    "unit": unit,
                    "threshold": threshold,
                    "evidence": evidence,
                    "recommendation": recommendation,
                }
            )

        def streaks(plot_rows: list[dict[str, Any]], field: str, predicate: Any, minimum: int) -> list[list[dict[str, Any]]]:
            matches: list[list[dict[str, Any]]] = []
            current: list[dict[str, Any]] = []
            previous: date | None = None
            for row in plot_rows:
                current_date = date.fromisoformat(row["timestamp"])
                value = row[field]
                consecutive = previous is not None and (current_date - previous).days == 1
                if value is not None and predicate(value):
                    if not consecutive:
                        if len(current) >= minimum:
                            matches.append(current)
                        current = []
                    current.append(row)
                else:
                    if len(current) >= minimum:
                        matches.append(current)
                    current = []
                previous = current_date
            if len(current) >= minimum:
                matches.append(current)
            return matches

        for plot_id, plot_rows in by_plot.items():
            plot_rows.sort(key=lambda row: row["timestamp"])
            for run in streaks(plot_rows, "temperature_c", lambda value: value >= 35, 1):
                add_event(
                    "HEAT_DAY", plot_id, run, "temperature_c",
                    max(row["temperature_c"] for row in run), "°C", value_label="峰值",
                )
            for run in streaks(plot_rows, "temperature_c", lambda value: value <= 0, 1):
                add_event(
                    "COLD_DAY", plot_id, run, "temperature_c",
                    min(row["temperature_c"] for row in run), "°C", value_label="最低值",
                )
            for run in streaks(plot_rows, "precipitation_mm", lambda value: value >= 50, 1):
                add_event(
                    "HEAVY_RAIN", plot_id, run, "precipitation_mm",
                    max(row["precipitation_mm"] for row in run), "mm/day", value_label="峰值",
                )

            for run in streaks(plot_rows, "temperature_c", lambda value: value >= 35, 3):
                add_event("HEAT_STREAK", plot_id, run, "temperature_c", max(row["temperature_c"] for row in run), "°C peak")
            for run in streaks(plot_rows, "precipitation_mm", lambda value: value < 1, 7):
                add_event("DRY_SPELL", plot_id, run, "precipitation_mm", sum(row["precipitation_mm"] for row in run), "mm total")
            for run in streaks(plot_rows, "solar_radiation_wm2", lambda value: value < 100, 3):
                add_event("LOW_LIGHT_STREAK", plot_id, run, "solar_radiation_wm2", statistics.fmean(row["solar_radiation_wm2"] for row in run), "W/m² mean")
            for previous, current in zip(plot_rows, plot_rows[1:]):
                if (date.fromisoformat(current["timestamp"]) - date.fromisoformat(previous["timestamp"])).days != 1:
                    continue
                before, after = previous["temperature_c"], current["temperature_c"]
                if before is not None and after is not None and abs(after - before) >= 10:
                    add_event("TEMP_SWING", plot_id, [previous, current], "temperature_c", abs(after - before), "°C change")

        return sorted(events, key=lambda event: (event["start_date"], event["plot_id"], event["code"]))

    def _risks(self, events: list[dict[str, Any]]) -> list[dict[str, Any]]:
        order = ("HEAT_DAY", "HEAT_STREAK", "COLD_DAY", "HEAVY_RAIN", "DRY_SPELL", "LOW_LIGHT_STREAK", "TEMP_SWING")
        defaults = {
            "HEAT_DAY": ("高温日", "中", "temperature_c ≥ 35°C", "加强土壤墒情监测，优先安排早晚灌溉。"),
            "HEAT_STREAK": ("连续高温", "高", "连续3日日均气温 ≥ 35°C", "连续高温期启动遮阳、补水和田间巡查。"),
            "COLD_DAY": ("低温冻害", "高", "temperature_c ≤ 0°C", "必要时覆盖保温，并避开清晨低温时段灌溉。"),
            "HEAVY_RAIN": ("强降水积涝", "高", "precipitation_mm ≥ 50 mm/day", "疏通田间沟渠，雨后巡查根区积水和病害。"),
            "DRY_SPELL": ("连续干日", "中", "连续7日 precipitation_mm < 1 mm/day", "结合土壤墒情安排分区灌溉。"),
            "LOW_LIGHT_STREAK": ("连续低光照", "中", "连续3日 solar_radiation_wm2 < 100 W/m²", "设施农业复核遮阳与补光策略。"),
            "TEMP_SWING": ("日际温度突变", "中", "相邻有效日日均温差绝对值 ≥ 10°C", "关注敏感生育期，检查防寒或通风措施。"),
        }
        output: list[dict[str, Any]] = []
        for code in order:
            name, severity, threshold, recommendation = defaults[code]
            matched = [event for event in events if event["code"] == code]
            plots = sorted({event["plot_id"] for event in matched})
            evidence = f"检测到 {len(matched)} 个事件"
            if matched:
                evidence += f"，涉及 {len(plots)} 个地块，最近事件 {matched[-1]['start_date']} 至 {matched[-1]['end_date']}"
            output.append(
                {
                    "code": code,
                    "name": name,
                    "severity": severity,
                    "status": "触发" if matched else "正常",
                    "metric": len(matched),
                    "threshold": threshold,
                    "evidence": evidence,
                    "plot_ids": plots,
                    "event_count": len(matched),
                    "events": matched[:20],
                    "recommendation": recommendation,
                }
            )
        return output

    @staticmethod
    def _conclusions(overview: dict[str, Any], seasonal: list[dict[str, Any]], risks: list[dict[str, Any]], quality: dict[str, Any]) -> list[str]:
        if not seasonal:
            return ["当前筛选范围无有效气象数据。"]
        warmest = max(seasonal, key=lambda item: item["avg_temperature_c"] if item["avg_temperature_c"] is not None else -999)
        wettest = max(seasonal, key=lambda item: item["total_precipitation_mm"] or 0)
        brightest = max(seasonal, key=lambda item: item["avg_solar_radiation_wm2"] or 0)
        triggered = [risk for risk in risks if risk["status"] == "触发"]
        return [
            f"温度季节性清晰：{warmest['season']}平均气温 {warmest['avg_temperature_c']}°C，筛选期温度范围 {overview['min_temperature_c']}–{overview['max_temperature_c']}°C。",
            f"降水集中在{wettest['season']}，区域累计 {wettest['total_precipitation_mm']} mm、雨日 {wettest['rainy_days']} 天；全期最长连续少雨 {overview['longest_dry_spell_days']} 天。",
            f"{brightest['season']}平均太阳辐射最高，为 {brightest['avg_solar_radiation_wm2']} W/m²，可结合物候期评估光能利用。",
            f"入库质量评分 {quality['score']} 分（{quality['grade']}），仅反映缺测、范围、标记与重复检查；缺测 {quality['missing_values']} 个、疑似记录 {quality['suspect_records']} 条。",
            ("当前重点风险：" + "、".join(f"{r['name']}({r['evidence']})" for r in triggered) + "。") if triggered else "当前规则阈值内未触发重点农业气象风险。",
        ]

    def build_result(self, rows: list[dict[str, Any]], filters: dict[str, Any]) -> dict[str, Any]:
        if not rows:
            raise ValueError("筛选范围内没有观测数据")
        quality = self.quality_report(rows)
        analysis_rows = [row for row in rows if row["quality_flag"] in VALID_QUALITY_FLAGS]
        if not analysis_rows:
            raise ValueError("筛选范围内没有 quality_flag=valid 的有效观测")
        overview = self._overview(analysis_rows)
        seasonal = self._seasonal(analysis_rows)
        anomalies = self._anomalies(analysis_rows)
        risk_events = self._risk_events(analysis_rows)
        risks = self._risks(risk_events)
        seasonal_by_plot = self._seasonal_by_plot(rows, analysis_rows)
        for item in seasonal_by_plot:
            group_key = f"{item['plot_id']}|{item['season_year']}|{item['season']}"
            item["outlier_count"] = anomalies.get("group_counts", {}).get(group_key, 0)
            item["risk_count"] = sum(
                event["plot_id"] == item["plot_id"]
                and _season_year(event["start_date"]) == item["season_year"]
                and season_for_month(int(event["start_date"][5:7])) == item["season"]
                for event in risk_events
            )
        return {
            "filters": filters,
            "data_scope": {
                "source_records": len(rows),
                "analyzed_records": len(analysis_rows),
                "excluded_non_valid_records": len(rows) - len(analysis_rows),
                "quality_policy": "统计、异常与风险使用 valid/observed/corrected/imputed；suspect/rejected 不进入聚合，全部记录仍进入质量摘要",
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
                    "TEMP_SWING": "相邻日温差>=10°C",
                },
                "risk_note": "规则提示用于田间巡查排序，需结合土壤、作物与现场信息复核",
            },
            "overview": overview,
            "quality": quality,
            "seasonal": seasonal,
            "seasonal_by_year": self._seasonal_by_year(analysis_rows),
            "seasonal_by_plot": seasonal_by_plot,
            "monthly": self._monthly(analysis_rows),
            "trends": self._trends(analysis_rows),
            "anomalies": anomalies,
            "risks": risks,
            "risk_events": risk_events,
            "conclusions": self._conclusions(overview, seasonal, risks, quality),
        }

    def run_analysis(self, filters: dict[str, Any]) -> dict[str, Any]:
        rows = self.repository.fetch_observations(filters)
        result = self.build_result(rows, filters)
        summary = self._summary(result)
        created_at = datetime.now().astimezone().isoformat(timespec="seconds")
        analysis_id = self.repository.save_analysis(created_at, filters, summary, result)
        return {
            "analysis_id": analysis_id,
            "created_at": created_at,
            "finished_at": created_at,
            "status": "completed",
            "filters": filters,
            "summary": summary,
            "record_count": result["overview"]["record_count"],
            "risk_count": len(result["risk_events"]),
            "result": result,
        }

    @staticmethod
    def _summary(result: dict[str, Any]) -> str:
        overview = result["overview"]
        highest_risk = next((r for r in result["risks"] if r["severity"] == "高" and r["status"] == "触发"), None)
        return (
            f"完成 {overview['record_count']} 条、{overview['plot_count']} 个地块、{overview['day_count']} 天气象记录分析；"
            f"平均气温 {overview['avg_temperature_c']}°C，累计区域降水 {overview['total_precipitation_mm']} mm，"
            f"入库质量 {result['quality']['score']} 分。"
            + (f" 高优先级风险：{highest_risk['name']}。" if highest_risk else " 当前无高优先级规则风险。")
        )

    def preview_analysis(self, filters: dict[str, Any]) -> dict[str, Any]:
        """Build a filtered export snapshot without polluting analysis history."""
        rows = self.repository.fetch_observations(filters)
        result = self.build_result(rows, filters)
        created_at = datetime.now().astimezone().isoformat(timespec="seconds")
        return {
            "analysis_id": "filtered",
            "created_at": created_at,
            "finished_at": created_at,
            "status": "completed",
            "filters": filters,
            "summary": self._summary(result),
            "record_count": result["overview"]["record_count"],
            "risk_count": len(result["risk_events"]),
            "result": result,
        }

    def overview_payload(self, filters: dict[str, Any]) -> dict[str, Any]:
        rows = self.repository.fetch_observations(filters)
        metadata = self.repository.dataset_metadata(filters)
        if not rows:
            return {
                "filters": filters,
                "dataset": metadata,
                "kpis": {},
                "quality": self.quality_report([]),
            }
        valid_rows = [row for row in rows if row["quality_flag"] in VALID_QUALITY_FLAGS]
        overview = self._overview(valid_rows) if valid_rows else {}
        return {
            "filters": filters,
            "dataset": metadata,
            "kpis": {
                "avg_temperature_c": overview.get("avg_temperature_c"),
                "min_temperature_c": overview.get("min_temperature_c"),
                "max_temperature_c": overview.get("max_temperature_c"),
                "total_precipitation_mm": overview.get("total_precipitation_mm"),
                "avg_solar_radiation_wm2": overview.get("avg_solar_radiation_wm2"),
                "avg_humidity_pct": overview.get("avg_humidity_pct"),
                "rainy_days": overview.get("rainy_days"),
                "longest_dry_spell_days": overview.get("longest_dry_spell_days"),
                "analyzed_records": len(valid_rows),
                "excluded_non_valid_records": len(rows) - len(valid_rows),
            },
            "quality": self.quality_report(rows),
        }

    def answer_question(self, question: str, analysis: dict[str, Any]) -> dict[str, Any]:
        text = question.strip()
        result = analysis["result"]
        overview = result["overview"]
        seasonal = result["seasonal"]
        risks = result["risks"]
        plot_scope = analysis.get("filters", {}).get("plot_id") or "全部地块"
        context = {
            "analysis_id": analysis["analysis_id"],
            "plot_scope": plot_scope,
            "date_start": overview["date_start"],
            "date_end": overview["date_end"],
            "record_count": overview["record_count"],
        }

        conversation = _conversation_reply(text)
        if conversation is not None:
            intent, answer, suggestions = conversation
            return {
                "answer": answer,
                "evidence": [],
                "analysis_id": analysis["analysis_id"],
                "intent": intent,
                "context": context,
                "suggestions": suggestions,
                "disclaimer": "涉及实际农事决策时，请再结合土壤、作物品种与现场巡查。",
                "_agent_context": {
                    "assistant_role": "农田气象数据分析搭档",
                    "capabilities": ["数据概览", "季节比较", "趋势判断", "风险解释", "质量检查"],
                    "current_scope": context,
                },
                "_evidence_catalog": {},
            }

        evidence: list[dict[str, Any]] = []
        intent = _question_intent(text)

        if any(word in text for word in ("降水", "下雨", "干旱", "雨季")):
            intent = "precipitation"
            wettest = max(seasonal, key=lambda item: item["total_precipitation_mm"] or 0)
            dry = next(r for r in risks if r["code"] == "DRY_SPELL")
            answer = f"{wettest['season']}降水最集中：累计 {wettest['total_precipitation_mm']} mm、雨日 {wettest['rainy_days']} 天。全期最长连续少雨 {overview['longest_dry_spell_days']} 天，规则状态为{dry['status']}。"
            evidence = [
                {"metric": "wettest_season", "value": wettest["season"], "source": "季节聚合"},
                {"metric": "seasonal_precipitation_mm", "value": wettest["total_precipitation_mm"], "source": "区域日均降水累加"},
                {"metric": "longest_dry_spell_days", "value": overview["longest_dry_spell_days"], "source": "逐日序列"},
            ]
        elif any(word in text for word in ("温度", "高温", "低温", "冻", "热")):
            intent = "temperature"
            warmest = max(seasonal, key=lambda item: item["avg_temperature_c"] if item["avg_temperature_c"] is not None else -999)
            heat = next(r for r in risks if r["code"] == "HEAT_DAY")
            frost = next(r for r in risks if r["code"] == "COLD_DAY")
            answer = f"{warmest['season']}平均气温最高，为 {warmest['avg_temperature_c']}°C；全期范围 {overview['min_temperature_c']}–{overview['max_temperature_c']}°C。高温日 {heat['metric']} 天，低温日 {frost['metric']} 天。"
            evidence = [
                {"metric": "warmest_season", "value": warmest["season"], "source": "季节聚合"},
                {"metric": "temperature_range_c", "value": [overview["min_temperature_c"], overview["max_temperature_c"]], "source": "观测极值"},
                {"metric": "heat_days", "value": heat["metric"], "source": heat["threshold"]},
            ]
        elif any(word in text for word in ("光照", "辐射", "太阳")):
            intent = "radiation"
            brightest = max(seasonal, key=lambda item: item["avg_solar_radiation_wm2"] or 0)
            low = next(r for r in risks if r["code"] == "LOW_LIGHT_STREAK")
            answer = f"{brightest['season']}光照最佳，平均太阳辐射 {brightest['avg_solar_radiation_wm2']} W/m²；连续低光照事件 {low['metric']} 个，风险状态为{low['status']}。"
            evidence = [
                {"metric": "brightest_season", "value": brightest["season"], "source": "季节聚合"},
                {"metric": "avg_solar_radiation_wm2", "value": brightest["avg_solar_radiation_wm2"], "source": "有效观测均值"},
                {"metric": "low_light_streaks", "value": low["metric"], "source": low["threshold"]},
            ]
        elif any(word in text for word in ("质量", "缺失", "异常", "可靠")):
            intent = "quality"
            quality = result["quality"]
            anomalies = result["anomalies"]
            answer = f"入库质量 {quality['score']} 分（{quality['grade']}）：该分数仅评价完整性、工程范围和重复；缺测 {quality['missing_values']} 个、疑似记录 {quality['suspect_records']} 条、重复 {quality['duplicate_records']} 条；IQR 标记统计异常 {anomalies['total']} 个。"
            evidence = [
                {"metric": "quality_score", "value": quality["score"], "source": "入库校验规则"},
                {"metric": "missing_values", "value": quality["missing_values"], "source": "字段完整性检查"},
                {"metric": "iqr_anomalies", "value": anomalies["total"], "source": "1.5×IQR"},
            ]
        elif any(word in text for word in ("风险", "建议", "管理", "农事")):
            intent = "risk"
            triggered = [r for r in risks if r["status"] == "触发"]
            if triggered:
                answer = "当前触发的气象风险为：" + "；".join(f"{r['name']}（{r['evidence']}）。建议：{r['recommendation']}" for r in triggered)
            else:
                answer = "当前阈值内未触发重点风险，建议继续关注最新观测并结合土壤墒情与作物物候复核。"
            evidence = [{"metric": r["code"], "value": r["metric"], "source": r["threshold"]} for r in triggered]
        else:
            answer = analysis["summary"] + " " + " ".join(result["conclusions"][:2])
            evidence = [
                {"metric": "record_count", "value": overview["record_count"], "source": "当前分析任务"},
                {"metric": "quality_score", "value": result["quality"]["score"], "source": "入库校验规则"},
            ]

        units = {
            "seasonal_precipitation_mm": "mm", "longest_dry_spell_days": "day",
            "temperature_range_c": "°C", "heat_days": "day", "avg_solar_radiation_wm2": "W/m²",
            "low_light_streaks": "event", "quality_score": "score", "missing_values": "cell",
            "iqr_anomalies": "event", "record_count": "record",
        }
        for item in evidence:
            item.setdefault("unit", units.get(str(item.get("metric")), ""))
            item.setdefault("plot_scope", plot_scope)
            item.setdefault("period_start", overview["date_start"])
            item.setdefault("period_end", overview["date_end"])
            item.setdefault("analysis_id", analysis["analysis_id"])
        scope_text = f"基于分析 #{analysis['analysis_id']}（{plot_scope}，{overview['date_start']} 至 {overview['date_end']}，{overview['record_count']} 条记录）："
        agent_context = _compact_agent_context(analysis, text)
        evidence_catalog = _focus_evidence_catalog(_agent_evidence_catalog(analysis, context), text)
        requested_period = agent_context.get("requested_period")
        if isinstance(requested_period, dict):
            period_slug = "-".join(f"{int(month):02d}" for month in requested_period.get("months", []))
            period_fields = {
                "avg_temperature_c": ("平均气温", "°C"),
                "total_precipitation_mm": ("累计降水", "mm"),
                "rainy_days": ("雨日", "day"),
                "avg_solar_radiation_wm2": ("平均太阳辐射", "W/m²"),
                "avg_humidity_pct": ("平均相对湿度", "%"),
            }
            for field, (label, unit) in period_fields.items():
                if requested_period.get(field) is None:
                    continue
                key = f"period.{period_slug}.{field}"
                evidence_catalog[key] = {
                    "metric": key,
                    "label": f"{requested_period.get('label')} {label}",
                    "value": requested_period[field],
                    "unit": unit,
                    "source": requested_period.get("method"),
                    "plot_scope": plot_scope,
                    "period_start": overview["date_start"],
                    "period_end": overview["date_end"],
                    "analysis_id": analysis["analysis_id"],
                }
        dataset_source = self.repository.active_dataset_source()
        if dataset_source is not None:
            provenance = dataset_source.get("provenance", {})
            agent_context["data_source"] = {
                "provider": dataset_source.get("provider"),
                "dataset_name": dataset_source.get("dataset_name"),
                "landing_page": dataset_source.get("landing_page"),
                "retrieved_at": provenance.get("retrieved_at"),
                "api_version": provenance.get("api_version"),
                "official_sources": provenance.get("official_sources", []),
                "spatial_note": provenance.get("spatial_note"),
                "quality_note": provenance.get("quality_note"),
                "points": [
                    {
                        key: point.get(key)
                        for key in ("plot_id", "label", "latitude", "longitude", "requested_coordinate")
                        if point.get(key) is not None
                    }
                    for point in provenance.get("points", [])
                ],
            }
            evidence_catalog["data.source"] = {
                "metric": "data.source",
                "label": "正式数据来源",
                "value": dataset_source.get("dataset_name"),
                "unit": "",
                "source": dataset_source.get("provider"),
                "landing_page": dataset_source.get("landing_page"),
                "retrieved_at": provenance.get("retrieved_at"),
                "analysis_id": analysis["analysis_id"],
            }
        return {
            "answer": scope_text + answer,
            "evidence": evidence,
            "analysis_id": analysis["analysis_id"],
            "intent": intent,
            "context": context,
            "suggestions": ["哪个季节降水最多？", "是否存在高温或干旱风险？", "这批数据质量可靠吗？"],
            "disclaimer": "Agent 的结论以当前筛选数据为依据，农业决策还需结合土壤、作物品种与现场巡查。",
            "_agent_context": agent_context,
            "_evidence_catalog": evidence_catalog,
        }
