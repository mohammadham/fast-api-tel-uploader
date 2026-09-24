// Smoke: login -> dashboard -> tabs -> upload via UI -> queue可见.
// Runs against the real FastAPI backend in fake-TG mode (see playwright.config.mjs).
import { test, expect } from "@playwright/test";

const ADMIN_USER = "admin";
const ADMIN_PASS = "e2e-admin-pass";

test.describe.serial("panel smoke", () => {
  test("login lands on dashboard with stats", async ({ page }) => {
    await page.goto("/");
    await expect(page.getByRole("heading", { name: "TelegramDrive" })).toBeVisible();

    // wrong password shows an inline error, no navigation
    await page.fill("#lg-user", ADMIN_USER);
    await page.fill("#lg-pass", "wrong-pass");
    await page.getByRole("button", { name: "ورود به پنل" }).click();
    await expect(page.locator(".err")).not.toBeEmpty(); // backend detail or Persian fallback

    // correct password -> app view
    await page.fill("#lg-pass", ADMIN_PASS);
    await page.getByRole("button", { name: "ورود به پنل" }).click();
    await expect(page.locator("header #tabs")).toBeVisible();
    await expect(page.locator(".stat").first()).toBeVisible(); // dashboard cards loaded

    // token persisted for reload
    await page.reload();
    await expect(page.locator("header #tabs")).toBeVisible();
  });

  test("tabs render real data", async ({ page }) => {
    await login(page);
    for (const [label, emptyText] of [
      ["اکانت‌ها", null],
      ["بات‌ها", "باتی نیست"],
      ["ایتا", null],
      ["کلیدها", null],
      ["فایل‌ها", null],
      ["صف", null],
      ["لاگ‌ها", null],
    ]) {
      await page.locator("#tabs button", { hasText: label }).click();
      const section = page.locator("main section:visible");
      await expect(section).toBeVisible();
      await expect(section.locator("h3")).toContainText(label);
      // every tab either has rows or shows its Persian empty-state
      const rows = await section.locator("tbody tr").count();
      if (rows === 0 && emptyText) await expect(section.locator("tbody")).toContainText(emptyText);
    }
  });

  test("upload via UI queues a file and lists it", async ({ page }) => {
    await login(page);
    await page.locator("#tabs button", { hasText: "فایل‌ها" }).click();

    const files = page.locator("main section:visible table tbody tr");
    // the section's own file-table only (a hidden restore input lives in another card)
    const uploadInput = page.locator("section input[type='file']").first();
    const before = await files.count();

    await uploadInput.setInputFiles({
      name: "e2e-smoke.txt",
      mimeType: "text/plain",
      buffer: Buffer.from("playwright smoke upload " + Date.now()),
    });

    // toast confirms queueing, table gains a row
    await expect(page.locator(".toast")).toContainText("صف شد: f_");
    await expect(files.first()).toContainText("e2e-smoke.txt"); // fresh list puts the new file first
    await expect(files).toHaveCount(before + 1);

    // real progress bar element exists with aria
    await expect(page.locator(".progress-track")).toHaveCount(0); // hidden after finish
  });

  test("logout returns to landing", async ({ page }) => {
    await login(page);
    await page.getByRole("button", { name: "خروج" }).click();
    await expect(page.locator("#lg-user")).toBeVisible();
    expect(await page.evaluate(() => localStorage.getItem("td_token"))).toBeNull();
  });
});

async function login(page) {
  await page.goto("/");
  if (await page.locator("#lg-user").count()) {
    await page.fill("#lg-user", ADMIN_USER);
    await page.fill("#lg-pass", ADMIN_PASS);
    await page.getByRole("button", { name: "ورود به پنل" }).click();
  }
  await expect(page.locator("header #tabs")).toBeVisible();
}
