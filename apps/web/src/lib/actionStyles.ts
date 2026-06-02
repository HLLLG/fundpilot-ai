export type ActionTone = "add" | "reduce" | "pause" | "watch" | "neutral";

export function actionTone(action: string): ActionTone {
  const text = action.trim();
  if (text.includes("加仓") || text.includes("定投")) return "add";
  if (text.includes("减仓") || text.includes("复核") || text.includes("风控")) return "reduce";
  if (text.includes("暂停")) return "pause";
  if (text.includes("观察")) return "watch";
  return "neutral";
}

const toneClasses: Record<ActionTone, string> = {
  add: "border-emerald-200 bg-emerald-50 text-emerald-900",
  reduce: "border-orange-200 bg-orange-50 text-orange-900",
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
  pause: "border-amber-100 bg-amber-50/60",
  watch: "border-slate-200 bg-slate-50/80",
  neutral: "border-blue-100 bg-blue-50/60",
};

export function actionCardClass(action: string): string {
  return cardToneClasses[actionTone(action)];
}
