// utils/derive.js
// 状态推导：排序、TOP5 推导、关注方向增减、图表 option 构造、轮询状态机等纯函数。
//
// 设计约束：本模块为纯 JavaScript（CommonJS），不依赖任何 `wx.*` 宿主能力，
// 因此可在 Node 环境下用单元测试与属性测试覆盖（见 design.md Properties 8–12、16、17、18）。
//
// Feature: miniprogram-web-parity

// ---------------------------------------------------------------------------
// 持仓排序
// ---------------------------------------------------------------------------

/**
 * 按当日收益降序排列持仓列表（nulls last）。
 * 原列表不修改，返回新数组。
 *
 * Property: 此函数是后续属性测试覆盖的基础（design.md 阶段二）。
 * @param {Array<{daily_profit?: number|null}>} holdings
 * @returns {Array}
 */
function holdingsSortByDailyProfit(holdings) {
  if (!Array.isArray(holdings)) return [];
  return holdings.slice().sort(function (a, b) {
    var ap = a == null ? null : a.daily_profit;
    var bp = b == null ? null : b.daily_profit;
    var aNull = ap == null || !Number.isFinite(Number(ap));
    var bNull = bp == null || !Number.isFinite(Number(bp));
    if (aNull && bNull) return 0;
    if (aNull) return 1;   // nulls last
    if (bNull) return -1;
    return Number(bp) - Number(ap); // descending
  });
}

// ---------------------------------------------------------------------------
// 组合 KPI 汇总（Req 17.2，Property 17）
// ---------------------------------------------------------------------------

/**
 * 从持仓列表汇总总资产与当日收益。
 * 空列表归零不抛错（Property 17）。
 * @param {Array} holdings
 * @returns {{ totalAssets: number, dailyProfit: number }}
 */
function summarizeHoldings(holdings) {
  if (!Array.isArray(holdings) || holdings.length === 0) {
    return { totalAssets: 0, dailyProfit: 0 };
  }
  var totalAssets = 0;
  var dailyProfit = 0;
  for (var i = 0; i < holdings.length; i++) {
    var h = holdings[i];
    if (h == null) continue;
    var amt = Number(h.holding_amount);
    var dp = Number(h.daily_profit);
    if (Number.isFinite(amt)) totalAssets += amt;
    if (Number.isFinite(dp)) dailyProfit += dp;
  }
  return { totalAssets: totalAssets, dailyProfit: dailyProfit };
}

// ---------------------------------------------------------------------------
// 当日 TOP5 推导（Req 9.6，Property 11）
// ---------------------------------------------------------------------------

/**
 * 从持仓列表推导当日盈利 TOP5 与亏损 TOP5。
 * - 盈利 TOP5：daily_profit > 0，降序，≤5 条。
 * - 亏损 TOP5：daily_profit < 0，升序（绝对值最大优先），≤5 条。
 * - 两个列表不含同一基金（互斥）。
 * @param {Array} holdings
 * @returns {{ gainers: Array, losers: Array }}
 */
function deriveDailyTop5(holdings) {
  if (!Array.isArray(holdings) || holdings.length === 0) {
    return { gainers: [], losers: [] };
  }
  var gainers = [];
  var losers = [];
  for (var i = 0; i < holdings.length; i++) {
    var h = holdings[i];
    if (h == null) continue;
    var dp = Number(h.daily_profit);
    if (!Number.isFinite(dp)) continue;
    if (dp > 0) gainers.push(h);
    else if (dp < 0) losers.push(h);
  }
  gainers.sort(function (a, b) { return Number(b.daily_profit) - Number(a.daily_profit); });
  losers.sort(function (a, b) { return Number(a.daily_profit) - Number(b.daily_profit); });
  return {
    gainers: gainers.slice(0, 5),
    losers: losers.slice(0, 5),
  };
}

// ---------------------------------------------------------------------------
// 关注方向增减约束（Req 10.7，Property 12）
// ---------------------------------------------------------------------------

/**
 * 把新板块加入关注方向集合（去重、上限 3 个）。
 * - 已存在：不重复加入，返回原数组副本。
 * - 未满 3 个：加入后返回新数组。
 * - 已满 3 个且未包含：保持不变，返回原数组副本。
 * @param {string[]} current  当前关注方向集合
 * @param {string}   newSector 要加入的板块
 * @returns {string[]}
 */
