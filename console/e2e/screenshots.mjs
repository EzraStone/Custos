/**
 * The images in the README.
 *
 * Three, chosen because each carries one claim the prose makes and cannot
 * demonstrate: that findings are ordered by consequence, that the evidence is
 * specific enough to argue with, and that sanctioning shows its scope first.
 *
 * Cropped rather than full-page. A 4,500px tall screenshot of five cards is
 * unreadable in a README and says less than two cards do.
 *
 * Run through `make screenshots`, which boots the stack around it.
 */
import { chromium } from "playwright-core";

const OUT = process.env.OUT;
const BASE = process.env.BASE ?? "http://127.0.0.1:8080";
const TOKEN = process.env.TOKEN ?? "tok-stack";
const executablePath = process.env.CHROMIUM;

if (!OUT || !executablePath) {
  console.log("OUT and CHROMIUM must be set — run this through make screenshots.");
  process.exit(1);
}

const WIDTH = 1160;

const browser = await chromium.launch({
  executablePath,
  args: [
    "--no-sandbox",
    // Chromium phones home on startup. Not the page's requests, and noise in
    // an environment with no egress.
    "--disable-background-networking",
    "--disable-component-update",
    "--no-first-run",
  ],
});

async function signedIn(height) {
  const page = await browser.newPage({
    viewport: { width: WIDTH, height },
    // Retina, so the evidence text is legible when a reader zooms in. It is
    // the point of one of these three images.
    deviceScaleFactor: 2,
  });
  await page.goto(BASE, { waitUntil: "networkidle" });
  await page.getByLabel(/control plane token/i).fill(TOKEN);
  await page.getByLabel(/your name/i).fill("ezra@custos.dev");
  await page.getByRole("button", { name: /continue/i }).click();
  await page.locator("article.finding").first().waitFor({ timeout: 10000 });
  return page;
}

const openEvidence = (card) =>
  card.locator("summary", { hasText: /why this was flagged/i }).click();

// 1. The register, cut off after the second finding. Destructive above write,
//    at equal confidence — which is the ordering claim, visible in one image.
{
  const page = await signedIn(1200);
  const box = await page.locator("article.finding").nth(1).boundingBox();
  await page.screenshot({
    path: `${OUT}/register.png`,
    clip: { x: 0, y: 0, width: WIDTH, height: Math.ceil(box.y + box.height + 16) },
  });
  console.log("       register.png");
  await page.close();
}

// 2. One card with everything open: the classifier's own sentences, the audit
//    trail, and the grant control enabled now that the evidence has been read.
{
  const page = await signedIn(1400);
  const card = page.locator("article.finding").first();
  await openEvidence(card);
  await card.locator("summary", { hasText: /^history$/i }).click();
  await card.locator("ol.history li").first().waitFor({ timeout: 10000 });
  await card.screenshot({ path: `${OUT}/evidence.png` });
  console.log("       evidence.png");
  await page.close();
}

// 3. The confirmation. Viewport rather than full page, so the overlay fills
//    the frame the way it fills a screen.
{
  const page = await signedIn(900);
  const card = page.locator("article.finding").first();
  await openEvidence(card);
  await card.getByRole("button", { name: /grant imprimatur/i }).click();
  await page.getByRole("dialog").waitFor({ timeout: 10000 });
  await page.screenshot({ path: `${OUT}/grant.png` });
  console.log("       grant.png");
  await page.close();
}

await browser.close();
