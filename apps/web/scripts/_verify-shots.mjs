import { chromium } from "@playwright/test";
import { mkdirSync } from "node:fs";

const BASE = "http://127.0.0.1:3001";
const OUT = "verify-shots";
mkdirSync(OUT, { recursive: true });

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1024, height: 1400 } });

async function inspect(path, name) {
  const url = `${BASE}${path}`;
  await page.goto(url, { waitUntil: "networkidle", timeout: 20000 });
  await page.waitForTimeout(800);
  await page.screenshot({ path: `${OUT}/${name}.png`, fullPage: true });

  const tokens = await page.evaluate(() => {
    const s = getComputedStyle(document.documentElement);
    return {
      brand: s.getPropertyValue("--brand").trim(),
      brandStrong: s.getPropertyValue("--brand-strong").trim(),
      accent: s.getPropertyValue("--accent").trim(),
      background: s.getPropertyValue("--background").trim(),
      radiusCard: s.getPropertyValue("--radius-card").trim(),
    };
  });

  const primaryBtn = await page.evaluate(() => {
    const el = document.querySelector(".btn-primary");
    if (!el) return null;
    const cs = getComputedStyle(el);
    return { backgroundImage: cs.backgroundImage.slice(0, 80), borderRadius: cs.borderRadius };
  });

  const title = await page.title();
  const h1 = (await page.locator("h1").first().textContent().catch(() => null)) ?? "(none)";
  return { name, url, title, h1: h1.replace(/\s+/g, " ").trim().slice(0, 40), tokens, primaryBtn };
}

const results = [];
results.push(await inspect("/", "landing"));
results.push(await inspect("/login", "login"));
results.push(await inspect("/register", "register"));

console.log(JSON.stringify(results, null, 2));
await browser.close();
