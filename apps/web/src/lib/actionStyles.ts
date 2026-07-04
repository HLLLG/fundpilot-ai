// M2.2：动作词表扩展新增「大幅减仓评估」「清仓评估」两档更强烈的减仓动作，须比
// 普通「减仓评估」更醒目（设计文档原文："比现有'减仓评估'更强调的配色，如深红/警示条纹"），
// 因此单独拆出 deep_reduce/clear_all 两档 tone，不再和 reduce 共用同一套配色。
export type ActionTone = "add" | "reduce" | "deep_reduce" | "clear_all" | "pause" | "watch" | "neutral";

/** 是否为需要二次确认展开的极端动作（M5：设计文档第10节决策#3）。 */
export function isExtremeAction(action: string): boolean {
  const text = action.trim();
  return text.includes("清仓") || text.includes("大幅减仓");
}

export function actionTone(action: string): ActionTone {
  const text = action.trim();
  // 识别顺序很重要：更强烈的减仓词必须先于泛化的"减仓"关键词判断，否则
  // "大幅减仓评估"/"清仓评估" 会被"减仓"子串误判为普通 reduce 档位。
  if (text.includes("清仓")) return "clear_all";
  if (text.includes("大幅减仓")) return "deep_reduce";
  if (text.includes("加仓") || text.includes("定投")) return "add";
  if (text.includes("减仓") || text.includes("复核") || text.includes("风控")) return "reduce";
  if (text.includes("暂停")) return "pause";
  if (text.includes("观察")) return "watch";
  return "neutral";
}

const toneClasses: Record<ActionTone, string> = {
  add: "border-emerald-200 bg-emerald-50 text-emerald-900",
  reduce: "border-orange-200 bg-orange-50 text-orange-900",
  deep_reduce: "border-rose-300 bg-rose-100 text-rose-900",
  clear_all: "border-rose-400 bg-rose-200 text-rose-950",
  pause: "border-amber-200 bg-amber-50 text-amber-900",
  watch: "border-slate-200 bg-slate-100 text-slate-800",
  neutral: "border-blue-100 bg-blue-50 text-blue-900",
};

export function actionBadgeClass(action: string): string {
  return toneClasses[actionTone(action)];
}

const cardToneClasses: Record<ActionTone, string> = {
  add: "border-emerald-100 bg-emerald-50/70",
  reduce: "border-orange-100 bg-orange-50/70",
  deep_reduce: "border-rose-200 bg-rose-50/80",
  clear_all: "border-rose-300 bg-rose-50",
  pause: "border-amber-100 bg-amber-50/60",
  watch: "border-slate-200 bg-slate-50/80",
  neutral: "border-blue-100 bg-blue-50/60",
};

export function actionCardClass(action: string): string {
  return cardToneClasses[actionTone(action)];
}
