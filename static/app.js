/* global ResizeObserver, IntersectionObserver */
(() => {
  'use strict';

  const API_ROOT = '/api';
  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) => Array.from(root.querySelectorAll(selector));
  const formatter = new Intl.NumberFormat('zh-CN', { maximumFractionDigits: 1 });
  const integerFormatter = new Intl.NumberFormat('zh-CN', { maximumFractionDigits: 0 });
  const dateTimeFormatter = new Intl.DateTimeFormat('zh-CN', {
    month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false
  });
  const fullDateFormatter = new Intl.DateTimeFormat('zh-CN', {
    year: 'numeric', month: '2-digit', day: '2-digit'
  });
  const fullDateTimeFormatter = new Intl.DateTimeFormat('zh-CN', {
    year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false
  });

  const state = {
    overview: null,
    observations: [],
    observationTotal: 0,
    analysis: null,
    history: [],
    filters: { plot_id: '', year: '', season: '', start_date: '', end_date: '' },
    granularity: 'month',
    seasonMetric: 'temperature',
    chartModels: {},
    tableVisible: 10,
    selectedFile: null,
    busyCount: 0,
    health: false,
    agentRuntime: { mode: 'checking', model: '', fallbackReason: '' },
    agentRuntimeFromQuery: false,
    agentQuerySequence: 0,
    activeAgentQuery: 0,
    agentQueryPending: false,
    contextSequence: 0,
    contextActionPending: false,
    fastmcp: { status: null, tools: [], lastCall: null }
  };

  const seasonMeta = {
    spring: { name: '春季', aliases: ['spring', '春', '春季'], color: '#77a870', icon: '芽' },
    summer: { name: '夏季', aliases: ['summer', '夏', '夏季'], color: '#dfaa38', icon: '日' },
    autumn: { name: '秋季', aliases: ['autumn', 'fall', '秋', '秋季'], color: '#bd7952', icon: '穗' },
    winter: { name: '冬季', aliases: ['winter', '冬', '冬季'], color: '#6d9aa3', icon: '霜' }
  };

  const metricConfig = {
    temperature: { field: 'temperature', label: '区域日均温度', unit: '°C', color: '#b85f47' },
    rainfall: { field: 'rainfall', label: '区域平均累计降水', unit: 'mm', color: '#4f8f9c' },
    solar: { field: 'solar', label: '日均太阳辐射', unit: 'W/m²', color: '#dfa632' }
  };

  const Api = {
    async request(path, options = {}) {
      const { allow404 = false, timeout = 25000, ...fetchOptions } = options;
      const controller = new AbortController();
      const timer = setTimeout(() => controller.abort(), timeout);
      try {
        const response = await fetch(`${API_ROOT}${path}`, {
          headers: fetchOptions.body instanceof FormData ? undefined : { 'Content-Type': 'application/json', ...(fetchOptions.headers || {}) },
          ...fetchOptions,
          signal: controller.signal
        });
        if (allow404 && response.status === 404) return null;
        const contentType = response.headers.get('content-type') || '';
        const payload = contentType.includes('application/json') ? await response.json() : await response.text();
        if (!response.ok) {
          const detail = payload?.detail || payload?.message || payload?.error || (typeof payload === 'string' ? payload : `HTTP ${response.status}`);
          const readable = Array.isArray(detail)
            ? detail.map(item => item?.msg || item?.message || (typeof item === 'string' ? item : JSON.stringify(item))).join('；')
            : detail && typeof detail === 'object' ? detail.message || detail.msg || JSON.stringify(detail) : String(detail);
          const requestError = new Error(readable);
          requestError.status = response.status;
          requestError.detail = detail;
          requestError.payload = payload;
          throw requestError;
        }
        return payload;
      } catch (error) {
        if (error.name === 'AbortError') throw new Error('请求超时，请检查分析服务状态');
        throw error;
      } finally {
        clearTimeout(timer);
      }
    },
    health: () => Api.request('/health', { timeout: 6000 }),
    overview: filters => Api.request(`/overview${queryString(filters)}`),
    observations: (filters, limit = 5000, offset = 0) => Api.request(`/observations${queryString({ ...filters, limit, offset })}`),
    runAnalysis: filters => Api.request('/analysis/run', { method: 'POST', body: JSON.stringify(compactObject(filters)), timeout: 60000 }),
    latestAnalysis: () => Api.request('/analysis/latest', { allow404: true }),
    history: () => Api.request('/analysis/history?limit=30&offset=0', { allow404: true }),
    analysisDetail: id => Api.request(`/analysis/${encodeURIComponent(id)}`),
    queryAgent: payload => Api.request('/agent/query', { method: 'POST', body: JSON.stringify(compactObject(payload)), timeout: 60000 }),
    fastmcpStatus: () => Api.request('/fastmcp/status', { timeout: 12000 }),
    fastmcpTools: () => Api.request('/fastmcp/tools', { timeout: 12000 }),
    fastmcpCall: payload => Api.request('/fastmcp/call', { method: 'POST', body: JSON.stringify(payload), timeout: 60000 }),
    importCsv: (file, replace) => {
      const body = new FormData();
      body.append('file', file);
      return Api.request(`/observations/import?replace=${replace ? 'true' : 'false'}`, { method: 'POST', body, timeout: 90000 });
    }
  };

  function queryString(values) {
    const params = new URLSearchParams();
    Object.entries(values || {}).forEach(([key, value]) => {
      if (value !== '' && value !== null && value !== undefined) params.set(key, String(value));
    });
    const query = params.toString();
    return query ? `?${query}` : '';
  }

  function compactObject(values) {
    return Object.fromEntries(Object.entries(values || {}).filter(([, value]) => value !== '' && value !== null && value !== undefined));
  }

  function normalizeAgentRuntime(payload, fallbackMode = 'checking') {
    const source = typeof payload === 'string' ? { mode: payload } : payload && typeof payload === 'object' ? payload : {};
    const rawMode = String(source.mode || source.response_mode || source.execution_mode || source.active_mode || '').trim().toLowerCase();
    let mode = fallbackMode;
    if (['local_model', 'local-model', 'local', 'model', 'llm', 'ollama'].includes(rawMode)) mode = 'local_model';
    else if (['rule_fallback', 'rule-fallback', 'rule', 'rules', 'fallback', 'deterministic'].includes(rawMode)) mode = 'rule_fallback';
    else if (source.fallback === true || source.used_fallback === true || source.local_model_ready === false) mode = 'rule_fallback';
    else if (source.local_model_ready === true || (source.ready === true && source.model)) mode = 'local_model';
    return {
      mode,
      model: firstText(source, ['model', 'model_name', 'name']),
      fallbackReason: firstText(source, ['fallback_reason', 'reason', 'message', 'detail'])
    };
  }

  function setAgentRuntime(payload, { source = 'health', fallbackMode = 'checking', requestId = 0 } = {}) {
    if (source === 'health' && state.agentRuntimeFromQuery) return;
    if (source === 'query' && requestId !== state.activeAgentQuery) return;
    state.agentRuntime = normalizeAgentRuntime(payload, fallbackMode);
    if (source === 'query') state.agentRuntimeFromQuery = true;
    renderAgentStatus();
  }

  function renderAgentStatus() {
    const badge = $('#agentStatusBadge');
    const label = $('#agentStatusText');
    if (!badge || !label) return;
    const blocked = Boolean(state.analysis) && !isCompletedAnalysis();
    const runtime = state.agentRuntime || { mode: 'checking', model: '', fallbackReason: '' };
    const mode = blocked ? 'locked' : runtime.mode === 'local_model' ? 'local_model' : runtime.mode === 'rule_fallback' ? 'rule_fallback' : 'checking';
    const labels = { checking: '检测中', local_model: `本地模型${runtime.model ? ` · ${runtime.model}` : ''}`, rule_fallback: '规则回退', locked: '已锁定' };
    badge.dataset.mode = mode;
    badge.classList.toggle('is-blocked', blocked);
    label.textContent = labels[mode];
    const detail = mode === 'locked' ? '当前分析未成功完成，Agent 问答已锁定'
      : mode === 'local_model' ? `本地模型${runtime.model ? `：${runtime.model}` : '运行正常'}`
      : mode === 'rule_fallback' ? `当前使用规则回退${runtime.fallbackReason ? `：${runtime.fallbackReason}` : ''}`
      : '正在检测 Agent 运行模式';
    badge.title = detail;
    badge.setAttribute('aria-label', `${labels[mode]}。${detail}`);
  }

  function invalidateAgentQuery({ render = true } = {}) {
    state.activeAgentQuery = ++state.agentQuerySequence;
    state.agentQueryPending = false;
    $('#agentTyping')?.remove();
    if (render) renderAnalysisState();
  }

  function beginAnalysisContextAction() {
    const contextId = ++state.contextSequence;
    state.contextActionPending = true;
    invalidateAgentQuery();
    return contextId;
  }

  function isCurrentContext(contextId) {
    return contextId === state.contextSequence;
  }

  function finishAnalysisContextAction(contextId) {
    if (!isCurrentContext(contextId)) return;
    state.contextActionPending = false;
    renderAnalysisState();
  }

  function number(value, fallback = null) {
    if (value === '' || value === null || value === undefined) return fallback;
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : fallback;
  }

  function firstNumber(object, keys, fallback = null) {
    if (!object) return fallback;
    for (const key of keys) {
      const value = key.includes('.') ? key.split('.').reduce((current, part) => current?.[part], object) : object[key];
      const parsed = number(value);
      if (parsed !== null) return parsed;
    }
    return fallback;
  }

  function firstText(object, keys, fallback = '') {
    if (!object) return fallback;
    for (const key of keys) {
      const value = key.includes('.') ? key.split('.').reduce((current, part) => current?.[part], object) : object[key];
      if (typeof value === 'string' && value.trim()) return value.trim();
    }
    return fallback;
  }

  function formatNumber(value, digits = 1, empty = '--') {
    const parsed = number(value);
    if (parsed === null) return empty;
    return new Intl.NumberFormat('zh-CN', { maximumFractionDigits: digits, minimumFractionDigits: digits }).format(parsed);
  }

  function formatInteger(value, empty = '--') {
    const parsed = number(value);
    return parsed === null ? empty : integerFormatter.format(Math.round(parsed));
  }

  function parseDate(value) {
    if (!value) return null;
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? null : date;
  }

  function formatDate(value, includeTime = false) {
    const date = parseDate(value);
    if (!date) return '--';
    return includeTime ? dateTimeFormatter.format(date) : fullDateFormatter.format(date);
  }

  function formatFullDateTime(value) {
    const date = parseDate(value);
    return date ? fullDateTimeFormatter.format(date) : '--';
  }

  function escapeHtml(value) {
    return String(value ?? '')
      .replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;')
      .replaceAll('"', '&quot;').replaceAll("'", '&#039;');
  }

  function displayTruthText(value) {
    return String(value ?? '')
      .replaceAll('地块范围', '空间样本范围')
      .replaceAll('个地块', '个空间样本')
      .replaceAll('观测记录', '数据记录')
      .replaceAll('观测值', '数据值')
      .replaceAll('数据质量评分', '入库质量评分')
      .replaceAll('数据质量', '入库质量');
  }

  function setText(selector, value) {
    const element = typeof selector === 'string' ? $(selector) : selector;
    if (element) element.textContent = value;
  }

  function setExternalLink(selector, href, label) {
    const link = $(selector);
    if (!link) return;
    if (label) link.textContent = label;
    if (href) {
      link.href = href;
      link.removeAttribute('aria-disabled');
    } else {
      link.removeAttribute('href');
      link.setAttribute('aria-disabled', 'true');
    }
  }

  function resultOf(analysis = state.analysis) {
    if (isFailedAnalysis(analysis)) return {};
    return analysis?.result || analysis?.data?.result || analysis?.data || {};
  }

  function analysisStatus(analysis = state.analysis) {
    return String(analysis?.status || analysis?.state || '').trim().toLowerCase();
  }

  function isFailedAnalysis(analysis = state.analysis) {
    return ['failed', 'error'].includes(analysisStatus(analysis));
  }

  function isCompletedAnalysis(analysis = state.analysis) {
    if (!analysis) return false;
    const status = analysisStatus(analysis);
    if (['completed', 'success', 'succeeded', 'done'].includes(status)) return true;
    if (status) return false;
    const result = analysis?.result || analysis?.data?.result || analysis?.data;
    return Boolean(result && typeof result === 'object' && Object.keys(result).length);
  }

  function analysisIdOf(analysis = state.analysis) {
    return analysis?.analysis_id || analysis?.id || analysis?.uuid || null;
  }

  function analysisFailureMessage(analysis = state.analysis) {
    const result = analysis?.result || analysis?.data?.result || analysis?.data || {};
    return firstText(analysis, ['error_message', 'error', 'message'])
      || (typeof analysis?.summary === 'string' ? analysis.summary.trim() : firstText(analysis?.summary, ['text', 'message']))
      || firstText(result, ['error_message', 'error', 'message'])
      || '当前筛选范围未生成可用分析结果';
  }

  function normalizeOverview(payload) {
    const source = payload?.overview || payload?.data || payload || {};
    return {
      raw: source,
      filters: source.filters || {},
      dataset: source.dataset || {},
      kpis: source.kpis || source.metrics || {},
      quality: source.quality || source.data_quality || {}
    };
  }

  function normalizeObservation(item) {
    return {
      id: item?.id,
      timestamp: item?.timestamp || item?.date || item?.observed_at,
      plot_id: String(item?.plot_id ?? item?.plot ?? item?.field_id ?? '--'),
      temperature_c: firstNumber(item, ['temperature_c', 'temperature', 'temp_c']),
      precipitation_mm: firstNumber(item, ['precipitation_mm', 'rainfall_mm', 'precipitation', 'rainfall']),
      solar_radiation_wm2: firstNumber(item, ['solar_radiation_wm2', 'solar_radiation', 'light_wm2', 'sunlight']),
      humidity_pct: firstNumber(item, ['humidity_pct', 'humidity', 'relative_humidity']),
      quality_flag: String(item?.quality_flag ?? item?.quality ?? 'valid')
    };
  }

  function normalizeAnalysis(payload) {
    if (!payload) return null;
    return payload.analysis || payload.data?.analysis || payload.data || payload;
  }

  function normalizeHistory(payload) {
    const items = Array.isArray(payload) ? payload : payload?.items || payload?.history || payload?.data?.items || [];
    return Array.isArray(items) ? items.map(normalizeAnalysis).filter(Boolean) : [];
  }

  function normalizedScope(filters = {}) {
    return {
      plot_id: String(filters.plot_id || ''),
      year: String(filters.year ?? filters.season_year ?? ''),
      season: seasonKey(filters.season) || String(filters.season || ''),
      start_date: String(filters.start_date || ''),
      end_date: String(filters.end_date || '')
    };
  }

  function analysisMatchesFilters(analysis, filters) {
    if (!isCompletedAnalysis(analysis)) return false;
    const analysisFilters = analysis?.filters || analysis?.result?.filters || analysis?.data?.result?.filters || {};
    const left = normalizedScope(analysisFilters);
    const right = normalizedScope(filters);
    return Object.keys(right).every(key => left[key] === right[key]);
  }

  function seasonKey(value) {
    const normalized = String(value ?? '').toLowerCase().trim();
    return Object.entries(seasonMeta).find(([, meta]) => meta.aliases.some(alias => normalized === alias.toLowerCase()))?.[0] || null;
  }

  function seasonForMonth(month) {
    if ([3, 4, 5].includes(month)) return 'spring';
    if ([6, 7, 8].includes(month)) return 'summer';
    if ([9, 10, 11].includes(month)) return 'autumn';
    return 'winter';
  }

  function normalizeSeasonal() {
    if (isFailedAnalysis()) return {};
    const result = resultOf();
    const raw = result.seasonal || result.seasonal_stats || result.seasons || state.analysis?.seasonal;
    const normalized = {};
    if (Array.isArray(raw)) {
      raw.forEach(item => {
        const key = seasonKey(item.season || item.name || item.label);
        if (key) normalized[key] = normalizeSeasonItem(item, key);
      });
    } else if (raw && typeof raw === 'object') {
      Object.entries(raw).forEach(([name, item]) => {
        const key = seasonKey(name) || seasonKey(item?.season || item?.name);
        if (key) normalized[key] = normalizeSeasonItem(item, key);
      });
    }
    const derived = deriveSeasonalFromObservations();
    Object.keys(seasonMeta).forEach(key => {
      if (!normalized[key] && derived[key]) normalized[key] = derived[key];
    });
    return normalized;
  }

  function normalizeSeasonItem(item, key) {
    const source = item || {};
    return {
      key,
      temperature: firstNumber(source, ['avg_temperature_c', 'average_temperature_c', 'temperature.avg', 'temperature', 'avg_temp']),
      rainfall: firstNumber(source, ['total_precipitation_mm', 'precipitation_total_mm', 'rainfall.total', 'rainfall', 'precipitation']),
      solar: firstNumber(source, ['avg_solar_radiation_wm2', 'average_solar_radiation_wm2', 'solar.avg', 'solar', 'sunlight']),
      humidity: firstNumber(source, ['avg_humidity_pct', 'humidity.avg', 'humidity']),
      rainyDays: firstNumber(source, ['rainy_days', 'rain_days']),
      drySpell: firstNumber(source, ['max_dry_spell_days', 'longest_dry_spell_days']),
      count: firstNumber(source, ['record_count', 'observation_count', 'count']),
      feature: firstText(source, ['feature', 'summary', 'characteristic', 'description'])
    };
  }

  function deriveSeasonalFromObservations() {
    const groups = {};
    aggregateObservations(state.observations, 'day').forEach(item => {
      const date = parseDate(item.label);
      if (!date) return;
      const key = seasonForMonth(date.getMonth() + 1);
      if (!groups[key]) groups[key] = { temperatures: [], rain: 0, solar: [], humidity: [], rainyDates: new Set(), count: 0 };
      const group = groups[key];
      if (item.temperature !== null) group.temperatures.push(item.temperature);
      if (item.rainfall !== null) {
        group.rain += item.rainfall;
        if (item.rainfall >= 0.1) group.rainyDates.add(item.label);
      }
      if (item.solar !== null) group.solar.push(item.solar);
      if (item.humidity !== null) group.humidity.push(item.humidity);
      group.count += item.count || 1;
    });
    return Object.fromEntries(Object.entries(groups).map(([key, group]) => {
      const temperature = average(group.temperatures);
      const solar = average(group.solar);
      const featureParts = [];
      if (temperature !== null) featureParts.push(`${formatNumber(temperature, 1)}°C`);
      if (group.rain > 0) featureParts.push(`${formatNumber(group.rain, 1)} mm 降水`);
      return [key, {
        key, temperature, rainfall: group.rain, solar, humidity: average(group.humidity),
        rainyDays: group.rainyDates.size, count: group.count,
        feature: featureParts.length ? `样本特征：${featureParts.join('，')}` : ''
      }];
    }));
  }

  function average(values) {
    const valid = values.filter(value => Number.isFinite(value));
    return valid.length ? valid.reduce((sum, value) => sum + value, 0) / valid.length : null;
  }

  function isValidObservation(item) {
    return /^(valid|observed|corrected|imputed|good|ok|normal|正常|有效|通过)$/i.test(String(item?.quality_flag || '').trim());
  }

  function getFiltersFromForm() {
    return {
      plot_id: $('#plotFilter').value,
      year: $('#yearFilter').value,
      season: $('#seasonFilter').value,
      start_date: $('#startDateFilter').value,
      end_date: $('#endDateFilter').value
    };
  }

  function syncFilterForm() {
    $('#plotFilter').value = state.filters.plot_id || '';
    $('#yearFilter').value = state.filters.year || '';
    $('#seasonFilter').value = state.filters.season || '';
    $('#startDateFilter').value = state.filters.start_date || '';
    $('#endDateFilter').value = state.filters.end_date || '';
  }

  function validateFilters(filters) {
    if (filters.start_date && filters.end_date && filters.start_date > filters.end_date) {
      showToast('日期范围有误', '开始日期应早于或等于结束日期', 'error');
      return false;
    }
    return true;
  }

  function updateFilterOptions() {
    const overview = normalizeOverview(state.overview);
    const dataset = overview.dataset;
    const currentPlot = state.filters.plot_id;
    const currentYear = state.filters.year;
    const plots = Array.from(new Set([
      ...(Array.isArray(dataset.plots) ? dataset.plots.map(item => typeof item === 'object' ? item.plot_id || item.id || item.name : item) : []),
      ...state.observations.map(item => item.plot_id)
    ].filter(Boolean))).sort((a, b) => String(a).localeCompare(String(b), 'zh-CN'));
    const years = Array.from(new Set([
      ...(Array.isArray(dataset.years) ? dataset.years : []),
      ...state.observations.map(item => parseDate(item.timestamp)?.getFullYear()).filter(Boolean)
    ])).sort((a, b) => Number(b) - Number(a));
    fillSelect($('#plotFilter'), '全部样本', plots, currentPlot);
    fillSelect($('#yearFilter'), '全部年份', years, currentYear);
  }

  function fillSelect(select, allLabel, values, selected) {
    const fragment = document.createDocumentFragment();
    const all = document.createElement('option');
    all.value = '';
    all.textContent = allLabel;
    fragment.appendChild(all);
    values.forEach(value => {
      const option = document.createElement('option');
      option.value = String(value);
      option.textContent = String(value);
      fragment.appendChild(option);
    });
    select.replaceChildren(fragment);
    select.value = selected || '';
  }

  async function checkHealth() {
    const dot = $('#healthDot');
    dot.className = 'status-dot is-checking';
    try {
      const health = await Api.health();
      state.health = true;
      dot.className = 'status-dot is-online';
      setText('#healthText', '分析服务运行正常');
      setText('#healthSubtext', health?.data_source?.provider ? `${health.data_source.provider} · 来源已登记` : firstText(health, ['service', 'message'], '本地服务已连接'));
      setText('#footerVersion', health?.version ? `v${health.version}` : 'v1.0');
      setAgentRuntime(health?.agent_runtime, { source: 'health', fallbackMode: 'rule_fallback' });
    } catch (error) {
      state.health = false;
      dot.className = 'status-dot is-offline';
      setText('#healthText', '分析服务未连接');
      setText('#healthSubtext', error.message);
      if (!state.agentRuntimeFromQuery) setAgentRuntime(null, { source: 'health', fallbackMode: 'checking' });
    }
  }

  function renderFastMCPTools() {
    const status = state.fastmcp.status || {};
    const tools = state.fastmcp.tools || [];
    const badge = $('#mcpStatus');
    const connected = status.status === 'connected';
    badge.dataset.state = connected ? 'connected' : 'error';
    $('span', badge).textContent = connected ? `已连接 · ${tools.length} 个工具` : '连接异常';
    setText('#mcpToolCount', `${tools.length} tools`);
    setText('#mcpRuntime', `FastMCP ${status.fastmcp_version || '--'} · ${status.transport || 'in-memory'} transport · ${formatNumber(status.latency_ms, 2)} ms`);

    const list = $('#mcpToolList');
    if (!tools.length) {
      list.innerHTML = '<div class="mcp-empty">服务端暂未返回工具定义</div>';
      return;
    }
    list.innerHTML = tools.map((tool, index) => {
      const properties = Object.keys(tool.inputSchema?.properties || {});
      return `<article class="mcp-tool-card"><span class="mcp-tool-card__index">0${index + 1}</span><div><strong>${escapeHtml(tool.name)}</strong><p>${escapeHtml(tool.description || 'FastMCP tool')}</p><small>${escapeHtml(properties.length ? properties.join(' · ') : '无参数')}</small></div></article>`;
    }).join('');
  }

  async function loadFastMCP() {
    try {
      const [status, tools] = await Promise.all([Api.fastmcpStatus(), Api.fastmcpTools()]);
      state.fastmcp.status = status;
      state.fastmcp.tools = tools?.items || [];
      renderFastMCPTools();
    } catch (error) {
      state.fastmcp.status = { status: 'error' };
      state.fastmcp.tools = [];
      renderFastMCPTools();
      setText('#mcpRuntime', error.message || 'FastMCP 服务连接失败');
    }
  }

  function fastMCPArguments(tool) {
    const values = compactObject({
      year: number($('#mcpYear').value),
      season: $('#mcpSeason').value,
      start_date: $('#mcpStartDate').value,
      end_date: $('#mcpEndDate').value
    });
    const limit = Math.max(1, Math.min(50, number($('#mcpLimit').value, 6)));
    if (tool === 'analyze_agricultural_risks') values.event_limit = limit;
    if (tool === 'query_weather_observations') values.limit = limit;
    return values;
  }

  function fastMCPSummary(payload) {
    const result = payload?.result || {};
    if (payload?.tool === 'get_weather_overview') {
      const item = result.overview || {};
      return `分析 ${formatInteger(item.record_count)} 条记录：平均气温 ${formatNumber(item.avg_temperature_c)}°C，累计区域降水 ${formatNumber(item.total_precipitation_mm)} mm，日期 ${item.date_start || '--'} 至 ${item.date_end || '--'}。`;
    }
    if (payload?.tool === 'analyze_agricultural_risks') {
      const item = result.summary || {};
      return `发现 ${formatInteger(item.triggered_risk_types)} 类已触发风险、${formatInteger(item.risk_event_count)} 个事件；本次返回 ${formatInteger(result.events_returned)} 条可追溯证据。`;
    }
    if (payload?.tool === 'query_weather_observations') {
      return `查询命中 ${formatInteger(result.total)} 条记录，本页返回 ${(result.items || []).length} 条标准化日值。`;
    }
    return 'FastMCP 调用已完成。';
  }

  async function callFastMCP() {
    const tool = $('#mcpToolSelect').value;
    const button = $('#mcpCallBtn');
    const resultPanel = $('#mcpResult');
    setButtonBusy(button, true, '正在调用 MCP');
    resultPanel.dataset.state = 'loading';
    setText('#mcpTraceId', '建立 MCP 会话…');
    setText('#mcpElapsed', '-- ms');
    setText('#mcpResultSummary', '正在通过 FastMCP Client 发现工具并执行调用。');
    $('#mcpResultJson').textContent = '{ "status": "calling" }';
    try {
      const payload = await Api.fastmcpCall({ tool, arguments: fastMCPArguments(tool) });
      state.fastmcp.lastCall = payload;
      resultPanel.dataset.state = 'success';
      setText('#mcpTraceId', `${payload.trace_id} · audit #${payload.audit_id}`);
      setText('#mcpElapsed', `${formatNumber(payload.elapsed_ms, 2)} ms`);
      setText('#mcpResultSummary', fastMCPSummary(payload));
      $('#mcpResultJson').textContent = JSON.stringify(payload.result, null, 2);
      showToast('FastMCP 调用成功', `${tool} · ${formatNumber(payload.elapsed_ms, 2)} ms`);
    } catch (error) {
      resultPanel.dataset.state = 'error';
      setText('#mcpTraceId', '调用失败');
      setText('#mcpResultSummary', error.message || 'FastMCP 工具调用失败');
      $('#mcpResultJson').textContent = JSON.stringify({ error: error.message }, null, 2);
      showToast('FastMCP 调用失败', error.message, 'error');
    } finally {
      setButtonBusy(button, false);
    }
  }

  async function loadDashboard({ quiet = false, preserveAnalysis = false, contextId = state.contextSequence } = {}) {
    if (!quiet) setLoading(true, '正在加载农田气象数据…');
    const filterSnapshot = { ...state.filters };
    const requests = [Api.overview(filterSnapshot), Api.observations(filterSnapshot), Api.history()];
    const hasActiveFilter = Object.values(filterSnapshot).some(Boolean);
    if (!preserveAnalysis && !hasActiveFilter) requests.push(Api.latestAnalysis());
    const results = await Promise.allSettled(requests);
    if (!isCurrentContext(contextId)) {
      if (!quiet) setLoading(false);
      return false;
    }
    const [overviewResult, observationsResult, historyResult, latestResult] = results;
    if (overviewResult.status === 'fulfilled') {
      state.overview = overviewResult.value;
    } else {
      showToast('概览加载失败', overviewResult.reason.message, 'error');
    }

    if (observationsResult.status === 'fulfilled') {
      const payload = observationsResult.value || {};
      const items = Array.isArray(payload) ? payload : payload.items || payload.observations || payload.data?.items || [];
      state.observations = (Array.isArray(items) ? items : []).map(normalizeObservation);
      state.observationTotal = number(payload.total, state.observations.length);
      state.tableVisible = 10;
    } else {
      state.observations = [];
      state.observationTotal = 0;
      showToast('气象日值加载失败', observationsResult.reason.message, 'error');
    }

    if (historyResult.status === 'fulfilled') state.history = normalizeHistory(historyResult.value);
    if (!preserveAnalysis) {
      const latest = latestResult?.status === 'fulfilled' ? normalizeAnalysis(latestResult.value) : null;
      state.analysis = [latest, ...state.history].filter(Boolean).find(item => analysisMatchesFilters(item, filterSnapshot)) || null;
    }

    updateFilterOptions();
    renderDashboard();
    setText('#lastRefreshTime', new Date().toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit', hour12: false }));
    if (!quiet) setLoading(false);
    return true;
  }

  function renderDashboard() {
    renderAnalysisState();
    renderHero();
    renderProvenance();
    renderKpis();
    renderCharts();
    renderSeasonal();
    renderQuality();
    renderRisks();
    renderConclusions();
    renderHistory();
    renderObservations();
  }

  function renderHero() {
    const overview = normalizeOverview(state.overview);
    const dataset = overview.dataset;
    const result = resultOf();
    const plotCount = firstNumber(dataset, ['plot_count'], new Set(state.observations.map(item => item.plot_id)).size);
    const dateValues = state.observations.map(item => parseDate(item.timestamp)).filter(Boolean).sort((a, b) => a - b);
    const start = firstText(dataset, ['date_start', 'start_date'], dateValues[0]?.toISOString());
    const end = firstText(dataset, ['date_end', 'end_date'], dateValues.at(-1)?.toISOString());
    const source = dataset?.source;
    const isGridProduct = /NASA|POWER|grid/i.test(`${source?.provider || ''} ${source?.dataset_name || ''} ${source?.provenance?.spatial_note || ''}`);
    setText('#heroPlotScope', `${formatInteger(plotCount, '0')} 个${isGridProduct ? '请求网格点' : '空间样本'}`);
    setText('#heroDateScope', start && end ? `${formatDate(start)} — ${formatDate(end)}` : '暂无数据范围');
    const createdAt = state.analysis?.created_at || state.analysis?.completed_at || state.analysis?.timestamp;
    setText('#heroAnalysisTime', createdAt ? formatDate(createdAt, true) : '尚未运行');
    const sourceLink = $('#heroDataSource');
    if (source?.provider) {
      sourceLink.textContent = /NASA|POWER/i.test(source.provider) ? 'NASA POWER · 本地快照可追溯' : `${source.provider} · 来源已登记`;
      sourceLink.href = source.landing_page || '#';
      sourceLink.title = `${source.dataset_name || source.provider}${source.provenance?.retrieved_at ? `；获取于 ${source.provenance.retrieved_at}` : ''}`;
    } else {
      sourceLink.textContent = '用户导入 / 来源未登记';
      sourceLink.removeAttribute('href');
      sourceLink.title = '当前数据库未登记外部来源';
    }

    if (isFailedAnalysis()) {
      const id = analysisIdOf();
      setText('#analysisNarrative', `分析任务${id ? ` #${id}` : ''}执行失败：${analysisFailureMessage()}。当前页面仅保留数据概览，未生成季节统计、风险研判或证据结论。`);
      return;
    }

    const resultOverview = result?.overview || {};
    const resultQuality = result?.quality || {};
    const analyzedRecords = firstNumber(resultOverview, ['record_count'], null);
    if (analyzedRecords !== null) {
      const analyzedPoints = firstNumber(resultOverview, ['plot_count'], plotCount);
      const analyzedDays = firstNumber(resultOverview, ['day_count'], null);
      const analyzedTemperature = firstNumber(resultOverview, ['avg_temperature_c'], null);
      const analyzedRainfall = firstNumber(resultOverview, ['total_precipitation_mm'], null);
      const qualityScore = firstNumber(resultQuality, ['score', 'quality_score'], null);
      const scopeNoun = isGridProduct ? '请求网格点' : '空间样本';
      const metrics = [
        analyzedTemperature !== null && `区域日均温度 ${formatNumber(analyzedTemperature, 1)}°C`,
        analyzedRainfall !== null && `区域平均累计降水 ${formatNumber(analyzedRainfall, 1)} mm`,
        qualityScore !== null && `入库规则校验 ${formatNumber(qualityScore, 1)} 分`
      ].filter(Boolean).join('，');
      setText('#analysisNarrative', `当前范围已分析 ${formatInteger(analyzedRecords)} 条、${formatInteger(analyzedPoints)} 个${scopeNoun}${analyzedDays !== null ? `、${formatInteger(analyzedDays)} 天` : ''}；${metrics}。质量分反映缺测、重复与数值范围规则，不代表地面测量准确率。`);
    } else {
      const summary = typeof state.analysis?.summary === 'string' ? state.analysis.summary
        : firstText(result, ['summary', 'overview.summary', 'narrative']) || firstText(state.analysis?.summary, ['text', 'content']);
      if (summary) setText('#analysisNarrative', summary.replaceAll('地块', '空间样本').replaceAll('数据质量', '入库质量'));
      else if (state.observationTotal) setText('#analysisNarrative', `已载入 ${integerFormatter.format(state.observationTotal)} 条气象日值。运行分析可生成季节特征、异常风险与证据结论。`);
      else setText('#analysisNarrative', '尚未发现气象日值，请先导入符合字段规范的 CSV 文件。');
    }
  }

  function renderProvenance() {
    const dataset = normalizeOverview(state.overview).dataset || {};
    const source = dataset.source || {};
    const provenance = source.provenance || {};
    const stateElement = $('#sourceState');
    const hasRegisteredSource = Boolean(source.provider || source.dataset_name || provenance.source_key);

    if (!hasRegisteredSource) {
      stateElement.dataset.state = 'unregistered';
      setText('#sourceVerification', '来源未登记');
      setText('#sourceDatasetName', '当前数据集没有外部来源登记；导入数据时应另附采集方式与原始文件说明。');
      setText('#sourceProvider', '用户导入 / 本地数据');
      setText('#sourceApiVersion', '未登记接口信息');
      setText('#sourceProductType', '未登记（当前数据源未提供产品类型）');
      setText('#sourceOfficialProducts', '产品类型未登记');
      setText('#sourcePointCount', `${formatInteger(dataset.plot_count, '0')} 个空间样本`);
      setText('#sourceRecordCount', `${formatInteger(dataset.total_records, '0')} 条记录`);
      setText('#sourcePeriod', dataset.date_start && dataset.date_end ? `${formatDate(dataset.date_start)} — ${formatDate(dataset.date_end)}` : '--');
      setText('#sourceTimeStandard', '时间标准未登记');
      setText('#sourceRetrievedAt', '--');
      setText('#sourceImportTime', dataset.last_import?.created_at ? `入库 ${formatFullDateTime(dataset.last_import.created_at)}` : '入库时间 --');
      setText('#sourceSpatialNote', '当前页面仅能确认本地数据记录，现有信息不足以推断采集设备、地面站或外部产品来源。');
      setText('#sourceHash', dataset.last_import?.sha256 ? `CSV SHA-256 ${dataset.last_import.sha256}` : 'SHA-256 未登记');
      setText('#sourceRawFiles', '原始快照未登记');
      ['#sourceLandingLink', '#sourceDocsLink', '#sourceQueryLink'].forEach(selector => setExternalLink(selector, ''));
      return;
    }

    const points = Array.isArray(provenance.points) ? provenance.points : [];
    const rawFiles = Array.isArray(provenance.raw_files) ? provenance.raw_files : [];
    const normalizedFile = provenance.normalized_file || {};
    const valueVerification = provenance.value_verification || {};
    const valuesVerified = String(valueVerification.result || '').toLowerCase() === 'passed'
      && firstNumber(valueVerification, ['parameter_value_mismatches'], 1) === 0;
    const verifiedValueCount = firstNumber(valueVerification, ['parameter_day_values_compared'], null);
    const normalizedHash = normalizedFile.sha256 || dataset.last_import?.sha256 || '';
    const importedHash = dataset.last_import?.sha256 || '';
    const hashesAligned = Boolean(normalizedFile.sha256 && importedHash && normalizedFile.sha256 === importedHash);
    const pointRecordTotal = points.reduce((sum, point) => sum + (number(point?.record_count, 0) || 0), 0);
    const isNasaPower = /NASA|POWER/i.test(`${source.provider || ''} ${source.dataset_name || ''}`);
    const apiName = provenance.api_name || points[0]?.header?.api?.name || '接口未登记';
    const apiVersion = provenance.api_version || points[0]?.header?.api?.version || '';
    const period = provenance.period || {};
    const productSources = Array.isArray(provenance.official_sources) ? provenance.official_sources.join(' / ') : '';
    const recordTotal = number(dataset.total_records, normalizedFile.records);
    const countConsistent = Boolean(recordTotal && (!points.length || pointRecordTotal === recordTotal) && (!normalizedFile.records || normalizedFile.records === recordTotal));

    stateElement.dataset.state = 'registered';
    setText('#sourceVerification', valuesVerified && verifiedValueCount !== null ? `${formatInteger(verifiedValueCount)} 项数值复核一致` : normalizedHash ? '来源已登记 · 哈希可复核' : '来源已登记');
    stateElement.setAttribute('aria-label', `核验状态：${$('#sourceVerification').textContent}`);
    setText('#sourceDatasetName', source.dataset_name || provenance.dataset_name || source.provider);
    setText('#sourceProvider', source.provider || provenance.provider || '--');
    setText('#sourceApiVersion', `${apiName}${apiVersion ? ` ${apiVersion}` : ''}`);
    setText('#sourceProductType', isNasaPower ? '卫星 / 再分析网格日值' : '已登记外部数据产品');
    setText('#sourceOfficialProducts', productSources || '产品来源标识未登记');
    setText('#sourcePointCount', `${formatInteger(points.length || dataset.plot_count, '0')} 个${isNasaPower ? '请求网格点' : '空间样本'}`);
    setText('#sourceRecordCount', `${formatInteger(recordTotal, '0')} 条${countConsistent ? ' · 计数一致' : ''}`);
    setText('#sourcePeriod', period.start && period.end ? `${formatDate(period.start)} — ${formatDate(period.end)}` : dataset.date_start && dataset.date_end ? `${formatDate(dataset.date_start)} — ${formatDate(dataset.date_end)}` : '--');
    setText('#sourceTimeStandard', `${provenance.time_standard || '未登记'}${period.days ? ` · ${formatInteger(period.days)} 天` : ''}`);
    setText('#sourceRetrievedAt', formatFullDateTime(provenance.retrieved_at));
    setText('#sourceImportTime', dataset.last_import?.created_at ? `入库 ${formatFullDateTime(dataset.last_import.created_at)}` : '入库时间 --');
    const boundaryNote = isNasaPower
      ? 'NASA POWER 是请求坐标对应的卫星/再分析网格产品，不是田间传感器或地面气象站实测。本数据中龙岗与大鹏全年四项序列相同，属于原生空间分辨率效应，因此不应按独立站序列解读。'
      : provenance.spatial_note || '该数据来自已登记外部产品，空间代表性需结合产品说明判断。';
    setText('#sourceSpatialNote', boundaryNote);
    const integrityStatus = hashesAligned ? 'CSV 登记哈希与入库哈希一致' : normalizedHash ? 'CSV 哈希已登记，可离线复核' : 'CSV 哈希未登记';
    setText('#sourceHash', normalizedHash ? `CSV SHA-256 ${normalizedHash}` : 'SHA-256 --');
    $('#sourceHash').title = integrityStatus;
    setText('#sourceRawFiles', rawFiles.length ? `${rawFiles.length} 份${provenance.raw_capture_mode === 'normalized_json_snapshot' ? '规范化 JSON 快照' : '原始响应快照'} · 哈希已登记` : integrityStatus);
    setExternalLink('#sourceLandingLink', source.landing_page || provenance.landing_page, '官方主页 ↗');
    setExternalLink('#sourceDocsLink', provenance.api_documentation, 'API 文档 ↗');
    setExternalLink('#sourceQueryLink', Array.isArray(provenance.query_urls) ? provenance.query_urls[0] : source.query_urls?.[0], '查询样例 ↗');
  }

  function renderKpis() {
    const overview = normalizeOverview(state.overview);
    const kpis = overview.kpis;
    const quality = overview.quality;
    const validObservations = state.observations.filter(isValidObservation);
    const temperatures = validObservations.map(item => item.temperature_c).filter(Number.isFinite);
    const solar = validObservations.map(item => item.solar_radiation_wm2).filter(Number.isFinite);
    const regionalDays = aggregateObservations(validObservations, 'day');
    const regionalTemperatures = regionalDays.map(item => item.temperature).filter(Number.isFinite);
    const regionalRainfall = regionalDays.map(item => item.rainfall).filter(Number.isFinite);
    const regionalSolar = regionalDays.map(item => item.solar).filter(Number.isFinite);
    const dates = new Set(regionalDays.map(item => item.label));

    const avgTemperature = firstNumber(kpis, ['avg_temperature_c', 'average_temperature_c'], average(regionalTemperatures));
    const totalRain = firstNumber(kpis, ['total_precipitation_mm', 'precipitation_total_mm'], regionalRainfall.length ? regionalRainfall.reduce((sum, value) => sum + value, 0) : null);
    const avgSolar = firstNumber(kpis, ['avg_solar_radiation_wm2', 'average_solar_radiation_wm2'], average(regionalSolar));
    const rainyDays = firstNumber(kpis, ['rainy_days'], regionalDays.length ? regionalDays.filter(item => Number.isFinite(item.rainfall) && item.rainfall >= .1).length : null);
    let score = firstNumber(quality, ['score', 'quality_score'], null);
    if (score !== null && score <= 1) score *= 100;

    setText('#avgTemperature', formatNumber(avgTemperature, 1));
    setText('#totalRainfall', formatNumber(totalRain, 1));
    setText('#avgSolar', formatNumber(avgSolar, 0));
    setText('#qualityScore', formatNumber(score, 1));
    setText('#rainyDayBadge', rainyDays === null ? '日均≥0.1 mm · --' : `日均≥0.1 mm · ${formatInteger(rainyDays, '0')} 天`);
    setText('#temperatureRange', temperatures.length ? `范围 ${formatNumber(Math.min(...temperatures), 1)}–${formatNumber(Math.max(...temperatures), 1)}°C` : '范围 --');
    setText('#rainfallDaily', dates.size && totalRain !== null ? `期间日均 ${formatNumber(totalRain / dates.size, 1)} mm` : '期间日均 --');
    setText('#solarPeak', solar.length ? `样本日值峰值 ${formatNumber(Math.max(...solar), 0)} W/m²` : '样本日值峰值 --');
    const valid = firstNumber(quality, ['valid_records'], state.observations.filter(item => /valid|good|ok|正常/i.test(item.quality_flag)).length);
    setText('#validRecords', `${formatInteger(valid, '0')} 条有效记录`);
    const label = score === null ? '入库校验 --' : score >= 90 ? '规则校验 优' : score >= 75 ? '规则校验 良' : score >= 60 ? '规则校验 中' : '入库数据待治理';
    setText('#qualityLabel', label);

    const activeRules = normalizeRisks().filter(isTriggeredRisk);
    const riskEvents = Array.isArray(resultOf().risk_events) ? resultOf().risk_events : activeRules.flatMap(risk => Array.isArray(risk.events) ? risk.events : []);
    const eventCount = riskEvents.length || activeRules.reduce((sum, risk) => sum + (firstNumber(risk, ['event_count', 'metric'], 1) || 0), 0);
    const analysisReady = isCompletedAnalysis();
    setText('#activeRiskKpi', analysisReady ? formatInteger(eventCount, '0') : '--');
    setText('#riskRuleBadge', isFailedAnalysis() ? '未研判' : `${activeRules.length} 类规则`);
    setText('#riskKpiFoot', isFailedAnalysis() ? '分析失败，未生成风险结果' : !state.analysis ? '等待分析' : analysisReady && activeRules.length ? `筛选期累计 · ${activeRules.length} 类风险` : analysisReady ? '筛选期内未触发' : '等待任务完成');

    const timeSeries = aggregateObservations(state.observations, state.granularity);
    renderSpark('#temperatureSpark', timeSeries.map(item => item.temperature), '#b85f47');
    renderSpark('#rainSpark', timeSeries.map(item => item.rainfall), '#4f8f9c');
    renderSpark('#solarSpark', timeSeries.map(item => item.solar), '#dfa632');
    renderSpark('#qualitySpark', score === null ? [] : [Math.max(0, score - 10), score - 5, score - 3, score], '#477c57');
    renderSpark('#riskSpark', activeRules.map(risk => firstNumber(risk, ['event_count', 'metric'], 0)), '#a15c27');
  }

  function renderSpark(selector, values, color) {
    const element = $(selector);
    const data = values.filter(Number.isFinite).slice(-20);
    if (!element || data.length < 2) { if (element) element.innerHTML = ''; return; }
    const min = Math.min(...data), max = Math.max(...data), range = max - min || 1;
    const points = data.map((value, index) => `${(index / (data.length - 1) * 62 + 1).toFixed(1)},${(16 - (value - min) / range * 13).toFixed(1)}`).join(' ');
    element.innerHTML = `<svg viewBox="0 0 64 18" preserveAspectRatio="none"><polyline points="${points}" fill="none" stroke="${color}" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/><path d="M1 17H63" stroke="#e1e5dd" stroke-width="1"/></svg>`;
  }

  function aggregateObservations(observations, granularity = 'month') {
    const dailyGroups = new Map();
    observations.filter(isValidObservation).forEach(item => {
      const date = parseDate(item.timestamp);
      if (!date) return;
      const dayKey = `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}`;
      if (!dailyGroups.has(dayKey)) dailyGroups.set(dayKey, { label: dayKey, temperatures: [], rainfall: [], solar: [], humidity: [], count: 0 });
      const group = dailyGroups.get(dayKey);
      if (Number.isFinite(item.temperature_c)) group.temperatures.push(item.temperature_c);
      if (Number.isFinite(item.precipitation_mm)) group.rainfall.push(item.precipitation_mm);
      if (Number.isFinite(item.solar_radiation_wm2)) group.solar.push(item.solar_radiation_wm2);
      if (Number.isFinite(item.humidity_pct)) group.humidity.push(item.humidity_pct);
      group.count += 1;
    });
    const daily = Array.from(dailyGroups.values()).sort((a, b) => a.label.localeCompare(b.label)).map(group => ({
      label: group.label,
      temperature: average(group.temperatures),
      rainfall: average(group.rainfall),
      solar: average(group.solar),
      humidity: average(group.humidity),
      count: group.count
    }));
    if (granularity === 'day') return daily;

    const periodGroups = new Map();
    daily.forEach(item => {
      const key = item.label.slice(0, 7);
      if (!periodGroups.has(key)) periodGroups.set(key, { label: key, temperatures: [], rainfall: 0, solar: [], humidity: [], count: 0 });
      const group = periodGroups.get(key);
      if (Number.isFinite(item.temperature)) group.temperatures.push(item.temperature);
      if (Number.isFinite(item.rainfall)) group.rainfall += item.rainfall;
      if (Number.isFinite(item.solar)) group.solar.push(item.solar);
      if (Number.isFinite(item.humidity)) group.humidity.push(item.humidity);
      group.count += item.count;
    });
    return Array.from(periodGroups.values()).sort((a, b) => a.label.localeCompare(b.label)).map(group => ({
      label: group.label,
      temperature: average(group.temperatures),
      rainfall: group.rainfall,
      solar: average(group.solar),
      humidity: average(group.humidity),
      count: group.count
    }));
  }

  function monthlyFromAnalysis() {
    const raw = resultOf().monthly || resultOf().monthly_stats || resultOf().time_series?.monthly;
    if (!Array.isArray(raw) || !raw.length) return [];
    return raw.map(item => ({
      label: String(item.month || item.period || item.date || item.label || ''),
      temperature: firstNumber(item, ['avg_temperature_c', 'temperature', 'avg_temp']),
      rainfall: firstNumber(item, ['total_precipitation_mm', 'rainfall', 'precipitation'], 0),
      solar: firstNumber(item, ['avg_solar_radiation_wm2', 'solar', 'sunlight']),
      humidity: firstNumber(item, ['avg_humidity_pct', 'humidity'])
    })).filter(item => item.label).sort((a, b) => a.label.localeCompare(b.label));
  }

  function chartSeries() {
    if (state.granularity === 'month') {
      const analysisSeries = monthlyFromAnalysis();
      if (analysisSeries.length) return analysisSeries;
    }
    return aggregateObservations(state.observations, state.granularity);
  }

  function renderCharts() {
    const series = chartSeries();
    const temperatures = series.map(item => item.temperature).filter(Number.isFinite);
    const rainfall = series.map(item => item.rainfall).filter(Number.isFinite);
    const solar = series.map(item => item.solar).filter(Number.isFinite);
    setText('#temperatureAmplitude', temperatures.length ? `${formatNumber(Math.max(...temperatures) - Math.min(...temperatures), 1)} °C` : '-- °C');
    const trend = resultOf().trends || {};
    const rainDirection = trendDirection(trend, 'precipitation');
    const solarDirection = trendDirection(trend, 'solar');
    setText('#rainPatternTag', rainDirection || describeDistribution(rainfall, '降水'));
    setText('#solarPatternTag', solarDirection || describeDistribution(solar, '光照'));
    setText('#temperatureTrend', trendDirection(trend, 'temperature') || '统计期均值');

    drawTimeChart('temperatureChart', series, {
      field: 'temperature', unit: '°C', color: '#b85f47', type: 'line', decimals: 1,
      tooltip: value => `${formatNumber(value, 1)} °C`
    });
    drawTimeChart('rainfallChart', series, {
      field: 'rainfall', unit: 'mm', color: '#4f8f9c', type: 'bar', decimals: 1, zeroBased: true,
      tooltip: value => `${formatNumber(value, 1)} mm`
    });
    drawTimeChart('solarChart', series, {
      field: 'solar', unit: 'W/m²', color: '#dfa632', type: 'area', decimals: 0, zeroBased: true,
      tooltip: value => `${formatNumber(value, 0)} W/m²`
    });
  }

  function trendDirection(trends, key) {
    const aliases = key === 'precipitation' ? ['precipitation_mm', 'precipitation', 'rainfall_mm', 'rainfall', 'rain'] : key === 'solar' ? ['solar_radiation_wm2', 'solar_radiation', 'solar', 'sunlight', 'radiation'] : ['temperature_c', 'temperature', 'temp'];
    let source;
    for (const alias of aliases) {
      source = trends?.[alias] || trends?.[`${alias}_trend`];
      if (source !== undefined) break;
    }
    let direction = typeof source === 'string' ? source : source?.direction || source?.label || source?.trend;
    if (!direction) {
      for (const alias of aliases) {
        const slope = firstNumber(trends, [`${alias}_slope`, `${alias}.slope`]);
        if (slope !== null) { direction = slope > .01 ? '上升趋势' : slope < -.01 ? '下降趋势' : '总体平稳'; break; }
      }
    }
    const map = { increasing: '上升趋势', rising: '上升趋势', upward: '上升趋势', '上升': '上升趋势', decreasing: '下降趋势', falling: '下降趋势', downward: '下降趋势', '下降': '下降趋势', stable: '总体平稳', flat: '总体平稳', '平稳': '总体平稳' };
    return direction ? (map[String(direction).toLowerCase()] || String(direction)) : '';
  }

  function describeDistribution(values, label) {
    if (!values.length) return '等待数据';
    const max = Math.max(...values), mean = average(values) || 0;
    if (mean && max > mean * 2.5) return `${label}峰值集中`;
    return `${label}分布平稳`;
  }

  function cssColorWithAlpha(hex, alpha) {
    const clean = hex.replace('#', '');
    const value = clean.length === 3 ? clean.split('').map(char => char + char).join('') : clean;
    const red = parseInt(value.slice(0, 2), 16), green = parseInt(value.slice(2, 4), 16), blue = parseInt(value.slice(4, 6), 16);
    return `rgba(${red},${green},${blue},${alpha})`;
  }

  function prepareCanvas(canvas) {
    const rect = canvas.getBoundingClientRect();
    if (rect.width < 10 || rect.height < 10) return null;
    const ratio = Math.min(window.devicePixelRatio || 1, 2);
    canvas.width = Math.round(rect.width * ratio);
    canvas.height = Math.round(rect.height * ratio);
    const context = canvas.getContext('2d');
    context.setTransform(ratio, 0, 0, ratio, 0, 0);
    context.clearRect(0, 0, rect.width, rect.height);
    return { context, width: rect.width, height: rect.height, ratio };
  }

  function drawTimeChart(canvasId, rawSeries, options) {
    const canvas = document.getElementById(canvasId);
    if (!canvas) return;
    const data = rawSeries.filter(item => Number.isFinite(item[options.field]));
    const empty = $(`[data-empty-for="${canvasId}"]`);
    empty.hidden = data.length > 0;
    if (!data.length) {
      const prepared = prepareCanvas(canvas);
      if (prepared) prepared.context.clearRect(0, 0, prepared.width, prepared.height);
      state.chartModels[canvasId] = null;
      return;
    }
    const prepared = prepareCanvas(canvas);
    if (!prepared) return;
    const { context: ctx, width, height } = prepared;
    const margin = { left: width < 440 ? 42 : 50, right: 15, top: 12, bottom: 31 };
    const plotWidth = width - margin.left - margin.right;
    const plotHeight = height - margin.top - margin.bottom;
    const values = data.map(item => item[options.field]);
    let minValue = options.zeroBased ? 0 : Math.min(...values);
    let maxValue = Math.max(...values);
    if (!options.zeroBased) {
      const pad = (maxValue - minValue || Math.max(Math.abs(maxValue), 1)) * .14;
      minValue -= pad; maxValue += pad;
    } else maxValue = maxValue <= 0 ? 1 : maxValue * 1.14;
    const range = maxValue - minValue || 1;
    const xAt = index => margin.left + (data.length === 1 ? plotWidth / 2 : index / (data.length - 1) * plotWidth);
    const yAt = value => margin.top + (maxValue - value) / range * plotHeight;

    ctx.font = '9px "Segoe UI", sans-serif';
    ctx.textBaseline = 'middle';
    ctx.lineWidth = 1;
    const ticks = 4;
    for (let index = 0; index <= ticks; index += 1) {
      const y = margin.top + index / ticks * plotHeight;
      const value = maxValue - index / ticks * range;
      ctx.beginPath(); ctx.setLineDash([3, 4]); ctx.strokeStyle = '#e2e6de'; ctx.moveTo(margin.left, y); ctx.lineTo(width - margin.right, y); ctx.stroke();
      ctx.setLineDash([]); ctx.fillStyle = '#7a867e'; ctx.textAlign = 'right';
      ctx.fillText(formatAxisValue(value, options.decimals), margin.left - 7, y);
    }
    ctx.beginPath(); ctx.strokeStyle = '#cfd6ca'; ctx.moveTo(margin.left, margin.top + plotHeight); ctx.lineTo(width - margin.right, margin.top + plotHeight); ctx.stroke();

    const maxLabels = width < 420 ? 4 : width < 700 ? 6 : 8;
    const labelStep = Math.max(1, Math.ceil(data.length / maxLabels));
    data.forEach((item, index) => {
      if (index % labelStep !== 0 && index !== data.length - 1) return;
      ctx.fillStyle = '#7a867e'; ctx.textAlign = index === 0 ? 'left' : index === data.length - 1 ? 'right' : 'center';
      ctx.fillText(formatPeriodLabel(item.label), xAt(index), height - 12);
    });

    const points = data.map((item, index) => ({ x: xAt(index), y: yAt(item[options.field]), value: item[options.field], label: item.label }));
    if (options.type === 'bar') {
      const slot = data.length > 1 ? plotWidth / (data.length - 1) : plotWidth;
      const barWidth = Math.max(2, Math.min(18, slot * .62));
      points.forEach(point => {
        const barHeight = margin.top + plotHeight - point.y;
        const gradient = ctx.createLinearGradient(0, point.y, 0, margin.top + plotHeight);
        gradient.addColorStop(0, options.color); gradient.addColorStop(1, cssColorWithAlpha(options.color, .46));
        ctx.fillStyle = gradient;
        roundedRect(ctx, point.x - barWidth / 2, point.y, barWidth, Math.max(barHeight, 1), Math.min(4, barWidth / 2));
        ctx.fill();
      });
    } else {
      if (options.type === 'area' || options.type === 'line') {
        const gradient = ctx.createLinearGradient(0, margin.top, 0, margin.top + plotHeight);
        gradient.addColorStop(0, cssColorWithAlpha(options.color, options.type === 'area' ? .34 : .18));
        gradient.addColorStop(1, cssColorWithAlpha(options.color, .015));
        ctx.beginPath(); ctx.moveTo(points[0].x, margin.top + plotHeight); points.forEach(point => ctx.lineTo(point.x, point.y)); ctx.lineTo(points.at(-1).x, margin.top + plotHeight); ctx.closePath(); ctx.fillStyle = gradient; ctx.fill();
      }
      ctx.beginPath(); points.forEach((point, index) => index ? ctx.lineTo(point.x, point.y) : ctx.moveTo(point.x, point.y));
      ctx.strokeStyle = options.color; ctx.lineWidth = 2; ctx.lineJoin = 'round'; ctx.lineCap = 'round'; ctx.stroke();
      if (points.length <= 36) points.forEach(point => { ctx.beginPath(); ctx.arc(point.x, point.y, 2.3, 0, Math.PI * 2); ctx.fillStyle = '#fff'; ctx.fill(); ctx.strokeStyle = options.color; ctx.lineWidth = 1.5; ctx.stroke(); });
    }
    state.chartModels[canvasId] = { canvas, points, options, margin, plotHeight, baseY: margin.top + plotHeight };
    canvas.setAttribute('aria-label', `${options.field === 'temperature' ? '温度' : options.field === 'rainfall' ? '降水' : '光照'}时序图，共 ${data.length} 个统计周期，最低 ${formatAxisValue(Math.min(...values), options.decimals)}，最高 ${formatAxisValue(Math.max(...values), options.decimals)} ${options.unit}`);
    bindChartPointer(canvasId);
  }

  function formatAxisValue(value, decimals = 0) {
    if (Math.abs(value) >= 1000) return `${formatNumber(value / 1000, 1)}k`;
    return formatNumber(value, decimals);
  }

  function formatPeriodLabel(label) {
    const value = String(label || '');
    const monthMatch = value.match(/^(\d{4})[-/]?(\d{1,2})$/);
    if (monthMatch) return `${monthMatch[1].slice(2)}.${String(monthMatch[2]).padStart(2, '0')}`;
    const date = parseDate(value);
    if (date) return state.granularity === 'day' ? `${date.getMonth() + 1}/${date.getDate()}` : `${String(date.getFullYear()).slice(2)}.${String(date.getMonth() + 1).padStart(2, '0')}`;
    return value.length > 8 ? value.slice(0, 8) : value;
  }

  function roundedRect(ctx, x, y, width, height, radius) {
    const r = Math.min(radius, Math.abs(width) / 2, Math.abs(height) / 2);
    ctx.beginPath(); ctx.moveTo(x + r, y); ctx.arcTo(x + width, y, x + width, y + height, r); ctx.arcTo(x + width, y + height, x, y + height, r); ctx.arcTo(x, y + height, x, y, r); ctx.arcTo(x, y, x + width, y, r); ctx.closePath();
  }

  function bindChartPointer(canvasId) {
    const model = state.chartModels[canvasId];
    if (!model || model.canvas.dataset.pointerBound) return;
    model.canvas.dataset.pointerBound = 'true';
    const move = event => {
      const latest = state.chartModels[canvasId];
      if (!latest?.points?.length) return;
      const rect = latest.canvas.getBoundingClientRect();
      const x = event.clientX - rect.left;
      const nearest = latest.points.reduce((best, point) => Math.abs(point.x - x) < Math.abs(best.x - x) ? point : best, latest.points[0]);
      const tooltip = document.getElementById(`${canvasId.replace('Chart', '')}Tooltip`);
      if (!tooltip) return;
      tooltip.hidden = false;
      tooltip.innerHTML = `<span>${escapeHtml(formatPeriodForTooltip(nearest.label))}</span><strong>${escapeHtml(latest.options.tooltip(nearest.value))}</strong>`;
      tooltip.style.left = `${Math.max(60, Math.min(rect.width - 60, nearest.x))}px`;
      tooltip.style.top = `${Math.max(52, nearest.y)}px`;
    };
    model.canvas.addEventListener('pointermove', move);
    model.canvas.addEventListener('pointerleave', () => {
      const tooltip = document.getElementById(`${canvasId.replace('Chart', '')}Tooltip`);
      if (tooltip) tooltip.hidden = true;
    });
  }

  function formatPeriodForTooltip(value) {
    const date = parseDate(value);
    if (date) return state.granularity === 'day' ? formatDate(date) : `${date.getFullYear()} 年 ${date.getMonth() + 1} 月`;
    const match = String(value).match(/^(\d{4})[-/]?(\d{1,2})$/);
    return match ? `${match[1]} 年 ${Number(match[2])} 月` : String(value);
  }

  function renderSeasonal() {
    const seasonal = normalizeSeasonal();
    Object.keys(seasonMeta).forEach(key => {
      const card = $(`.season-card[data-season="${key}"]`);
      const item = seasonal[key];
      if (!card) return;
      setText($('[data-metric="temperature"]', card), item?.temperature === null || item?.temperature === undefined ? '--' : `${formatNumber(item.temperature, 1)}°C`);
      setText($('[data-metric="rainfall"]', card), item?.rainfall === null || item?.rainfall === undefined ? '--' : `${formatNumber(item.rainfall, 1)}mm`);
      setText($('[data-metric="solar"]', card), item?.solar === null || item?.solar === undefined ? '--' : `${formatNumber(item.solar, 0)}W/m²`);
      setText($('[data-metric="feature"]', card), item?.feature || seasonalFeature(item, key));
    });
    drawSeasonChart(seasonal);
  }

  function seasonalFeature(item, key) {
    if (isFailedAnalysis()) return '分析失败，未生成季节统计';
    if (!item) return '当前范围暂无该季节数据';
    const meta = seasonMeta[key];
    if (item.rainyDays !== null && item.rainyDays !== undefined) return `${meta.name}共记录 ${formatInteger(item.rainyDays)} 个雨日`;
    if (item.count) return `${meta.name}纳入 ${formatInteger(item.count)} 条气象日值`;
    return `${meta.name}气象特征已统计`;
  }

  function drawSeasonChart(seasonal) {
    const canvas = $('#seasonChart');
    const empty = $('[data-empty-for="seasonChart"]');
    const config = metricConfig[state.seasonMetric];
    const data = Object.keys(seasonMeta).map(key => ({ key, label: seasonMeta[key].name, value: seasonal[key]?.[config.field], color: seasonMeta[key].color })).filter(item => Number.isFinite(item.value));
    empty.textContent = isFailedAnalysis() ? '该分析任务失败，未生成四季对比' : '运行分析后展示四季对比';
    empty.hidden = data.length > 0;
    if (!data.length) { const prepared = prepareCanvas(canvas); if (prepared) prepared.context.clearRect(0, 0, prepared.width, prepared.height); state.chartModels.seasonChart = null; return; }
    const prepared = prepareCanvas(canvas);
    if (!prepared) return;
    const { context: ctx, width, height } = prepared;
    const margin = { left: 48, right: 17, top: 18, bottom: 37 };
    const plotWidth = width - margin.left - margin.right, plotHeight = height - margin.top - margin.bottom;
    let min = state.seasonMetric === 'temperature' ? Math.min(0, ...data.map(item => item.value)) : 0;
    let max = Math.max(...data.map(item => item.value));
    if (min < 0) { const pad = (max - min) * .08; min -= pad; max += pad; } else max = max <= 0 ? 1 : max * 1.14;
    const range = max - min || 1;
    const slot = plotWidth / 4, barWidth = Math.min(48, slot * .46);
    ctx.font = '9px "Segoe UI", sans-serif';
    for (let tick = 0; tick <= 4; tick += 1) {
      const y = margin.top + tick / 4 * plotHeight;
      const value = max - tick / 4 * range;
      ctx.beginPath(); ctx.setLineDash([3, 4]); ctx.strokeStyle = '#e2e6de'; ctx.moveTo(margin.left, y); ctx.lineTo(width - margin.right, y); ctx.stroke(); ctx.setLineDash([]);
      ctx.fillStyle = '#7a867e'; ctx.textAlign = 'right'; ctx.textBaseline = 'middle'; ctx.fillText(formatAxisValue(value, state.seasonMetric === 'solar' ? 0 : 1), margin.left - 7, y);
    }
    const zeroY = margin.top + (max / range) * plotHeight;
    ctx.beginPath(); ctx.strokeStyle = '#cfd6ca'; ctx.moveTo(margin.left, zeroY); ctx.lineTo(width - margin.right, zeroY); ctx.stroke();
    const points = [];
    Object.keys(seasonMeta).forEach((key, index) => {
      const item = data.find(entry => entry.key === key);
      const x = margin.left + slot * index + slot / 2;
      ctx.fillStyle = '#59695f'; ctx.textAlign = 'center'; ctx.textBaseline = 'middle'; ctx.fillText(seasonMeta[key].name, x, height - 14);
      if (!item) return;
      const y = margin.top + (max - item.value) / range * plotHeight;
      const top = Math.min(y, zeroY), barHeight = Math.max(2, Math.abs(zeroY - y));
      const gradient = ctx.createLinearGradient(0, top, 0, top + barHeight);
      gradient.addColorStop(0, item.color); gradient.addColorStop(1, cssColorWithAlpha(item.color, .52));
      ctx.fillStyle = gradient; roundedRect(ctx, x - barWidth / 2, top, barWidth, barHeight, 7); ctx.fill();
      ctx.fillStyle = '#31463a'; ctx.font = '600 9px "Segoe UI", sans-serif'; ctx.fillText(`${formatNumber(item.value, state.seasonMetric === 'solar' ? 0 : 1)}`, x, Math.max(9, top - 8));
      points.push({ x, y: top, value: item.value, label: item.label });
    });
    state.chartModels.seasonChart = { canvas, points, options: { tooltip: value => `${formatNumber(value, state.seasonMetric === 'solar' ? 0 : 1)} ${config.unit}` } };
    canvas.setAttribute('aria-label', `四季${config.label}对比图，单位 ${config.unit}`);
    bindChartPointer('seasonChart');
  }

  function qualityData() {
    const overviewQuality = normalizeOverview(state.overview).quality;
    const analysisQuality = resultOf().quality || resultOf().data_quality || {};
    return { ...overviewQuality, ...analysisQuality };
  }

  function renderQuality() {
    const quality = qualityData();
    let score = firstNumber(quality, ['score', 'quality_score'], null);
    if (score !== null && score <= 1) score *= 100;
    score = score === null ? null : Math.max(0, Math.min(100, score));
    const ring = $('#qualityRing');
    ring.style.setProperty('--score', score || 0);
    const ringCircle = $('.quality-ring__value', ring);
    if (score !== null) ringCircle.style.stroke = score >= 85 ? '#65956d' : score >= 65 ? '#d0a039' : '#b85a43';
    setText('#qualityRingValue', formatNumber(score, 1));
    setText('#missingCount', formatInteger(firstNumber(quality, ['missing_values', 'missing_count'], 0), '0'));
    setText('#suspectCount', formatInteger(firstNumber(quality, ['suspect_records', 'suspect_count'], 0), '0'));
    setText('#duplicateCount', formatInteger(firstNumber(quality, ['duplicate_records', 'duplicate_count'], 0), '0'));
    const anomaliesRaw = resultOf().anomalies || state.analysis?.anomalies;
    const anomalyTotal = firstNumber(anomaliesRaw, ['total', 'count'], normalizeAnomalies().length);
    setText('#anomalyCount', formatInteger(anomalyTotal, '0'));
    const status = score === null ? '等待数据' : score >= 90 ? '规则校验优秀' : score >= 75 ? '规则校验良好' : score >= 60 ? '建议治理' : '需优先治理';
    setText('#qualityStatusTag', status);
    const missing = firstNumber(quality, ['missing_values', 'missing_count'], 0);
    const duplicate = firstNumber(quality, ['duplicate_records', 'duplicate_count'], 0);
    const suspect = firstNumber(quality, ['suspect_records', 'suspect_count'], 0);
    const note = score === null ? '导入或加载数据后，将自动检查字段完整性、重复记录与数值范围；该分数不衡量产品测量误差。'
      : missing + duplicate + suspect === 0 ? '当前范围通过缺测、重复与数值范围规则校验，可用于季节统计；100 分不等同地面测量准确率。'
      : `共发现 ${formatInteger(missing + duplicate + suspect)} 项入库质量问题，分析结论已结合质量标记；该分数不衡量产品测量误差。`;
    setText($('#qualityNote p'), note);
  }

  function normalizeAnomalies() {
    const raw = resultOf().anomalies || state.analysis?.anomalies || [];
    if (Array.isArray(raw)) return raw;
    if (raw && Array.isArray(raw.items)) return raw.items;
    if (raw && typeof raw === 'object') return Object.values(raw).flat().filter(Boolean);
    return [];
  }

  function normalizeRisks() {
    const raw = resultOf().risks || resultOf().alerts || state.analysis?.risks || [];
    if (Array.isArray(raw)) return raw;
    if (raw && Array.isArray(raw.items)) return raw.items;
    if (raw && typeof raw === 'object') return Object.entries(raw).map(([type, item]) => typeof item === 'object' ? { type, ...item } : { type, description: String(item) });
    return [];
  }

  function riskLevel(risk) {
    const value = String(risk.level || risk.severity || risk.risk_level || '').toLowerCase();
    if (['high', 'severe', 'critical', '高', '高风险', '严重'].includes(value)) return 'high';
    if (['low', 'minor', '低', '低风险'].includes(value)) return 'low';
    return 'medium';
  }

  function riskIcon(type) {
    const value = String(type || '').toLowerCase();
    if (/heat|高温|temperature/.test(value)) return '温';
    if (/rain|flood|storm|降水|暴雨|涝/.test(value)) return '雨';
    if (/dry|drought|干旱|无雨/.test(value)) return '旱';
    if (/light|solar|光照/.test(value)) return '光';
    if (/cold|frost|低温|霜/.test(value)) return '霜';
    return '!';
  }

  function renderRisks() {
    const allRisks = normalizeRisks();
    const risks = allRisks.filter(isTriggeredRisk);
    const inactiveCount = allRisks.length - risks.length;
    const container = $('#riskList');
    setText('#riskCount', `${risks.length} 类`);
    if (isFailedAnalysis()) {
      setText('#riskCount', '未研判');
      container.innerHTML = `<div class="empty-inline"><span>!</span><p><strong>分析任务失败</strong>${escapeHtml(analysisFailureMessage())}。该任务没有可用的风险研判结果。</p></div>`;
      return;
    }
    if (!risks.length) {
      const hasAnalysis = isCompletedAnalysis();
      container.innerHTML = `<div class="empty-inline"><span>${hasAnalysis ? '✓' : '…'}</span><p><strong>${hasAnalysis ? '未检出显著风险' : '等待分析结果'}</strong>${hasAnalysis ? `${inactiveCount || allRisks.length} 类监测规则均未触发。` : '运行分析后将按证据展示高温、强降水、干旱与低光照风险。'}</p></div>`;
      return;
    }
    container.innerHTML = risks.map(risk => {
      const level = riskLevel(risk);
      const title = risk.title || risk.name || risk.type || '气象风险';
      const evidence = displayTruthText(risk.evidence || risk.description || risk.detail || risk.message || '分析规则已命中');
      const events = Array.isArray(risk.events) ? risk.events : [];
      const recent = events.at(-1) || {};
      const threshold = risk.threshold || recent.threshold;
      const plot = recent.plot_id || risk.plot_id || (Array.isArray(risk.plot_ids) ? risk.plot_ids.join('、') : '');
      const start = recent.start_date || risk.start_date;
      const end = recent.end_date || risk.end_date;
      const duration = recent.duration_days || risk.duration_days;
      const meta = [threshold && `阈值 ${threshold}`, plot && `空间样本 ${plot}`, start && `时段 ${start}${end && end !== start ? `—${end}` : ''}`, duration && `持续 ${duration} 天`].filter(Boolean);
      return `<article class="risk-item" data-level="${level}"><span class="risk-item__icon">${escapeHtml(riskIcon(title))}</span><div><h4>${escapeHtml(title)}</h4><p>${escapeHtml(typeof evidence === 'string' ? evidence : JSON.stringify(evidence))}</p>${meta.length ? `<div class="risk-meta">${meta.map(item => `<span>${escapeHtml(item)}</span>`).join('')}</div>` : ''}</div><span class="risk-level">${level === 'high' ? '高风险' : level === 'low' ? '低风险' : '中风险'}</span></article>`;
    }).join('') + (inactiveCount ? `<p class="risk-inactive-note">另有 ${inactiveCount} 类监测规则未触发</p>` : '');
  }

  function isTriggeredRisk(risk) {
    const status = String(risk?.status || risk?.trigger_status || '').trim().toLowerCase();
    if (!status) return true;
    if (['正常', '未触发', 'normal', 'inactive', 'clear', 'false', '0'].includes(status)) return false;
    return ['触发', '已触发', 'triggered', 'active', 'alert', 'true', '1'].includes(status) || !['正常', '未触发', 'normal', 'inactive', 'clear'].includes(status);
  }

  function normalizeConclusions() {
    const raw = resultOf().conclusions || resultOf().findings || resultOf().insights || state.analysis?.conclusions || [];
    if (typeof raw === 'string') return [raw];
    if (Array.isArray(raw)) return raw.map(item => typeof item === 'string' ? item : item.conclusion || item.text || item.summary || item.description || JSON.stringify(item));
    if (raw && typeof raw === 'object') return Object.values(raw).map(item => typeof item === 'string' ? item : item?.text || item?.summary).filter(Boolean);
    return [];
  }

  function renderConclusions() {
    const conclusions = normalizeConclusions();
    const list = $('#conclusionList');
    if (isFailedAnalysis()) {
      list.innerHTML = `<li><span>!</span><p>分析任务失败：${escapeHtml(analysisFailureMessage())}。未生成证据结论，请重新运行或选择一条已完成记录。</p></li>`;
      return;
    }
    if (!conclusions.length) {
      list.innerHTML = '<li><span>01</span><p>运行分析后，系统将在此生成带统计依据的季节气象结论。</p></li>';
      return;
    }
    list.innerHTML = conclusions.slice(0, 8).map((conclusion, index) => `<li><span>${String(index + 1).padStart(2, '0')}</span><p>${escapeHtml(displayTruthText(conclusion))}</p></li>`).join('');
  }

  function renderHistory() {
    const container = $('#historyList');
    if (!state.history.length) {
      container.innerHTML = '<div class="history-empty"><span>⌁</span><p>暂无分析记录</p><small>点击“运行分析”创建首个任务</small></div>';
      return;
    }
    container.innerHTML = state.history.map(item => {
      const id = item.analysis_id || item.id || item.uuid;
      const createdAt = item.created_at || item.completed_at || item.timestamp;
      const finishedAt = item.finished_at || item.completed_at || createdAt;
      const status = String(item.status || 'completed');
      const summary = typeof item.summary === 'string' ? item.summary : firstText(item.summary, ['text'], `分析任务 ${id || ''}`);
      const statusKey = status.toLowerCase();
      const failed = ['failed', 'error'].includes(statusKey);
      const running = ['running', 'processing', 'pending'].includes(statusKey);
      const node = failed ? '!' : running ? '…' : '✓';
      const recordCount = firstNumber(item, ['record_count', 'result.overview.record_count'], 0);
      const riskCount = firstNumber(item, ['risk_count'], Array.isArray(item?.result?.risk_events) ? item.result.risk_events.length : 0);
      const scope = formatHistoryScope(item.filters || item?.result?.filters || {});
      const displaySummary = displayTruthText(summary || '气象时序分析');
      return `<button class="history-item${failed ? ' is-failed' : running ? ' is-running' : ''}" type="button" data-analysis-id="${escapeHtml(id)}"><span class="history-item__node">${node}</span><span class="history-item__content"><strong title="${escapeHtml(displaySummary)}">${escapeHtml(displaySummary)}</strong><small class="history-item__scope">${escapeHtml(scope)}</small><span class="history-item__metrics"><b>${formatInteger(recordCount, '0')} 条记录</b><b>${formatInteger(riskCount, '0')} 项风险</b><i class="history-item__status">${escapeHtml(statusLabel(status))}</i></span><span class="history-item__times"><time>开始 ${escapeHtml(formatDate(createdAt, true))}</time><time>完成 ${escapeHtml(formatDate(finishedAt, true))}</time></span></span></button>`;
    }).join('');
  }

  function formatHistoryScope(filters) {
    const parts = [];
    parts.push(filters.plot_id ? String(filters.plot_id) : '全部空间样本');
    const selectedSeason = seasonKey(filters.season);
    if (selectedSeason) parts.push(seasonMeta[selectedSeason].name);
    const year = filters.season_year || filters.year;
    if (year) parts.push(`${year} 年`);
    if (filters.start_date || filters.end_date) parts.push(`${filters.start_date || '起始'} 至 ${filters.end_date || '最新'}`);
    else if (!year && !selectedSeason) parts.push('全时段');
    return parts.join(' · ');
  }

  function statusLabel(status) {
    const value = status.toLowerCase();
    if (['completed', 'success', 'succeeded', 'done'].includes(value)) return '已完成';
    if (['running', 'processing'].includes(value)) return '处理中';
    if (['failed', 'error'].includes(value)) return '失败';
    return status;
  }

  function renderObservations() {
    const body = $('#observationTableBody');
    setText('#recordTotal', formatInteger(state.observationTotal, '0'));
    if (!state.observations.length) {
      body.innerHTML = '<tr><td colspan="7" class="table-empty">当前筛选范围暂无气象日值</td></tr>';
      setText('#tableRange', '显示 0 条');
      $('#loadMoreBtn').hidden = true;
      return;
    }
    const items = state.observations.slice(0, state.tableVisible);
    body.innerHTML = items.map(item => {
      const valid = isValidObservation(item);
      return `<tr><td>${escapeHtml(formatDate(item.timestamp))}</td><td>${escapeHtml(item.plot_id)}</td><td>${formatCell(item.temperature_c, '°C', 1)}</td><td>${formatCell(item.precipitation_mm, 'mm', 1)}</td><td>${formatCell(item.solar_radiation_wm2, 'W/m²', 0)}</td><td>${formatCell(item.humidity_pct, '%', 1)}</td><td><span class="quality-badge ${valid ? 'is-valid' : 'is-suspect'}">${escapeHtml(valid ? '规则通过' : item.quality_flag)}</span></td></tr>`;
    }).join('');
    setText('#tableRange', `显示 1–${items.length}，共 ${integerFormatter.format(state.observationTotal)} 条`);
    $('#loadMoreBtn').hidden = items.length >= state.observations.length && state.observations.length >= state.observationTotal;
  }

  async function loadMoreObservations() {
    const button = $('#loadMoreBtn');
    const contextId = state.contextSequence;
    if (state.tableVisible < state.observations.length) {
      state.tableVisible = Math.min(state.tableVisible + 10, state.observations.length);
      renderObservations();
      return;
    }
    if (state.observations.length >= state.observationTotal) return;
    setButtonBusy(button, true, '加载中');
    try {
      const payload = await Api.observations(state.filters, 500, state.observations.length);
      if (!isCurrentContext(contextId)) return;
      const incoming = (payload?.items || payload?.observations || []).map(normalizeObservation);
      state.observations.push(...incoming);
      state.observationTotal = number(payload?.total, state.observationTotal);
      state.tableVisible = Math.min(state.tableVisible + 10, state.observations.length);
      renderObservations();
    } catch (error) {
      if (isCurrentContext(contextId)) showToast('记录加载失败', error.message, 'error');
    } finally { setButtonBusy(button, false); }
  }

  function formatCell(value, unit, digits) {
    return Number.isFinite(value) ? `${formatNumber(value, digits)} ${unit}` : '<span aria-label="缺失值">--</span>';
  }

  async function runAnalysis() {
    const filters = getFiltersFromForm();
    if (!validateFilters(filters)) return;
    const contextId = beginAnalysisContextAction();
    state.filters = filters;
    setLoading(true, '正在执行季节统计与风险研判…');
    setButtonBusy($('#runAnalysisBtn'), true, '分析中');
    try {
      const response = await Api.runAnalysis(filters);
      if (!isCurrentContext(contextId)) return;
      state.analysis = normalizeAnalysis(response);
      if (!await loadDashboard({ quiet: true, preserveAnalysis: true, contextId })) return;
      await refreshHistory(true, contextId);
      if (!isCurrentContext(contextId)) return;
      showToast('分析任务已完成', `任务 ${state.analysis?.analysis_id || state.analysis?.id || ''} 已生成季节统计、风险与结论`, 'success');
    } catch (error) {
      if (!isCurrentContext(contextId)) return;
      const failedId = error?.detail?.analysis_id;
      if (failedId) {
        try {
          const failedAnalysis = normalizeAnalysis(await Api.analysisDetail(failedId));
          if (!isCurrentContext(contextId)) return;
          state.analysis = failedAnalysis;
          renderDashboard();
        } catch { /* history remains the source of truth if detail reload fails */ }
        await refreshHistory(true, contextId);
      }
      if (!isCurrentContext(contextId)) return;
      showToast('分析运行失败', error.message, 'error');
    } finally {
      setLoading(false);
      setButtonBusy($('#runAnalysisBtn'), false);
      finishAnalysisContextAction(contextId);
    }
  }

  async function applyFiltersAndAnalyze(filters) {
    if (!validateFilters(filters)) return;
    const contextId = beginAnalysisContextAction();
    state.filters = filters;
    state.analysis = null;
    setLoading(true, '正在应用筛选并重算分析…');
    try {
      if (!await loadDashboard({ quiet: true, preserveAnalysis: true, contextId })) return;
      const analysis = normalizeAnalysis(await Api.runAnalysis({ ...state.filters }));
      if (!isCurrentContext(contextId)) return;
      state.analysis = analysis;
      renderDashboard();
      await refreshHistory(true, contextId);
      if (!isCurrentContext(contextId)) return;
      showToast('筛选已应用', '图表、季节统计、风险与结论已按当前范围重算', 'success');
      if (window.innerWidth <= 1050) toggleDrawer(false);
    } catch (error) {
      if (!isCurrentContext(contextId)) return;
      const failedId = error?.detail?.analysis_id;
      if (failedId) {
        try {
          const failedAnalysis = normalizeAnalysis(await Api.analysisDetail(failedId));
          if (!isCurrentContext(contextId)) return;
          state.analysis = failedAnalysis;
        } catch { /* keep the empty result view */ }
        await refreshHistory(true, contextId);
      }
      if (!isCurrentContext(contextId)) return;
      renderDashboard();
      showToast('筛选分析失败', error.message, 'error');
    } finally { setLoading(false); finishAnalysisContextAction(contextId); }
  }

  async function refreshHistory(quiet = false, contextId = null) {
    try {
      const history = normalizeHistory(await Api.history());
      if (contextId !== null && !isCurrentContext(contextId)) return false;
      state.history = history;
      renderHistory();
      if (!quiet) showToast('记录已刷新', `共 ${state.history.length} 次分析任务`, 'success');
      return true;
    } catch (error) {
      if (!quiet && (contextId === null || isCurrentContext(contextId))) showToast('记录刷新失败', error.message, 'error');
      return false;
    }
  }

  async function openHistoryAnalysis(id) {
    if (!id) return;
    const contextId = beginAnalysisContextAction();
    setLoading(true, '正在读取历史分析结果…');
    try {
      const analysis = normalizeAnalysis(await Api.analysisDetail(id));
      if (!isCurrentContext(contextId)) return;
      state.analysis = analysis;
      const storedFilters = state.analysis?.filters || resultOf(state.analysis)?.filters || {};
      state.filters = {
        plot_id: storedFilters.plot_id || '',
        year: storedFilters.year == null ? '' : String(storedFilters.year),
        season: seasonKey(storedFilters.season) || '',
        start_date: storedFilters.start_date || '',
        end_date: storedFilters.end_date || ''
      };
      syncFilterForm();
      if (!await loadDashboard({ quiet: true, preserveAnalysis: true, contextId })) return;
      if (isFailedAnalysis()) {
        showToast('已载入失败任务', `分析任务 ${id}：${analysisFailureMessage()}`, 'warning');
        document.getElementById('analysisFailureBanner').scrollIntoView({ behavior: 'smooth', block: 'center' });
      } else {
        showToast('已载入历史分析', `分析任务 ${id}`, 'success');
        document.getElementById('seasonSection').scrollIntoView({ behavior: 'smooth', block: 'start' });
      }
    } catch (error) {
      if (isCurrentContext(contextId)) showToast('历史结果加载失败', error.message, 'error');
    } finally { setLoading(false); finishAnalysisContextAction(contextId); }
  }

  const evidenceLabels = {
    wettest_season: '降水峰值季节',
    seasonal_precipitation_mm: '季节累计降水',
    longest_dry_spell_days: '最长连续少雨',
    warmest_season: '最高温季节',
    temperature_range_c: '气温范围',
    heat_days: '高温日数',
    brightest_season: '最佳光照季节',
    avg_solar_radiation_wm2: '日均太阳辐射',
    low_light_streaks: '连续低光照事件',
    quality_score: '入库质量评分',
    missing_values: '缺测单元',
    iqr_anomalies: 'IQR 异常记录',
    record_count: '分析记录数',
    HEAT_DAY: '高温日',
    HEAT_STREAK: '连续高温',
    COLD_DAY: '低温冻害',
    HEAVY_RAIN: '强降水积涝',
    DRY_SPELL: '连续干日',
    LOW_LIGHT_STREAK: '连续低光照',
    TEMP_SWING: '日际温度突变'
  };

  const evidenceFieldLabels = {
    metric: '指标编码', value: '指标值', unit: '单位', source: '数据来源', plot_scope: '空间样本范围',
    period_start: '开始日期', period_end: '结束日期', analysis_id: '分析任务', threshold: '判定阈值',
    date: '数据日期', count: '数量', description: '说明', text: '说明', label: '证据名称',
    landing_page: '来源主页', retrieved_at: '获取时间'
  };

  function evidenceUnit(value) {
    const units = { day: '天', event: '个事件', record: '条', cell: '个单元', score: '分', plot: '个网格点' };
    return units[value] || value || '';
  }

  function readableEvidenceValue(value, depth = 0) {
    if (value === null || value === undefined || value === '') return '—';
    if (typeof value === 'boolean') return value ? '是' : '否';
    if (Array.isArray(value)) return value.map(item => readableEvidenceValue(item, depth + 1)).join(' ～ ');
    if (typeof value === 'object') {
      if (depth >= 2) return Object.entries(value).map(([key, item]) => `${key}：${String(item)}`).join('；');
      return Object.entries(value).map(([key, item]) => `${evidenceFieldLabels[key] || key}：${readableEvidenceValue(item, depth + 1)}`).join('；');
    }
    return String(value);
  }

  function evidenceValue(item) {
    const value = readableEvidenceValue(item?.value);
    const unit = evidenceUnit(item?.unit);
    return `${value}${unit && value !== '—' && !value.endsWith(unit) ? ` ${unit}` : ''}`;
  }

  function evidenceFieldMarkup(key, value) {
    const label = evidenceFieldLabels[key] || `扩展字段 · ${key.replaceAll('_', ' ')}`;
    const rendered = key === 'unit' ? `${evidenceUnit(value)}${evidenceUnit(value) !== value ? `（${readableEvidenceValue(value)}）` : ''}` : readableEvidenceValue(value);
    return `<div class="evidence-card__row"><dt>${escapeHtml(label)}</dt><dd>${escapeHtml(rendered)}</dd></div>`;
  }

  function evidenceMarkup(item, index) {
    if (typeof item === 'string') {
      return `<article class="evidence-card" role="listitem"><header><div><span>证据 ${index + 1}</span><strong>分析依据</strong></div></header><p>${escapeHtml(item)}</p></article>`;
    }
    if (!item || typeof item !== 'object') {
      return `<article class="evidence-card" role="listitem"><header><div><span>证据 ${index + 1}</span><strong>分析依据</strong></div></header><p>${escapeHtml(readableEvidenceValue(item))}</p></article>`;
    }
    const metric = String(item.metric || '').trim();
    const label = String(item.label || evidenceLabels[metric] || (metric ? metric.replaceAll('_', ' ') : '结构化分析依据'));
    const hasValue = Object.prototype.hasOwnProperty.call(item, 'value');
    const fieldRows = Object.entries(item)
      .filter(([key]) => key !== 'value' && key !== 'label')
      .map(([key, value]) => evidenceFieldMarkup(key, value)).join('');
    return `<article class="evidence-card" role="listitem"><header><div><span>证据 ${index + 1}</span><strong>${escapeHtml(label)}</strong></div>${hasValue ? `<b>${escapeHtml(evidenceValue(item))}</b>` : ''}</header>${fieldRows ? `<dl>${fieldRows}</dl>` : '<p>已引用结构化分析结果</p>'}</article>`;
  }

  function appendChatMessage(role, text, evidence = []) {
    const windowElement = $('#chatWindow');
    const wrapper = document.createElement('div');
    wrapper.className = `chat-message chat-message--${role}`;
    const safeEvidence = (Array.isArray(evidence) ? evidence : [evidence]).filter(Boolean);
    wrapper.innerHTML = `${role === 'agent' ? '<div class="chat-message__avatar">A</div>' : ''}<div class="chat-bubble"><p>${escapeHtml(role === 'agent' ? displayTruthText(text) : text)}</p>${safeEvidence.length ? `<div class="chat-evidence" role="list" aria-label="回答依据">${safeEvidence.map(evidenceMarkup).join('')}</div>` : ''}<time>${new Date().toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit', hour12: false })}</time></div>`;
    windowElement.appendChild(wrapper);
    windowElement.scrollTop = windowElement.scrollHeight;
    return wrapper;
  }

  function appendTyping() {
    const windowElement = $('#chatWindow');
    const wrapper = document.createElement('div');
    wrapper.className = 'chat-message chat-message--agent';
    wrapper.id = 'agentTyping';
    wrapper.innerHTML = '<div class="chat-message__avatar">A</div><div class="chat-bubble"><div class="typing-dots" aria-label="Agent 正在思考"><i></i><i></i><i></i></div></div>';
    windowElement.appendChild(wrapper);
    windowElement.scrollTop = windowElement.scrollHeight;
  }

  async function submitAgentQuestion(question) {
    const value = String(question || '').trim();
    if (!value) return;
    if (state.agentQueryPending) return;
    if (state.analysis && !isCompletedAnalysis()) {
      const id = analysisIdOf();
      showToast('当前任务不可问答', `分析任务${id ? ` #${id}` : ''}未成功完成，请重新运行或选择已完成记录`, 'warning');
      return;
    }
    const textarea = $('#agentQuestion');
    appendChatMessage('user', value);
    textarea.value = '';
    resizeTextarea(textarea);
    const requestId = ++state.agentQuerySequence;
    state.activeAgentQuery = requestId;
    state.agentQueryPending = true;
    renderAnalysisState();
    appendTyping();
    try {
      const payload = await Api.queryAgent({
        question: value,
        plot_id: state.filters.plot_id,
        year: state.filters.year ? Number(state.filters.year) : undefined,
        season: state.filters.season,
        start_date: state.filters.start_date,
        end_date: state.filters.end_date,
        analysis_id: state.analysis?.analysis_id || state.analysis?.id
      });
      if (requestId !== state.activeAgentQuery) return;
      $('#agentTyping')?.remove();
      setAgentRuntime(payload?.agent_runtime, { source: 'query', fallbackMode: 'rule_fallback', requestId });
      appendChatMessage('agent', payload?.answer || payload?.response || payload?.message || '分析完成，但服务未返回文本结论。', payload?.evidence || []);
      if (Array.isArray(payload?.suggestions) && payload.suggestions.length) renderSuggestions(payload.suggestions);
    } catch (error) {
      if (requestId !== state.activeAgentQuery) return;
      $('#agentTyping')?.remove();
      appendChatMessage('agent', `请求处理异常：${error.message}`);
      showToast('Agent 查询失败', error.message, 'error');
    } finally {
      if (requestId === state.activeAgentQuery) {
        state.agentQueryPending = false;
        renderAnalysisState();
        if (!textarea.disabled) textarea.focus();
      }
    }
  }

  function renderSuggestions(suggestions) {
    const container = $('#suggestionChips');
    container.innerHTML = suggestions.slice(0, 5).map(item => `<button type="button">${escapeHtml(typeof item === 'string' ? item : item.question || item.text)}</button>`).join('');
  }

  function resizeTextarea(textarea) {
    textarea.style.height = 'auto';
    textarea.style.height = `${Math.min(112, Math.max(39, textarea.scrollHeight))}px`;
  }

  function openImportDialog() {
    const dialog = $('#importDialog');
    clearSelectedFile();
    if (typeof dialog.showModal === 'function') dialog.showModal();
    else dialog.setAttribute('open', '');
  }

  function closeImportDialog() {
    const dialog = $('#importDialog');
    if (typeof dialog.close === 'function') dialog.close();
    else dialog.removeAttribute('open');
  }

  function selectFile(file) {
    if (!file) return;
    if (!file.name.toLowerCase().endsWith('.csv') && file.type !== 'text/csv') {
      showToast('文件格式错误', '请选择 .csv 格式的气象数据文件', 'error'); return;
    }
    if (file.size > 20 * 1024 * 1024) {
      showToast('文件过大', 'CSV 文件大小应小于 20 MB', 'error'); return;
    }
    state.selectedFile = file;
    $('#selectedFile').hidden = false;
    setText('#selectedFileName', file.name);
    setText('#selectedFileMeta', `${formatFileSize(file.size)} · 等待导入`);
    $('#importSubmitBtn').disabled = false;
  }

  function clearSelectedFile() {
    state.selectedFile = null;
    $('#csvFileInput').value = '';
    $('#selectedFile').hidden = true;
    $('#importSubmitBtn').disabled = true;
  }

  function formatFileSize(bytes) {
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 * 1024) return `${formatNumber(bytes / 1024, 1)} KB`;
    return `${formatNumber(bytes / 1024 / 1024, 1)} MB`;
  }

  async function submitImport() {
    if (!state.selectedFile) return;
    const button = $('#importSubmitBtn');
    setButtonBusy(button, true, '导入中');
    try {
      const response = await Api.importCsv(state.selectedFile, $('#replaceDataCheck').checked);
      const imported = firstNumber(response, ['imported', 'inserted', 'created'], 0);
      const updated = firstNumber(response, ['updated'], 0);
      const rejected = firstNumber(response, ['rejected', 'failed'], 0);
      const replace = $('#replaceDataCheck').checked;
      closeImportDialog();
      showToast('CSV 导入完成', `新增 ${formatInteger(imported)} 条，更新 ${formatInteger(updated)} 条，拒绝 ${formatInteger(rejected)} 条`, rejected ? 'warning' : 'success');
      state.analysis = null;
      if (replace) {
        state.filters = { plot_id: '', year: '', season: '', start_date: '', end_date: '' };
        syncFilterForm();
      }
      await loadDashboard({ quiet: true, preserveAnalysis: true });
      state.analysis = normalizeAnalysis(await Api.runAnalysis(state.filters));
      renderDashboard();
      await refreshHistory(true);
      showToast('导入闭环已完成', '新数据已完成质量检查、季节统计与风险重算', 'success');
    } catch (error) {
      showToast('CSV 导入失败', error.message, 'error');
    } finally { setButtonBusy(button, false); }
  }

  async function exportFile(type) {
    const isCsv = type === 'csv';
    const isExcel = type === 'xlsx';
    const path = isCsv ? '/export/seasonal.csv' : isExcel ? '/export/report.xlsx' : '/export/report.md';
    const fallbackName = isCsv ? 'seasonal_weather_statistics.csv' : isExcel ? 'agri_weather_analysis_report.xlsx' : 'agri_weather_analysis_report.md';
    if (state.analysis && !isCompletedAnalysis()) {
      const id = analysisIdOf();
      showToast('当前任务不可导出', `分析任务${id ? ` #${id}` : ''}未成功完成，未发送 analysis_id 导出请求`, 'warning');
      closeExportMenu();
      return;
    }
    setLoading(true, isCsv ? '正在生成季节统计 CSV…' : isExcel ? '正在生成 Excel 工程报告…' : '正在生成分析报告…');
    try {
      const analysisId = isCompletedAnalysis() ? analysisIdOf() : null;
      const exportQuery = analysisId ? { analysis_id: analysisId } : state.filters;
      const response = await fetch(`${API_ROOT}${path}${queryString(exportQuery)}`);
      if (!response.ok) {
        let detail = `HTTP ${response.status}`;
        try { const body = await response.json(); detail = body.detail || body.message || detail; } catch { /* response is not JSON */ }
        throw new Error(detail);
      }
      const blob = await response.blob();
      const disposition = response.headers.get('content-disposition') || '';
      const match = disposition.match(/filename\*?=(?:UTF-8''|\")?([^\";]+)/i);
      const fileName = match ? decodeURIComponent(match[1].replaceAll('"', '')) : fallbackName;
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement('a');
      anchor.href = url; anchor.download = fileName; document.body.appendChild(anchor); anchor.click(); anchor.remove();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
      showToast('导出已完成', `${fileName} 已开始下载`, 'success');
    } catch (error) {
      showToast('导出失败', error.message, 'error');
    } finally { setLoading(false); closeExportMenu(); }
  }

  function setLoading(active, text = '正在处理…') {
    if (active) state.busyCount += 1;
    else state.busyCount = Math.max(0, state.busyCount - 1);
    const loader = $('#globalLoader');
    setText('#globalLoaderText', text);
    loader.hidden = state.busyCount === 0;
    document.body.setAttribute('aria-busy', state.busyCount ? 'true' : 'false');
  }

  function setButtonBusy(button, active, label = '') {
    if (!button) return;
    if (active) {
      button.dataset.originalHtml = button.innerHTML;
      button.disabled = true;
      button.innerHTML = `<span class="button-spinner" aria-hidden="true"></span><span>${escapeHtml(label)}</span>`;
    } else {
      button.disabled = false;
      if (button.dataset.originalHtml) { button.innerHTML = button.dataset.originalHtml; delete button.dataset.originalHtml; }
    }
  }

  function renderAnalysisState() {
    const failed = isFailedAnalysis();
    const blocked = Boolean(state.analysis) && !isCompletedAnalysis();
    const banner = $('#analysisFailureBanner');
    banner.hidden = !failed;
    if (failed) {
      const id = analysisIdOf();
      setText('#analysisFailureTitle', `分析任务${id ? ` #${id}` : ''}未成功完成`);
      setText('#analysisFailureMessage', `${analysisFailureMessage()}。该任务未生成可用的季节统计、风险研判或证据结论；请选择已完成记录或调整筛选后重新运行。`);
    }

    const textarea = $('#agentQuestion');
    const submitButton = $('#agentForm button[type="submit"]');
    const inputBlocked = blocked || state.agentQueryPending;
    textarea.disabled = inputBlocked;
    textarea.placeholder = blocked ? '当前分析未成功完成，请重新运行或选择已完成记录'
      : state.agentQueryPending ? 'Agent 正在处理当前问题…'
      : '例如：夏季高温和降水是否对农田构成风险？';
    submitButton.disabled = inputBlocked;
    $$('#suggestionChips button').forEach(button => { button.disabled = inputBlocked; });
    $('#scrollAgentBtn').disabled = blocked;

    renderAgentStatus();

    const exportButton = $('#exportMenuBtn');
    exportButton.disabled = blocked;
    exportButton.title = blocked ? '当前分析未成功完成，结果导出已锁定' : '';
    $$('#exportMenu [data-export]').forEach(button => { button.disabled = blocked; });
    if (blocked) closeExportMenu();
  }

  function showToast(title, message, type = 'success', duration = 4800) {
    const region = $('#toastRegion');
    const toast = document.createElement('div');
    toast.className = `toast ${type === 'error' ? 'is-error' : type === 'warning' ? 'is-warning' : ''}`;
    toast.innerHTML = `<span class="toast__icon">${type === 'error' ? '!' : type === 'warning' ? 'i' : '✓'}</span><div><strong>${escapeHtml(title)}</strong><p>${escapeHtml(message || '')}</p></div><button type="button" aria-label="关闭通知">×</button>`;
    const remove = () => { toast.classList.add('is-leaving'); setTimeout(() => toast.remove(), 220); };
    $('button', toast).addEventListener('click', remove);
    region.appendChild(toast);
    setTimeout(remove, duration);
  }

  function openExportMenu() {
    const menu = $('#exportMenu');
    menu.hidden = false;
    $('#exportMenuBtn').setAttribute('aria-expanded', 'true');
    $('button', menu)?.focus();
  }

  function closeExportMenu() {
    $('#exportMenu').hidden = true;
    $('#exportMenuBtn').setAttribute('aria-expanded', 'false');
  }

  function toggleDrawer(open, { restoreFocus = false } = {}) {
    $('#appShell').classList.toggle('is-drawer-open', open);
    $('#openSidebarBtn').setAttribute('aria-expanded', String(open));
    document.body.style.overflow = open ? 'hidden' : '';
    if (open) $('#closeSidebarBtn').focus();
    else if (restoreFocus) $('#openSidebarBtn').focus();
  }

  function setActiveNavigation(targetId) {
    $$('.section-nav__item').forEach(link => {
      const active = link.dataset.section === targetId;
      link.classList.toggle('is-active', active);
      if (active) link.setAttribute('aria-current', 'location');
      else link.removeAttribute('aria-current');
    });
  }

  function bindNavigationTracking() {
    if (!('IntersectionObserver' in window)) return;
    const links = $$('.section-nav__item');
    const observer = new IntersectionObserver(entries => {
      const visible = entries.filter(entry => entry.isIntersecting).sort((a, b) => b.intersectionRatio - a.intersectionRatio)[0];
      if (!visible) return;
      setActiveNavigation(visible.target.id);
    }, { rootMargin: '-20% 0px -68% 0px', threshold: [0, .1, .4] });
    links.forEach(link => { const section = document.getElementById(link.dataset.section); if (section) observer.observe(section); });
  }

  function bindEvents() {
    $('#filterForm').addEventListener('submit', event => {
      event.preventDefault();
      const filters = getFiltersFromForm();
      applyFiltersAndAnalyze(filters);
    });
    $('#resetFiltersBtn').addEventListener('click', () => {
      const emptyFilters = { plot_id: '', year: '', season: '', start_date: '', end_date: '' };
      state.filters = emptyFilters;
      syncFilterForm();
      applyFiltersAndAnalyze(emptyFilters);
    });
    $('#runAnalysisBtn').addEventListener('click', runAnalysis);
    $('#refreshHistoryBtn').addEventListener('click', () => refreshHistory(false));
    $('#historyList').addEventListener('click', event => {
      const button = event.target.closest('[data-analysis-id]');
      if (button) openHistoryAnalysis(button.dataset.analysisId);
    });
    $('#loadMoreBtn').addEventListener('click', loadMoreObservations);
    $$('.range-tabs button').forEach(button => button.addEventListener('click', () => {
      $$('.range-tabs button').forEach(item => item.classList.toggle('is-active', item === button));
      state.granularity = button.dataset.granularity;
      renderKpis(); renderCharts();
    }));
    $$('.season-metric-tabs button').forEach(button => button.addEventListener('click', () => {
      $$('.season-metric-tabs button').forEach(item => item.classList.toggle('is-active', item === button));
      state.seasonMetric = button.dataset.seasonMetric;
      drawSeasonChart(normalizeSeasonal());
    }));
    $('#scrollAgentBtn').addEventListener('click', () => { $('#agentSection').scrollIntoView({ behavior: 'smooth' }); setTimeout(() => $('#agentQuestion').focus(), 450); });
    $('#agentForm').addEventListener('submit', event => { event.preventDefault(); submitAgentQuestion($('#agentQuestion').value); });
    $('#agentQuestion').addEventListener('input', event => resizeTextarea(event.target));
    $('#agentQuestion').addEventListener('keydown', event => {
      if (event.key === 'Enter' && !event.shiftKey) { event.preventDefault(); $('#agentForm').requestSubmit(); }
    });
    $('#suggestionChips').addEventListener('click', event => { const button = event.target.closest('button'); if (button) submitAgentQuestion(button.textContent); });
    $('#mcpCallForm').addEventListener('submit', event => { event.preventDefault(); callFastMCP(); });

    $('#importOpenBtn').addEventListener('click', openImportDialog);
    $('#importCloseBtn').addEventListener('click', closeImportDialog);
    $('#importCancelBtn').addEventListener('click', closeImportDialog);
    $('#dropZone').addEventListener('click', () => $('#csvFileInput').click());
    $('#dropZone').addEventListener('keydown', event => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); $('#csvFileInput').click(); } });
    $('#csvFileInput').addEventListener('change', event => selectFile(event.target.files?.[0]));
    ['dragenter', 'dragover'].forEach(name => $('#dropZone').addEventListener(name, event => { event.preventDefault(); $('#dropZone').classList.add('is-dragging'); }));
    ['dragleave', 'drop'].forEach(name => $('#dropZone').addEventListener(name, event => { event.preventDefault(); $('#dropZone').classList.remove('is-dragging'); }));
    $('#dropZone').addEventListener('drop', event => selectFile(event.dataTransfer?.files?.[0]));
    $('#clearFileBtn').addEventListener('click', clearSelectedFile);
    $('#importForm').addEventListener('submit', event => { event.preventDefault(); submitImport(); });

    $('#exportMenuBtn').addEventListener('click', event => { event.stopPropagation(); $('#exportMenu').hidden ? openExportMenu() : closeExportMenu(); });
    $('#exportMenu').addEventListener('click', event => { const button = event.target.closest('[data-export]'); if (button) exportFile(button.dataset.export); });
    document.addEventListener('click', event => { if (!event.target.closest('.export-menu-wrap')) closeExportMenu(); });
    document.addEventListener('keydown', event => { if (event.key === 'Escape') { closeExportMenu(); if ($('#appShell').classList.contains('is-drawer-open')) toggleDrawer(false, { restoreFocus: true }); } });

    $('#openSidebarBtn').addEventListener('click', () => toggleDrawer(true));
    $('#closeSidebarBtn').addEventListener('click', () => toggleDrawer(false, { restoreFocus: true }));
    $('#drawerScrim').addEventListener('click', () => toggleDrawer(false, { restoreFocus: true }));
    $$('.section-nav__item').forEach(link => link.addEventListener('click', event => {
      const target = document.getElementById(link.dataset.section);
      if (!target) return;
      event.preventDefault();
      if (window.innerWidth <= 1050) toggleDrawer(false);
      setActiveNavigation(target.id);
      target.scrollIntoView({ behavior: 'smooth', block: 'start' });
      if (!target.hasAttribute('tabindex')) target.setAttribute('tabindex', '-1');
      setTimeout(() => target.focus({ preventScroll: true }), 380);
      history.replaceState(null, '', `#${target.id}`);
    }));
  }

  function bindResponsiveCharts() {
    let timer;
    const redraw = () => { clearTimeout(timer); timer = setTimeout(() => { renderCharts(); drawSeasonChart(normalizeSeasonal()); }, 130); };
    if ('ResizeObserver' in window) {
      const observer = new ResizeObserver(redraw);
      $$('.canvas-wrap').forEach(element => observer.observe(element));
    } else window.addEventListener('resize', redraw);
  }

  async function init() {
    bindEvents();
    bindNavigationTracking();
    bindResponsiveCharts();
    syncFilterForm();
    await Promise.all([checkHealth(), loadDashboard(), loadFastMCP()]);
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init, { once: true });
  else init();
})();
