// components/chart-calendar/chart-calendar.js
// 盈亏日历组件 — WXML 网格自绘（不用 echarts），复用 num-text 做涨跌着色。
//
// 设计说明（design.md 图表方案）：
//   盈亏日历 `chart-calendar` 用 WXML 网格 + `num-text` 着色自绘（而非 echarts calendar），
//   更轻、交互更可控，并复用红涨绿跌语义。
//
// 数据流：
//   父页面（pages/profit）从 API 拿到 profit_calendar.days[]，
//   调用 derive.mapCalendarColors 为每个 day 附加 colorClass，再把结果传入本组件。
//   本组件只负责把 colorClass 映射成 CSS class，以及把月份导航事件 emit 给父页面。
//
// Props:
//   days            - Array：经 derive.mapCalendarColors 处理后的日历日数组，
//                     每项含 { date, daily_profit, is_trading_day, colorClass, ... }
//   calendarYear    - Number：当前年份
//   calendarMonth   - Number：当前月份（1-12）
//
// 事件：
//   monthChange     - 用户点击上/下月按钮时触发，detail: { year, month }
//
// Requirements: 9.4, 9.5
// Design: miniprogram-web-parity / 图表方案 / chart-calendar 用 WXML 网格自绘

Component({
  properties: {
    /** 经 derive.mapCalendarColors 处理的 days 数组，含 colorClass 字段 */
    days: {
      type: Array,
      value: [],
    },
    /** 当前日历年份 */
    calendarYear: {
      type: Number,
      value: new Date().getFullYear(),
    },
    /** 当前日历月份（1-12） */
    calendarMonth: {
      type: Number,
      value: new Date().getMonth() + 1,
    },
  },

  data: {
    // 工作日表头（一二三四五六日，对应 Mon-Sun）
    weekHeaders: ['一', '二', '三', '四', '五', '六', '日'],
    // 网格单元格列表，由 _buildGrid 生成
    gridCells: [],
    // 格式化的「YYYY 年 MM 月」标题
    titleLabel: '',
  },

  observers: {
    'days, calendarYear, calendarMonth': function () {
      this._buildGrid();
    },
  },

  lifetimes: {
    attached: function () {
      this._buildGrid();
    },
  },

  methods: {
    /**
     * 根据 days 数组与当前年月构建 7 列网格。
     *
     * 逻辑：
     * 1. 用 calendarYear/calendarMonth 算出当月第 1 天是周几（0=Sun），
     *    因本日历以周一为第 1 列，需偏移至 Mon=0, Tue=1, ... Sun=6。
     * 2. 在日期格前插入空白占位格（前置偏移）。
     * 3. 将 days 数组中日期在本月内的日（is_trading_day 或非交易日）依次填入。
     * 4. 补足末尾使总格数为 7 的倍数。
     *
     * days 数组可能来自 API，包含本月全部日期（含非交易日占位），
     * 也可能仅含交易日——本组件统一处理：按日期号 1-31 遍历，
     * 在 days 中查找当天数据；找不到则作为占位非交易日渲染。
     */
    _buildGrid: function () {
      var year = this.data.calendarYear;
      var month = this.data.calendarMonth;
      if (!year || !month) return;

      // 格式化标题
      var pad = month < 10 ? '0' + month : '' + month;
      var titleLabel = year + ' 年 ' + pad + ' 月';

      // 当月天数
      var daysInMonth = new Date(year, month, 0).getDate();

      // 当月 1 日是周几：0=Sun,1=Mon,...,6=Sat → 转为 Mon=0,...,Sun=6
      var firstDow = new Date(year, month - 1, 1).getDay(); // 0=Sun
      var offset = firstDow === 0 ? 6 : firstDow - 1; // Mon-based offset

      // 把 days prop 映射成 dateNum → item 的查找表
      var dayMap = {};
      var daysArr = this.data.days || [];
      daysArr.forEach(function (d) {
        if (!d || !d.date) return;
        // date 格式预期为 'YYYY-MM-DD'
        var parts = d.date.split('-');
        if (parts.length < 3) return;
        var y = parseInt(parts[0], 10);
        var m = parseInt(parts[1], 10);
        var n = parseInt(parts[2], 10);
        if (y === year && m === month) {
          dayMap[n] = d;
        }
      });

      var cells = [];

      // 前置空白偏移格
      for (var i = 0; i < offset; i++) {
        cells.push({ key: 'pad-' + i, isEmpty: true, dateNum: 0 });
      }

      // 填充本月每一天
      for (var d = 1; d <= daysInMonth; d++) {
        var item = dayMap[d];
        if (item) {
          // 有数据：取 colorClass 与 daily_profit
          cells.push({
            key: 'day-' + d,
            isEmpty: false,
            dateNum: d,
            isTrading: !!item.is_trading_day,
            colorClass: item.colorClass || 'placeholder',
            dailyProfit: item.is_trading_day ? item.daily_profit : null,
            // 提供给 num-text 的 value（非交易日传 null 不展示数字）
            profitValue: item.is_trading_day
              ? (item.daily_profit != null ? item.daily_profit : null)
              : null,
          });
        } else {
          // 无数据：作为非交易日占位
          cells.push({
            key: 'day-' + d,
            isEmpty: false,
            dateNum: d,
            isTrading: false,
            colorClass: 'placeholder',
            dailyProfit: null,
            profitValue: null,
          });
        }
      }

      // 末尾补全至 7 的倍数（完整行）
      var remainder = cells.length % 7;
      if (remainder !== 0) {
        for (var j = 0; j < 7 - remainder; j++) {
          cells.push({ key: 'tail-' + j, isEmpty: true, dateNum: 0 });
        }
      }

      this.setData({ gridCells: cells, titleLabel: titleLabel });
    },

    /** 上一个月 */
    onPrevMonth: function () {
      var year = this.data.calendarYear;
      var month = this.data.calendarMonth;
      var newMonth = month - 1;
      var newYear = year;
      if (newMonth < 1) {
        newMonth = 12;
        newYear = year - 1;
      }
      this.triggerEvent('monthChange', { year: newYear, month: newMonth });
    },

    /** 下一个月 */
    onNextMonth: function () {
      var year = this.data.calendarYear;
      var month = this.data.calendarMonth;
      var newMonth = month + 1;
      var newYear = year;
      if (newMonth > 12) {
        newMonth = 1;
        newYear = year + 1;
      }
      this.triggerEvent('monthChange', { year: newYear, month: newMonth });
    },
  },
});
