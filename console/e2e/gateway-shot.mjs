/**
 * The gateway question, from the stress corpus.
 *
 * Its own script and its own stack because the base corpus deliberately has no
 * hidden gateway — every agent there reaches a provider we recognise. Making
 * the main screenshots run against the stress corpus would change every image
 * to show a harder account than the one the rest of the README describes, and
 * inventing a candidate for the picture would be showing a feature working on
 * data chosen to make it work.
 */
import { chromium } from "playwright-core";

const OUT = process.env.OUT;
const BASE = process.env.BASE;
const TOKEN = process.env.TOKEN;
const executablePath = process.env.CHROMIUM;

if (!OUT || !executablePath) {
  console.log("OUT and CHROMIUM must be set — run this through make screenshots.");
  process.exit(1);
}

const browser = await chromium.launch({
  executablePath,
  args: ["--no-sandbox", "--disable-background-networking", "--no-first-run"],
});
const page = await browser.newPage({
  viewport: { width: 1160, height: 1000 },
  deviceScaleFactor: 2,
});

await page.goto(BASE, { waitUntil: "networkidle" });
await page.getByLabel(/control plane token/i).fill(TOKEN);
await page.getByLabel(/your name/i).fill("ezra@custos.dev");
await page.getByRole("button", { name: /continue/i }).click();

const asking = page.getByRole("heading", { name: /is one of these a model gateway/i });
await asking.waitFor({ timeout: 10000 });

const section = page.locator("section.gateways");
const box = await section.boundingBox();
await page.screenshot({
  path: `${OUT}/gateway.png`,
  clip: { x: 0, y: 0, width: 1160, height: Math.ceil(box.y + box.height + 20) },
});
console.log("       gateway.png");
await browser.close();
