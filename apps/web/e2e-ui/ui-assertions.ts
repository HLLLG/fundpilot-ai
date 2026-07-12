import { expect, type Locator, type Page } from "@playwright/test";

export async function expectNoHorizontalOverflow(page: Page): Promise<void> {
  const measurement = await page.evaluate(() => {
    const root = document.documentElement;
    const body = document.body;
    const contentWidth = Math.max(root.scrollWidth, body?.scrollWidth ?? 0);
    const viewportWidth = root.clientWidth;
    const offenders = Array.from(document.querySelectorAll<HTMLElement>("body *"))
      .map((element) => {
        const rect = element.getBoundingClientRect();
        return {
          tag: element.tagName.toLowerCase(),
          testId: element.dataset.testid ?? null,
          left: Math.round(rect.left),
          right: Math.round(rect.right),
        };
      })
      .filter((item) => item.left < -1 || item.right > window.innerWidth + 1)
      .slice(0, 5);

    return {
      contentWidth,
      viewportWidth,
      overflow: contentWidth - viewportWidth,
      offenders,
    };
  });

  expect(
    measurement.overflow,
    `页面横向溢出 ${measurement.overflow}px；疑似元素：${JSON.stringify(measurement.offenders)}`,
  ).toBeLessThanOrEqual(1);
}

export async function expectMinimumTapTarget(locator: Locator): Promise<void> {
  await expect(locator).toBeVisible();
  const box = await locator.boundingBox();
  expect(box, "关键操作应具备可测量的触控区域").not.toBeNull();
  expect(box?.width ?? 0, "移动端关键操作宽度至少应为 44px").toBeGreaterThanOrEqual(44);
  expect(box?.height ?? 0, "移动端关键操作高度至少应为 44px").toBeGreaterThanOrEqual(44);
}
