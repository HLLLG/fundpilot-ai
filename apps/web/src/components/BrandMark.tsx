import { BRAND } from "@/lib/brand";

type BrandMarkProps = {
  size?: "sm" | "md" | "lg";
  showName?: boolean;
  showEnglish?: boolean;
  className?: string;
};

const SIZES = {
  sm: { box: "h-8 w-8", name: "text-base", en: "text-[9px]" },
  md: { box: "h-9 w-9", name: "text-lg", en: "text-[10px]" },
  lg: { box: "h-11 w-11", name: "text-2xl", en: "text-[11px]" },
} as const;

/**
 * 灵析品牌标识：以数据刻度与「析」的拆解感组成专属研究台印记。
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
        className={`brand-seal relative flex items-center justify-center overflow-hidden ${s.box}`}
        aria-hidden="true"
      >
        <span className="brand-seal-rail" />
        <span className="brand-seal-glyph">析</span>
      </span>
      {showName ? (
        <span className="flex flex-col leading-none">
          <span className={`font-display font-bold text-[var(--brand-deep)] ${s.name}`}>
            {BRAND.name}
          </span>
          {showEnglish ? (
            <span className={`mt-1 font-bold uppercase tracking-[0.24em] text-[var(--accent-strong)] ${s.en}`}>
              {BRAND.englishName}
            </span>
          ) : null}
        </span>
      ) : null}
    </span>
  );
}
