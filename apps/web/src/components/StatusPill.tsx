type StatusPillProps = {
  tone?: "blue" | "green" | "amber" | "red" | "dark";
  children: React.ReactNode;
};

// 语义色统一走 :root token（--info/success/warn/danger）；
// "dark" 保留用于品牌深底徽标（如 "已确认" 权威徽标）。
const tones = {
  blue: "status-info",
  green: "status-good",
  amber: "status-warn",
  red: "status-bad",
  dark: "border-[var(--brand-deep)] bg-[var(--brand-deep)] text-white",
};

export function StatusPill({ tone = "blue", children }: StatusPillProps) {
  return (
    <span className={`inline-flex items-center rounded-full border px-3 py-1 text-xs font-semibold ${tones[tone]}`}>
      {children}
    </span>
  );
}
