/**
 * The end-to-end smoke.
 *
 * Every other test in this directory mocks fetch. This one drives the built
 * bundle, in a real browser, against a real control plane holding a real
 * scanned database — which is the only place three classes of bug can show up:
 * a bundle that works in jsdom and not in Chromium, a client whose request
 * shapes the server rejects, and a mount order that serves index.html where
 * JSON was expected.
 *
 * Not part of `npm test`. It needs a browser and a control plane, so it is run
 * on purpose (`make smoke`) rather than on every commit.
 *
 * Exits non-zero on the first failed step, and treats an uncaught page error or
 * a failed request as a failure of its own — a console that renders correctly
 * while throwing in the background is not passing.
 */
import { chromium } from "playwright-core";


const base = process.env.BASE ?? "http://127.0.0.1:8081";
const token = process.env.TOKEN ?? "tok-e2e";

// Set by the caller; the environment's pre-installed Chromium is not where
// playwright-core looks by default.
const executablePath = process.env.CHROMIUM;
if (!executablePath) {
  console.log("CHROMIUM is unset — skipping the browser smoke.");
  process.exit(0);
}

const browser = await chromium.launch({
  executablePath,
  args: [
    "--no-sandbox",
    // Chromium phones home on startup. Those are not the page's requests and
    // would show up as noise in an environment with no egress.
    "--disable-background-networking",
    "--disable-component-update",
    "--no-first-run",
  ],
});
const page = await browser.newPage();

const problems = [];
page.on("console", (m) => { if (m.type() === "error") problems.push(`console: ${m.text()}`); });
page.on("pageerror", (e) => problems.push(`pageerror: ${e.message}`));
page.on("requestfailed", (r) => problems.push(`requestfailed: ${r.url()}`));

await page.goto(base, { waitUntil: "networkidle" });

const step = async (name, fn) => {
  try { await fn(); console.log(`  ok   ${name}`); }
  catch (e) { console.log(`  FAIL ${name}: ${e.message}`); process.exitCode = 1; }
};

await step("sign-in is the first thing shown", async () => {
  await page.getByRole("heading", { name: /sign in/i }).waitFor({ timeout: 5000 });
});

await step("signing in loads the register", async () => {
  await page.getByLabel(/control plane token/i).fill(token);
  await page.getByLabel(/your name/i).fill("ezra@custos.dev");
  await page.getByRole("button", { name: /continue/i }).click();
  await page.getByRole("heading", { name: /unsanctioned agents/i }).waitFor({ timeout: 5000 });
});

await step("all five real agents render", async () => {
  await page.locator("article.finding").first().waitFor({ timeout: 5000 });
  const n = await page.locator("article.finding").count();
  if (n !== 5) throw new Error(`expected 5 findings, saw ${n}`);
});

await step("the most destructive agent is first", async () => {
  const first = await page.locator("article.finding h3").first().textContent();
  if (first?.trim() !== "ops-automation") throw new Error(`first card is ${first}`);
});

await step("the grant button is gated until evidence is opened", async () => {
  const card = page.locator("article.finding").first();
  const grant = card.getByRole("button", { name: /grant imprimatur/i });
  if (!(await grant.isDisabled())) throw new Error("grant was enabled before evidence");
  // The hint text under the disabled button says the same words, so the
  // toggle has to be addressed as the summary element it is.
  await card.locator("summary", { hasText: /why this was flagged/i }).click();
  await grant.waitFor({ state: "visible" });
  if (await grant.isDisabled()) throw new Error("grant still disabled after evidence");
});

await step("evidence is real sentences from the classifier", async () => {
  const text = await page.locator("article.finding").first().innerText();
  if (!/ratio|asymmetr|sent/i.test(text)) throw new Error("no evidence text found");
});

await step("history loads from the API", async () => {
  const card = page.locator("article.finding").first();
  await card.locator("summary", { hasText: /^history$/i }).click();
  await card.locator("ol.history li, p.muted").first().waitFor({ timeout: 5000 });
});

await step("granting is confirmed before it happens", async () => {
  const card = page.locator("article.finding").first();
  await card.getByRole("button", { name: /grant imprimatur/i }).click();
  await page.getByRole("dialog").waitFor({ timeout: 5000 });
});

await step("the grant sanctions the agent", async () => {
  await page.getByRole("dialog").getByRole("button", { name: /^grant as /i }).click();
  await page.getByRole("dialog").waitFor({ state: "detached", timeout: 8000 });
  // The dialog closing and the register refetching are separate steps; the
  // count is only meaningful once the reload has landed.
  await page.locator("article.finding").nth(4).waitFor({ state: "detached", timeout: 8000 });
  const n = await page.locator("article.finding").count();
  if (n !== 4) throw new Error(`expected 4 unsanctioned after granting, saw ${n}`);
});

await step("the sanction is recorded against the operator", async () => {
  await page.getByRole("button", { name: /show all/i }).click();
  await page.getByRole("heading", { name: /the register/i }).waitFor({ timeout: 5000 });
  const body = await page.locator("main").innerText();
  if (!body.includes("ezra@custos.dev")) throw new Error("operator not shown on the sanctioned agent");
});

await step("the page loaded nothing it could not fetch", async () => {
  if (problems.length) throw new Error(problems.join(" | "));
});

await browser.close();
console.log(process.exitCode ? "\nFAILED" : "\nall steps passed");
