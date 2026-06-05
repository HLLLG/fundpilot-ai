export function notifyDesktop(title: string, options?: NotificationOptions) {
  if (typeof window === "undefined" || !("Notification" in window)) {
    return;
  }
  if (Notification.permission !== "granted") {
    return;
  }
  new Notification(title, options);
}
