import { expect, test } from "./ui-test";
import { expectNoHorizontalOverflow } from "./ui-assertions";

const VISUAL_PROJECTS = new Set(["desktop-1440", "mobile-390", "mobile-320"]);

for (const route of ["/", "/login", "/register"] as const) {
  test(`${route} 稳定视觉基线`, async ({ page }, testInfo) => {
    test.skip(!VISUAL_PROJECTS.has(testInfo.project.name), "视觉回归保留代表性桌面与移动视口");
    await page.addInitScript(() => {
      window.localStorage.clear();
      window.sessionStorage.clear();
    });
    await page.goto(route);
    await expect(page.getByRole("main")).toBeVisible();
    await expectNoHorizontalOverflow(page);
    await expect(page).toHaveScreenshot(`${route === "/" ? "landing" : route.slice(1)}.png`, {
      animations: "disabled",
      fullPage: true,
      caret: "hide",
    });
  });
}
