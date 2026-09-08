/**
 * The gateway question and the maybe it explains, from the stress corpus.
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

// The review band, on the same account. Deliberately from the stress corpus
// too: it is the account with the hardest workloads, so the maybes here are
// the interesting ones rather than the tidy ones.
//
// What is NOT in this picture is the point. The agent behind the undeclared
// gateway used to sit at the top of this list at 0.77, on signals that had
// nothing to measure. It is not here any more; it is in the question above.
const reviews = page.locator("details.reviews");
await reviews.scrollIntoViewIfNeeded();
if (!(await reviews.getAttribute("open"))) {
  await reviews.locator("summary").click();
}
await reviews.getByText(/unsure about/i).first().waitFor({ timeout: 10000 });
await reviews.screenshot({ path: `${OUT}/reviews.png` });
console.log("       reviews.png");

await browser.close();
