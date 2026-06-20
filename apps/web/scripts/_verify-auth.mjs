import { chromium } from "playwright";
import { mkdirSync } from "node:fs";

const BASE = "http://127.0.0.1:3001";
const OUT = "verify-shots";
mkdirSync(OUT, { recursive: true });

const email = `uitest+${Date.now()}@example.com`;
const password = "test12345";

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1024, height: 1500 } });

// 注册一个一次性账号（本地 DB），登录后进入 Dashboard
await page.goto(`${BASE}/register`, { waitUntil: "networkidle", timeout: 20000 });
await page.fill('input[type="email"]', email);
const pwd = page.locator('input[type="password"]');
await pwd.nth(0).fill(password);
await pwd.nth(1).fill(password);
await page.click('button[type="submit"]');
await page.waitForTimeout(2500);

await page.screenshot({ path: `${OUT}/dashboard-empty.png`, fullPage: true });

// 顶部品牌头文字 + Tab 选中色
const brandText = (await page.locator("nav").first().innerText().catch(() => "")).replace(/\s+/g, " ").trim().slice(0, 30);
const activeTabBg = await page.evaluate(() => {
  const el = document.querySelector('.tab-segment-btn[aria-current="page"]');
  return el ? getComputedStyle(el).backgroundColor : null;
});

// 切到「市场」Tab
let marketInfo = {};
try {
  await page.getByRole("button", { name: "市场" }).click();
  await page.waitForTimeout(3500);
  await page.screenshot({ path: `${OUT}/market.png`, fullPage: true });
  // 读取板块种类徽章（指数/行业/概念）的实际颜色
  marketInfo = await page.evaluate(() => {
    const findBadge = () => {
      const nodes = Array.from(document.querySelectorAll("span"));
      return nodes.find((n) => ["指数", "行业", "概念"].includes(n.textContent?.trim() ?? ""));
    };
    const badge = findBadge();
    return badge
      ? { badgeText: badge.textContent.trim(), badgeColor: getComputedStyle(badge).color, badgeBg: getComputedStyle(badge).backgroundColor }
      : { note: "no badge found yet" };
  });
} catch (e) {
  marketInfo = { error: String(e).slice(0, 120) };
}

console.log(JSON.stringify({ email, brandText, activeTabBg, marketInfo }, null, 2));
await browser.close();
