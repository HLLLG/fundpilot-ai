"use client";

type UserAvatarProps = {
  name: string;
  avatarUrl?: string | null;
  size?: "sm" | "md" | "lg";
};

const SIZE_CLASS = {
  sm: "h-9 w-9 text-sm",
  md: "h-12 w-12 text-base",
  lg: "h-14 w-14 text-lg",
} as const;

export function UserAvatar({ name, avatarUrl, size = "md" }: UserAvatarProps) {
  const initial = (name.trim() || "用户").slice(0, 1).toUpperCase();
  if (avatarUrl) {
    return (
      // eslint-disable-next-line @next/next/no-img-element
      <img
        src={avatarUrl}
        alt=""
        className={`${SIZE_CLASS[size]} rounded-full object-cover`}
      />
    );
  }
  return (
    <span
      className={`inline-flex ${SIZE_CLASS[size]} items-center justify-center rounded-full bg-gradient-to-br from-[var(--brand)] to-[var(--brand-strong)] font-black text-white shadow-[var(--shadow-brand)] ring-2 ring-[var(--panel)]`}
    >
      {initial}
    </span>
  );
}
