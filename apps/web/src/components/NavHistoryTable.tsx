"use client";

import { memo, useMemo } from "react";
import type { PerformanceSeriesPoint } from "@/lib/performanceTrend";
import { formatSignedPercent } from "@/lib/performanceTrend";

type NavHistoryTableProps = {
  points: PerformanceSeriesPoint[];
  maxRows?: number;
};

function cnDailyReturn(value: number | null) {
  if (value == null || Math.abs(value) < 0.005) {
    return "text-slate-500";
  }
  return value > 0 ? "profit-up" : "profit-down";
}

function NavHistoryTableView({ points, maxRows = 120 }: NavHistoryTableProps) {
  // 原来每次渲染都对最多 260 个净值点重新排序再截断。行数上限（120）本身已经存在，
  // 所以这里不引入分页，只把排序结果缓存下来，行内容与顺序完全不变。
  const rows = useMemo(
    () => [...points].sort((left, right) => right.date.localeCompare(left.date)).slice(0, maxRows),
    [maxRows, points],
  );

  if (rows.length === 0) {
    return (
      <div className="px-4 py-6 text-center text-sm text-slate-500">暂无历史净值数据</div>
    );
  }

  return (
    <div className="overflow-hidden">
      <div className="grid grid-cols-3 border-b border-slate-100 bg-slate-50/80 px-4 py-2 text-[11px] font-semibold text-slate-500">
        <span>日期</span>
        <span className="text-center">净值</span>
        <span className="text-right">日涨幅</span>
      </div>
      <div>
        {rows.map((row) => (
          <div
            key={row.date}
            // 行高固定，屏幕外的行交给浏览器跳过渲染；contain-intrinsic-size 给出
            // 与实际行高一致的占位尺寸，滚动条长度与 CLS 都不受影响。
            style={{ contentVisibility: "auto", containIntrinsicSize: "auto 38px" }}
            className="grid grid-cols-3 border-b border-slate-50 px-4 py-2.5 text-[13px] tabular-nums"
          >
            <span className="text-slate-600">{row.date}</span>
            <span className="text-center font-semibold text-slate-900">{row.nav.toFixed(4)}</span>
            <span className={`text-right font-bold ${cnDailyReturn(row.dailyReturn)}`}>
              {formatSignedPercent(row.dailyReturn)}
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}

// points 由父级 useMemo 产出，引用稳定；净值表最多 120 行、每行 3 格，
// memo 可以把弹窗内其它状态变化带来的整表重排挡掉。
export const NavHistoryTable = memo(NavHistoryTableView);
