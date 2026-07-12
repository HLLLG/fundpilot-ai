export function safeLoginRedirect(raw: string | null): string {
  if (!raw) {
    return "/";
  }
  try {
    const base = new URL("https://lingxi.local");
    const target = new URL(raw, base);
    if (target.origin !== base.origin || !target.pathname.startsWith("/")) {
      return "/";
    }
    return `${target.pathname}${target.search}${target.hash}`;
  } catch {
    return "/";
  }
}
