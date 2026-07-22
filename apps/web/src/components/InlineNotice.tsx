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
  info: { icon: Info, toneClass: "inline-notice-info" },
  success: { icon: CheckCircle2, toneClass: "inline-notice-success" },
  warning: { icon: AlertTriangle, toneClass: "inline-notice-warning" },
  error: { icon: CircleAlert, toneClass: "inline-notice-error" },
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
      className={`inline-notice ${config.toneClass} ${className}`.trim()}
      role={tone === "error" ? "alert" : "status"}
      aria-live={tone === "error" ? "assertive" : "polite"}
    >
      <Icon className="inline-notice-icon" aria-hidden="true" size={17} />
      <span className="inline-notice-message">{message}</span>
      {action ? (
        <button
          type="button"
          onClick={action.onClick}
          className="inline-notice-action"
        >
          {action.label}
        </button>
      ) : null}
      {onDismiss ? (
        <button
          type="button"
          onClick={onDismiss}
          className="inline-notice-dismiss touch-target"
          aria-label="关闭提示"
        >
          <X aria-hidden="true" size={16} />
        </button>
      ) : null}
    </div>
  );
}
