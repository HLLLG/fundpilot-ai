import {
  expect,
  test as base,
  type ConsoleMessage,
} from "@playwright/test";

type RuntimeErrorFixtures = {
  runtimeErrorMonitor: void;
};

function isAllowedToolingNoise(message: string, sourceUrl: string): boolean {
  const combined = `${message}\n${sourceUrl}`;
  if (/\b(?:chrome|moz|safari-web)-extension:\/\//i.test(combined)) {
    return true;
  }

  return (
    /WebSocket connection .* failed/i.test(message) &&
    (message.includes("/_next/webpack-hmr") || sourceUrl.includes("/_next/webpack-hmr"))
  );
}

export const test = base.extend<RuntimeErrorFixtures>({
  runtimeErrorMonitor: [
    async ({ page }, use, testInfo) => {
      const unexpected: string[] = [];

      const onPageError = (error: Error) => {
        const detail = error.stack || error.message;
        if (!isAllowedToolingNoise(detail, "")) {
          unexpected.push(`[pageerror] ${detail}`);
        }
      };
      const onConsole = (message: ConsoleMessage) => {
        const type = message.type();
        if (type !== "error" && type !== "warning") {
          return;
        }
        const location = message.location();
        const sourceUrl = location.url ?? "";
        const detail = message.text();
        if (!isAllowedToolingNoise(detail, sourceUrl)) {
          const locationLabel = sourceUrl
            ? ` (${sourceUrl}:${location.lineNumber ?? 0}:${location.columnNumber ?? 0})`
            : "";
          unexpected.push(`[console.${type}] ${detail}${locationLabel}`);
        }
      };

      page.on("pageerror", onPageError);
      page.on("console", onConsole);
      await use();
      await page.waitForTimeout(50).catch(() => undefined);
      page.off("pageerror", onPageError);
      page.off("console", onConsole);

      if (unexpected.length > 0) {
        await testInfo.attach("unexpected-browser-runtime-errors", {
          body: unexpected.join("\n\n"),
          contentType: "text/plain",
        });
      }
      expect(
        unexpected,
        `检测到非预期 pageerror / console.error / console.warn：\n${unexpected.join("\n\n")}`,
      ).toEqual([]);
    },
    { auto: true },
  ],
});

export { expect };
