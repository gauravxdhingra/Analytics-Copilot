import { execFileSync } from "node:child_process";
import path from "node:path";
import { type Browser, type Page, expect, test } from "@playwright/test";

const root = path.resolve(import.meta.dirname, "../../..");
const password = process.env.DEMO_USER_PASSWORD ?? "";
const expected: Record<string, number> = JSON.parse(
  execFileSync("uv", ["run", "python", "apps/web/e2e/expected_counts.py"], { cwd: root }).toString(),
);

async function signIn(page: Page, username: string, pass = password) {
  await page.goto("/");
  await page.getByRole("link", { name: "Sign in" }).click();
  await page.locator("#username").fill(username);
  await page.locator("#password").fill(pass);
  await page.locator("#kc-login").click();
}

async function ask(page: Page, question: string) {
  await page.getByRole("textbox", { name: "Question" }).fill(question);
  await page.getByRole("textbox", { name: "Question" }).press("Enter");
}

async function asUser(browser: Browser, username: string) {
  const context = await browser.newContext();
  const page = await context.newPage();
  await signIn(page, username);
  await expect(page.getByText(username, { exact: true })).toBeVisible();
  return page;
}

const QUESTION = "How many booked transactions are there?";

test("two analysts ask the same question and each sees only their own entity", async ({ browser }) => {
  const [ana, ben] = [await asUser(browser, "ana.analyst"), await asUser(browser, "ben.analyst")];
  expect(expected["ana.analyst"]).not.toBe(expected["ben.analyst"]);
  for (const [page, user] of [
    [ana, "ana.analyst"],
    [ben, "ben.analyst"],
  ] as const) {
    await ask(page, QUESTION);
    const cell = page.getByRole("table").getByRole("cell").first();
    await expect(cell).toHaveText(String(expected[user]));
  }
});

test("a manager sees both entities", async ({ browser }) => {
  const maria = await asUser(browser, "maria.manager");
  await expect(maria.getByText(/HAL-DE, HAL-UK/)).toBeVisible();
  await ask(maria, QUESTION);
  await expect(maria.getByRole("table").getByRole("cell").first()).toHaveText(String(expected["maria.manager"]));
});

test("the drawer shows the verified query, its parameters and the user's own trace", async ({ browser }) => {
  const ana = await asUser(browser, "ana.analyst");
  await ask(ana, QUESTION);
  await ana.getByRole("button", { name: "How I got this" }).click();
  const drawer = ana.getByRole("dialog", { name: "How I got this" });
  await expect(drawer.getByText(/SELECT/)).toBeVisible();
  await expect(drawer.getByText("Parameters:")).toBeVisible();
  await expect(drawer.getByRole("heading", { name: "Trace" })).toBeVisible();
  // The user's own trace, read back from the tracing backend through the BFF.
  await expect(drawer.getByText("agent.run")).toBeVisible({ timeout: 20_000 });
});

test("a wrong password never reaches the app", async ({ page }) => {
  await signIn(page, "ana.analyst", "definitely-not-the-password");
  await expect(page.locator("#kc-login")).toBeVisible(); // still at the identity provider
  const me = await page.request.get("/api/me");
  expect(me.status()).toBe(401);
});

test("no token is ever readable by the page", async ({ browser }) => {
  const ana = await asUser(browser, "ana.analyst");
  const exposed = await ana.evaluate(() => ({
    cookies: document.cookie,
    local: JSON.stringify({ ...localStorage }),
    session: JSON.stringify({ ...sessionStorage }),
  }));
  expect(JSON.stringify(exposed)).not.toContain("eyJ");
  expect(exposed.cookies).not.toContain("copilot_session"); // httpOnly
});

test("only approvers get the approvals tab", async ({ browser }) => {
  const ana = await asUser(browser, "ana.analyst");
  await expect(ana.getByRole("tab", { name: "Approvals" })).toHaveCount(0);
  const amit = await asUser(browser, "amit.approver");
  await expect(amit.getByRole("tab", { name: "Approvals" })).toBeVisible();
});

test("signing out ends the session", async ({ browser }) => {
  const ana = await asUser(browser, "ana.analyst");
  await ana.getByRole("button", { name: "Sign out" }).click();
  await ana.waitForURL(/localhost:(3000|8081)/);
  const me = await ana.request.get("/api/me");
  expect(me.status()).toBe(401);
});
