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

/**
 * Poll until a condition holds.
 *
 * Playwright's locator waits cover visibility and attachment, not "React has
 * re-rendered with new state". Asserting on a property immediately after a
 * click that triggers a state update is a race, and it lost about one run in
 * three — the flakiest possible way to learn that, since two green runs look
 * like proof.
 */
const until = async (what, holds, ms = 5000) => {
  const deadline = Date.now() + ms;
  for (;;) {
    if (await holds()) return;
    if (Date.now() > deadline) throw new Error(what);
    await new Promise((resolve) => setTimeout(resolve, 50));
  }
};

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
  await until("grant still disabled after evidence", async () => !(await grant.isDisabled()));
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

// Retiring is the other mutation, and the newer one. It is exercised here
// rather than only in jsdom because it is the path where a real browser and a
// real control plane disagree most cheaply: a status transition the server
// refuses looks identical to one it accepted until the register reloads.
await step("retiring asks why, and will not proceed without an answer", async () => {
  const card = page.locator("article.finding").first();
  await card.getByRole("button", { name: /^retire$/i }).click();
  const dialog = page.getByRole("dialog");
  await dialog.waitFor({ timeout: 5000 });

  const confirm = dialog.getByRole("button", { name: /^retire$/i });
  await until("retire was available with no reason given", async () => confirm.isDisabled());

  await dialog.getByLabel(/why/i).fill("decommissioned in DEP-812");
  await until("retire stayed disabled after a reason", async () => !(await confirm.isDisabled()));
});

await step("the retired agent leaves the unsanctioned list", async () => {
  const before = await page.locator("article.finding").count();
  await page.getByRole("dialog").getByRole("button", { name: /^retire$/i }).click();
  await page.getByRole("dialog").waitFor({ state: "detached", timeout: 8000 });
  await until(
    "the register did not reload after retiring",
    async () => (await page.locator("article.finding").count()) === before - 1,
    8000,
  );
});

await step("the retired agent says it is retired, not sanctioned", async () => {
  // Retiring clears the imprimatur and takes the agent out of the unsanctioned
  // set, which once put it in the sanctioned branch with nobody to name.
  await page.getByRole("button", { name: /show all/i }).click();
  await page.getByRole("heading", { name: /the register/i }).waitFor({ timeout: 5000 });
  await page.getByRole("button", { name: /^retired$/i }).click();

  const retired = page.locator("article.finding").first();
  await retired.waitFor({ timeout: 5000 });
  const text = await retired.innerText();
  if (!/retired\./i.test(text)) throw new Error(`retired card reads: ${text.slice(0, 200)}`);
  if (/sanctioned by/i.test(text)) throw new Error("a retired agent claims it was sanctioned");
});

await step("the sanction is recorded against the operator", async () => {
  // Already in the "all" view from the previous step; clear the status filter
  // so the sanctioned agent is visible again.
  await page.getByRole("button", { name: /any status/i }).click();
  const body = await page.locator("main").innerText();
  if (!body.includes("ezra@custos.dev")) throw new Error("operator not shown on the sanctioned agent");
});

await step("the page loaded nothing it could not fetch", async () => {
  if (problems.length) throw new Error(problems.join(" | "));
});


// Appended after the main flow: a theme check needs its own page, because a
// colour scheme is fixed when the context is created.
await step("dark mode has no light-mode rectangles in it", async () => {
  const dark = await browser.newPage({ viewport: { width: 1160, height: 900 }, colorScheme: "dark" });
  await dark.goto(base, { waitUntil: "networkidle" });
  await dark.getByLabel(/control plane token/i).fill(token);
  await dark.getByLabel(/your name/i).fill("ezra@custos.dev");
  await dark.getByRole("button", { name: /continue/i }).click();
  await dark.locator("article.finding").first().waitFor({ timeout: 10000 });

  // The search box was outside the .field selector that themed every other
  // input, so it rendered as a white rectangle. Checking every control rather
  // than that one, because the next one added will be outside it too.
  const ground = await dark.evaluate(() => {
    const luminance = (colour) => {
      const [r, g, b] = colour.match(/\d+/g).map(Number);
      return (0.299 * r + 0.587 * g + 0.114 * b) / 255;
    };
    return [...document.querySelectorAll("input, select, textarea")]
      .map((el) => ({ el: el.getAttribute("aria-label") ?? el.type, l: luminance(getComputedStyle(el).backgroundColor) }))
      .filter((x) => x.l > 0.5)
      .map((x) => x.el);
  });
  if (ground.length > 0) throw new Error(`light controls in dark mode: ${ground.join(", ")}`);
  await dark.close();
});

await browser.close();
console.log(process.exitCode ? "\nFAILED" : "\nall steps passed");