function addFocusSector(current, newSector) {
  var arr = Array.isArray(current) ? current.slice() : [];
  if (!newSector) return arr;
  if (arr.indexOf(newSector) !== -1) return arr; // 已存在，不重复
  if (arr.length >= 3) return arr;               // 已满，保持不变
  arr.push(newSector);
  return arr;
}

/**
 * 从关注方向集合移除某板块（不在集合中时无副作用）。
 * @param {string[]} current
 * @param {string}   sector
 * @returns {string[]}
 */
function removeFocusSector(current, sector) {
  var arr = Array.isArray(current) ? current.slice() : [];
  var idx = arr.indexOf(sector);
  if (idx === -1) return arr;
  arr.splice(idx, 1);
  return arr;
}

// ---------------------------------------------------------------------------
// 美股数据源不可用占位（Req 12.6，Property 13）
// ---------------------------------------------------------------------------

/**
 * 将单条行情条目映射为展示值：
 * - status === 'unavailable' → 数值字段返回 null，isUnavailable: true
 * - status ∈ {ok, stale} → 保留原数值字段
 * @param {{ status: string, last_price?: number|null, change_percent?: number|null }} item
 * @returns {{ status: string, last_price: number|null, change_percent: number|null, isUnavailable: boolean }}
 */
function mapUsQuoteDisplay(item) {
  if (!item) return { status: 'unavailable', last_price: null, change_percent: null, isUnavailable: true };
  var unavailable = item.status === 'unavailable';
  return {
    status: item.status,
    last_price: unavailable ? null : (item.last_price != null ? item.last_price : null),
    change_percent: unavailable ? null : (item.change_percent != null ? item.change_percent : null),
    isUnavailable: unavailable,
  };
}

// ---------------------------------------------------------------------------
// 任务轮询状态机（Req 13.4/15.5，Property 16）
// ---------------------------------------------------------------------------

var POLL_CONTINUE = 'continue';
var POLL_STOP = 'stop';

/**
 * 根据任务状态决定是否继续轮询。
 * pending / running → 继续；completed / failed → 停止。
 * @param {string} status
 * @returns {'continue'|'stop'}
 */
function pollDecision(status) {
  if (status === 'pending' || status === 'running') return POLL_CONTINUE;
  return POLL_STOP; // completed / failed / unknown
}

// 已知 stage → 展示标签映射（确保每个已知 stage 唯一确定）。
var STAGE_LABELS = {
  initializing: '准备中',
  fetching_holdings: '获取持仓',
  fetching_news: '获取资讯',
  analyzing: '分析中',
  generating: '生成报告',
  done: '完成',
  failed: '失败',
};

/**
 * 获取阶段展示标签，未知 stage 返回空字符串。
 * @param {string} stage
 * @returns {string}
 */
function getStagLabel(stage) {
  if (!stage) return '';
  // 用 hasOwnProperty 防止原型链键（如 "__proto__"/"constructor"）误命中 Object.prototype。
  if (!Object.prototype.hasOwnProperty.call(STAGE_LABELS, stage)) return '';
  return STAGE_LABELS[stage] || '';
}

// ---------------------------------------------------------------------------
// 图表数据映射（Req 8.2/9.3/9.7，Property 8）
// ---------------------------------------------------------------------------

/**
 * 从时间序列点集生成折线图 option（收益走势，保点 x 轴时间严格有序）。
 * @param {Array<{ time?: string, date?: string, portfolio_percent?: number|null, index_percent?: number|null }>} points
 * @returns {object} echarts option
 */
function buildLineChartOption(points) {
  if (!Array.isArray(points) || points.length === 0) {
    return { xAxis: { data: [] }, series: [{ data: [] }, { data: [] }] };
  }
  // 按时间排序（x 轴严格有序）。
  var sorted = points.slice().sort(function (a, b) {
    var ta = a.time || a.date || '';
    var tb = b.time || b.date || '';
    return ta < tb ? -1 : ta > tb ? 1 : 0;
  });
  var xData = sorted.map(function (p) { return p.time || p.date || ''; });
  var portfolioData = sorted.map(function (p) {
    return p.portfolio_percent != null ? p.portfolio_percent : null;
  });
  var indexData = sorted.map(function (p) {
    return p.index_percent != null ? p.index_percent : null;
  });
  return {
    xAxis: { type: 'category', data: xData },
    yAxis: { type: 'value' },
    series: [
      { name: '我的收益', type: 'line', data: portfolioData },
      { name: '沪深300', type: 'line', data: indexData },
    ],
  };
}

