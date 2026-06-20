import { Sparkles } from "lucide-react";

type BrandMarkProps = {
  size?: "sm" | "md" | "lg";
  showName?: boolean;
  showEnglish?: boolean;
  className?: string;
};

const SIZES = {
  sm: { box: "h-8 w-8 rounded-xl", icon: 16, name: "text-base", en: "text-[10px]" },
  md: { box: "h-9 w-9 rounded-xl", icon: 18, name: "text-lg", en: "text-[11px]" },
  lg: { box: "h-12 w-12 rounded-2xl", icon: 24, name: "text-2xl", en: "text-xs" },
} as const;

/**
 * 好基灵品牌标识：品牌蓝圆角图标 + 中文名（可选英文辅助）。
 * 用于落地页 / 登录注册 / Dashboard 顶部，保持一致性。
 */
export function BrandMark({
  size = "md",
  showName = true,
  showEnglish = false,
  className = "",
}: BrandMarkProps) {
  const s = SIZES[size];
  return (
    <span className={`inline-flex items-center gap-2.5 ${className}`.trim()}>
      <span
        className={`flex items-center justify-center text-white shadow-[0_6px_16px_rgba(35,86,224,0.30)] ${s.box}`}
        style={{
          background: "linear-gradient(180deg, var(--brand) 0%, var(--brand-strong) 100%)",
        }}
      >
        <Sparkles size={s.icon} strokeWidth={2.4} />
      </span>
      {showName ? (
        <span className="flex flex-col leading-none">
          <span className={`font-black tracking-tight text-slate-950 ${s.name}`}>好基灵</span>
          {showEnglish ? (
            <span className={`mt-0.5 font-bold uppercase tracking-[0.18em] text-slate-400 ${s.en}`}>
              FundPilot
            </span>
          ) : null}
        </span>
      ) : null}
    </span>
  );
}
