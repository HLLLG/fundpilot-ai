import { AlertTriangle, CheckCircle2, CircleAlert, Info, X } from "lucide-react";

export type NoticeTone = "info" | "success" | "warning" | "error";

type InlineNoticeProps = {
  tone?: NoticeTone;
  message: string;
  onDismiss?: () => void;
  action?: { label: string; onClick: () => void };
  className?: string;
};

const TONES = {
  info: {
    icon: Info,
    className: "border-blue-200 bg-blue-50/90 text-blue-950",
    iconClassName: "text-blue-700",
  },
  success: {
    icon: CheckCircle2,
    className: "border-emerald-200 bg-emerald-50/90 text-emerald-950",
    iconClassName: "text-emerald-700",
  },
  warning: {
    icon: AlertTriangle,
    className: "border-amber-200 bg-amber-50/90 text-amber-950",
    iconClassName: "text-amber-700",
  },
  error: {
    icon: CircleAlert,
    className: "border-rose-200 bg-rose-50/90 text-rose-950",
    iconClassName: "text-rose-700",
  },
} as const;

export function InlineNotice({
  tone = "info",
  message,
  onDismiss,
  action,
  className = "",
}: InlineNoticeProps) {
  const config = TONES[tone];
  const Icon = config.icon;
  return (
    <div
      className={`flex items-start gap-3 rounded-2xl border px-3 py-2.5 text-sm ${config.className} ${className}`.trim()}
      role={tone === "error" ? "alert" : "status"}
      aria-live={tone === "error" ? "assertive" : "polite"}
    >
      <Icon className={`mt-1 shrink-0 ${config.iconClassName}`} aria-hidden="true" size={17} />
      <span className="min-w-0 flex-1 break-words leading-6">{message}</span>
      {action ? (
        <button
          type="button"
          onClick={action.onClick}
          className="min-h-11 shrink-0 rounded-full border border-current/20 bg-white/70 px-3 text-xs font-black"
        >
          {action.label}
        </button>
      ) : null}
      {onDismiss ? (
        <button
          type="button"
          onClick={onDismiss}
          className="touch-target -my-1.5 -mr-1.5 inline-flex shrink-0 items-center justify-center rounded-full text-current/70 hover:bg-white/70 hover:text-current"
          aria-label="关闭提示"
        >
          <X aria-hidden="true" size={16} />
        </button>
      ) : null}
    </div>
  );
}