/**
 * 从持仓分布列表生成甜甜圈图 option（各扇区值 = weight_percent，总和≈100%）。
 * @param {Array<{ fund_code: string, fund_name: string, weight_percent: number }>} allocation
 * @returns {object} echarts option
 */
function buildDonutChartOption(allocation) {
  if (!Array.isArray(allocation) || allocation.length === 0) {
    return { series: [{ data: [] }] };
  }
  var seriesData = allocation.map(function (row) {
    return {
      name: row.fund_name || row.fund_code || '',
      value: row.weight_percent != null ? row.weight_percent : 0,
    };
  });
  return {
    series: [{
      type: 'pie',
      radius: ['40%', '70%'],
      data: seriesData,
    }],
  };
}

// ---------------------------------------------------------------------------
// 净值分页合并去重排序（Req 8.4，Property 9）
// ---------------------------------------------------------------------------

/**
 * 合并多页净值点序列，按日期有序、无重复日期。
 * 长度等于各页去重后并集大小。
 * @param {Array<Array<{ date: string }>>} pages
 * @returns {Array<{ date: string }>}
 */
function mergeNavPages(pages) {
  if (!Array.isArray(pages)) return [];
  var seen = Object.create(null);
  var merged = [];
  for (var i = 0; i < pages.length; i++) {
    var page = pages[i];
    if (!Array.isArray(page)) continue;
    for (var j = 0; j < page.length; j++) {
      var item = page[j];
      if (!item || !item.date) continue;
      if (!seen[item.date]) {
        seen[item.date] = true;
        merged.push(item);
      }
    }
  }
  merged.sort(function (a, b) {
    return a.date < b.date ? -1 : a.date > b.date ? 1 : 0;
  });
  return merged;
}

// ---------------------------------------------------------------------------
// 盈亏日历着色映射（Req 9.4，Property 10）
// ---------------------------------------------------------------------------

/**
 * 给盈亏日历的每个交易日附加配色类名。
 * - daily_profit > 0 → 'up'
 * - daily_profit < 0 → 'down'
 * - daily_profit == 0 → 'neutral'
 * - 非交易日 → 'placeholder'（不参与盈亏配色）
 * @param {Array<{ is_trading_day: boolean, daily_profit?: number|null }>} days
 * @returns {Array} 带 colorClass 属性的日历项数组
 */
function mapCalendarColors(days) {
  if (!Array.isArray(days)) return [];
  return days.map(function (d) {
    if (!d) return null;
    var result = Object.assign({}, d);
    if (!d.is_trading_day) {
      result.colorClass = 'neutral';
      result.daily_profit = 0;
    } else if (d.is_pending_update) {
      result.colorClass = 'placeholder';
      result.daily_profit = null;
    } else {
      var dp = Number(d.daily_profit);
      if (!Number.isFinite(dp) || dp === 0) {
        result.colorClass = 'neutral';
      } else if (dp > 0) {
        result.colorClass = 'up';
      } else {
        result.colorClass = 'down';
      }
    }
    return result;
  });
}

// ---------------------------------------------------------------------------
// 投资预设联动（Req 18.3，Property 18）
// ---------------------------------------------------------------------------

var PRESET_DEFAULTS = {
  conservative_hold: {
    investment_preset: 'conservative_hold',
    max_drawdown_percent: 10,
    concentration_limit_percent: 30,
    prefer_dca: true,
    avoid_chasing: true,
    decision_style: 'conservative',
  },
  aggressive_swing: {
    investment_preset: 'aggressive_swing',
    max_drawdown_percent: 25,
    concentration_limit_percent: 50,
    prefer_dca: false,
    avoid_chasing: false,
    decision_style: 'aggressive',
  },
};

