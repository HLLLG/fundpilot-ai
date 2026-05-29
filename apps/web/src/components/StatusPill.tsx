type StatusPillProps = {
  tone?: "blue" | "green" | "amber" | "red" | "dark";
  children: React.ReactNode;
};

const tones = {
  blue: "border-blue-200 bg-blue-50 text-blue-700",
  green: "border-emerald-200 bg-emerald-50 text-emerald-700",
  amber: "border-amber-200 bg-amber-50 text-amber-700",
  red: "border-rose-200 bg-rose-50 text-rose-700",
  dark: "border-slate-200 bg-slate-950 text-white",
};

export function StatusPill({ tone = "blue", children }: StatusPillProps) {
  return (
    <span className={`inline-flex items-center rounded-full border px-3 py-1 text-xs font-semibold ${tones[tone]}`}>
      {children}
    </span>
  );
}
