import { expect, test } from "./ui-test";
import {
  expectMinimumTapTarget,
  expectNoHorizontalOverflow,
} from "./ui-assertions";

test.beforeEach(async ({ page }) => {
  await page.addInitScript(() => {
    window.localStorage.clear();
    window.sessionStorage.clear();
  });
});

test("公共落地页具备清晰主路径与关键语义", async ({ page }) => {
  const response = await page.goto("/");
  expect(response?.ok()).toBeTruthy();

  await expect(page.locator("html")).toHaveAttribute("lang", "zh-CN");
  await expect(page.getByRole("main")).toBeVisible();
  await expect(
    page.getByRole("heading", { level: 1, name: /截个图.*就懂你的基金/ }),
  ).toBeVisible();
  await expect(page.getByRole("navigation", { name: "账号入口" })).toBeVisible();

  const primaryCta = page.getByTestId("landing-primary-cta");
  await expect(primaryCta).toHaveAccessibleName("免费开始使用");
  await expect(primaryCta).toHaveAttribute("href", "/register");
  await expect(
    page.getByRole("heading", { name: "每一步都可确认，不把识别结果直接当答案" }),
  ).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "放心使用，先把边界说清楚" }),
  ).toBeVisible();

  await expectMinimumTapTarget(primaryCta);
  await expectNoHorizontalOverflow(page);
});

test("移动固定主操作只在首屏主操作离开后出现", async ({ page }, testInfo) => {
  test.skip(!testInfo.project.name.startsWith("mobile-"), "仅移动视口需要固定主操作");
  await page.goto("/");

  await expect(page.getByTestId("landing-primary-cta")).toBeVisible();
  await expect(page.getByTestId("landing-sticky-cta")).toHaveCount(0);
  await page.getByTestId("landing-steps").scrollIntoViewIfNeeded();
  await expect(page.getByTestId("landing-sticky-cta")).toBeVisible();
  await expectNoHorizontalOverflow(page);
});

test("登录页暴露可命名表单控件且移动端不溢出", async ({ page }) => {
  const response = await page.goto("/login");
  expect(response?.ok()).toBeTruthy();

  await expect(page.getByRole("heading", { level: 1, name: "欢迎回来" })).toBeVisible();
  const email = page.getByLabel("邮箱");
  const password = page.getByLabel("密码");
  await expect(email).toHaveAttribute("autocomplete", "email");
  await expect(password).toHaveAttribute("autocomplete", "current-password");
  await expect(email).toHaveAttribute("aria-invalid", "false");
  await expect(page.getByRole("link", { name: "免费注册" })).toHaveAttribute(
    "href",
    "/register",
  );

  const submit = page.getByRole("button", { name: "登录" });
  await expect(submit).toBeEnabled();
  await expect(email).toHaveAttribute("required", "");
  await expect(password).toHaveAttribute("required", "");

  await email.fill("not-an-email");
  await password.fill("Example123!");
  await submit.click();
  expect(
    await email.evaluate((element: HTMLInputElement) => element.validity.typeMismatch),
    "登录页应在本地拦截无效邮箱",
  ).toBe(true);
  await expect(email).toBeFocused();

  await expectMinimumTapTarget(submit);
  await expectNoHorizontalOverflow(page);
});

test("未登录访问设置页会保留目标并跳转登录", async ({ page }) => {
  const response = await page.goto("/settings");
  expect(response?.ok()).toBeTruthy();

  await expect(page).toHaveURL(/\/login\?redirect=%2Fsettings$/);
  await expect(page.getByRole("heading", { level: 1, name: "欢迎回来" })).toBeVisible();
  await expectNoHorizontalOverflow(page);
});

test("注册页提供可访问的本地校验反馈且移动端不溢出", async ({ page }) => {
  const response = await page.goto("/register");
  expect(response?.ok()).toBeTruthy();

  await expect(page.getByRole("heading", { level: 1, name: "创建账号" })).toBeVisible();
  await expect(page.getByLabel("昵称（可选）")).toHaveAttribute("autocomplete", "nickname");
  await expect(page.getByLabel("邮箱")).toHaveAttribute("autocomplete", "email");
  await expect(page.getByLabel("密码", { exact: true })).toHaveAttribute(
    "autocomplete",
    "new-password",
  );
  await expect(page.getByLabel("确认密码")).toHaveAttribute(
    "autocomplete",
    "new-password",
  );

  await page.getByLabel("邮箱").fill("ui-check@example.com");
  await page.getByLabel("密码", { exact: true }).fill("Example123!");
  await page.getByLabel("确认密码").fill("Different123!");
  const submit = page.getByRole("button", { name: "免费注册，开始使用" });
  await submit.click();

  const alert = page.getByRole("alert").filter({ hasText: "两次输入的密码不一致" });
  await expect(alert).toHaveText("两次输入的密码不一致");
  await expect(page.getByLabel("邮箱")).toHaveAttribute("aria-describedby", "register-error");
  await expectMinimumTapTarget(submit);
  await expectNoHorizontalOverflow(page);
});