/**
 * 根据预设名返回对应的风控字段配置（investment_preset 同步）。
 * @param {'conservative_hold'|'aggressive_swing'} preset
 * @returns {object|null}
 */
function applyInvestmentPreset(preset) {
  if (!preset) return null;
  return PRESET_DEFAULTS[preset] ? Object.assign({}, PRESET_DEFAULTS[preset]) : null;
}

// ---------------------------------------------------------------------------
// 板块分时图数据映射（Req 8.2，Property 8）
// ---------------------------------------------------------------------------

/**
 * 从板块分时行情数据生成单折线分时图 option。
 * 分时图 x 轴为时间标签（HH:MM），y 轴为涨跌幅或价格。
 * @param {Array<{ time?: string, change_percent?: number|null, price?: number|null }>} points
 * @param {{ yField?: 'change_percent'|'price', seriesName?: string }} opts
 * @returns {object} echarts option
 */
function buildIntradayChartOption(points, opts) {
  var options = opts || {};
  var yField = options.yField || 'change_percent';
  var seriesName = options.seriesName || '分时';

  if (!Array.isArray(points) || points.length === 0) {
    return {
      xAxis: { type: 'category', data: [] },
      yAxis: { type: 'value' },
      series: [{ name: seriesName, type: 'line', data: [] }],
    };
  }

  // 按 time 排序（x 轴严格有序），与 buildLineChartOption 保持一致。
  var sorted = points.slice().sort(function (a, b) {
    var ta = a.time || '';
    var tb = b.time || '';
    return ta < tb ? -1 : ta > tb ? 1 : 0;
  });

  var xData = sorted.map(function (p) { return p.time || ''; });
  var yData = sorted.map(function (p) {
    var v = p[yField];
    return v != null ? v : null;
  });

  return {
    grid: { top: 20, right: 8, bottom: 24, left: 48 },
    xAxis: {
      type: 'category',
      data: xData,
      axisLabel: { fontSize: 10, color: '#94a3b8' },
      axisLine: { lineStyle: { color: '#e2e8f0' } },
    },
    yAxis: {
      type: 'value',
      axisLabel: {
        fontSize: 10,
        color: '#94a3b8',
        formatter: yField === 'change_percent' ? '{value}%' : '{value}',
      },
      splitLine: { lineStyle: { color: '#f1f5f9' } },
    },
    series: [{
      name: seriesName,
      type: 'line',
      data: yData,
      smooth: false,
      symbol: 'none',
      lineStyle: { color: '#2356e0', width: 2 },
      areaStyle: { color: 'rgba(35,86,224,0.08)' },
    }],
    tooltip: {
      trigger: 'axis',
      formatter: yField === 'change_percent'
        ? function (params) {
            var p = params && params[0];
            if (!p) return '';
            var v = p.value;
            return p.axisValue + '<br/>' + (v != null ? v.toFixed(2) + '%' : '—');
          }
        : undefined,
    },
  };
}

// ---------------------------------------------------------------------------
// 导出
// ---------------------------------------------------------------------------

module.exports = {
  // 持仓排序
  holdingsSortByDailyProfit: holdingsSortByDailyProfit,
  // 组合 KPI
  summarizeHoldings: summarizeHoldings,
  // 当日 TOP5
  deriveDailyTop5: deriveDailyTop5,
  // 关注方向增减
  addFocusSector: addFocusSector,
  removeFocusSector: removeFocusSector,
  // 美股不可用占位
  mapUsQuoteDisplay: mapUsQuoteDisplay,
  // 任务轮询状态机
  pollDecision: pollDecision,
  getStagLabel: getStagLabel,
  POLL_CONTINUE: POLL_CONTINUE,
  POLL_STOP: POLL_STOP,
  STAGE_LABELS: STAGE_LABELS,
  // 图表数据映射
  buildLineChartOption: buildLineChartOption,
  buildDonutChartOption: buildDonutChartOption,
  buildIntradayChartOption: buildIntradayChartOption,
  // 净值分页合并
  mergeNavPages: mergeNavPages,
  // 盈亏日历配色
  mapCalendarColors: mapCalendarColors,
  // 投资预设联动
  applyInvestmentPreset: applyInvestmentPreset,
  PRESET_DEFAULTS: PRESET_DEFAULTS,
};
