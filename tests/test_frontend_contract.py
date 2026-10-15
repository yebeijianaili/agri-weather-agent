from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
JS = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
CSS = (ROOT / "static" / "styles.css").read_text(encoding="utf-8")


def test_provenance_is_visible_and_misleading_live_wording_is_absent():
    for element_id in (
        "sourceSection",
        "sourceProvider",
        "sourceApiVersion",
        "sourceProductType",
        "sourcePointCount",
        "sourcePeriod",
        "sourceRetrievedAt",
        "sourceHash",
    ):
        assert f'id="{element_id}"' in HTML

    combined = HTML + JS
    assert "API 已连接" not in combined
    assert "监测地块" not in combined
    assert "逐日观测" not in combined
    assert "页面刷新" in HTML
    assert "本地快照可追溯" in JS


def test_frontend_distinguishes_product_provenance_and_analysis_scope():
    assert "卫星/再分析网格产品" in JS
    assert "不是田间传感器或地面气象站实测" in JS
    assert "规范化 JSON 快照" in JS
    assert "入库规则校验" in JS
    assert "不代表地面测量准确率" in JS
    assert "function analysisMatchesFilters" in JS
    assert ".find(item => analysisMatchesFilters(item, filterSnapshot))" in JS


def test_mobile_controls_and_data_table_remain_accessible():
    assert 'aria-label="导入 CSV"' in HTML
    assert 'aria-label="打开导出菜单"' in HTML
    assert 'aria-label="运行分析"' in HTML
    assert 'aria-label="气象日值数据表，可横向滚动"' in HTML
    assert ".source-ledger" in CSS
    assert ".metric-card--risk { grid-column: 1 / -1" in CSS
    assert ".chat-bubble p { font-size: 13px" in CSS
    assert ".agent-input textarea" in CSS and "font-size: 14px" in CSS


def test_excel_report_is_exposed_as_a_real_download_option():
    assert 'data-export="xlsx"' in HTML
    assert "'/export/report.xlsx'" in JS
    assert "agri_weather_analysis_report.xlsx" in JS
