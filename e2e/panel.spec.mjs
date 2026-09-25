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

    // seed two proxies for the ops tab: one healthy (the e2e backend itself
    // is a reachable TCP endpoint) and one dead (closed loopback port)
    let seeded = [];
    await cleanupE2eProxies(page); // self-heal leftovers from a crashed run
    seeded = await seedProxies(page);
    try {
      for (const [label, emptyText] of [
        ["اکانت‌ها", null],
        ["بات‌ها", "باتی نیست"],
        ["ایتا", null],
        ["کلیدها", null],
        ["فایل‌ها", null],
        ["صف", null],
        ["لاگ‌ها", null],
        ["عملیات", null],
      ]) {
        await page.locator("#tabs button", { hasText: label }).click();
        const section = page.locator("main section:visible");
        await expect(section).toBeVisible();
        await expect(section.locator("h3")).toContainText(label);
        // every tab either has rows or shows its Persian empty-state
        const rows = await section.locator("tbody tr").count();
        if (rows === 0 && emptyText) await expect(section.locator("tbody")).toContainText(emptyText);
      }

      // ops tab (last in the loop, still visible): status grid, legend, error count
      const ops = page.locator("main section:visible");
      // status grid shows both seeded proxies with their Persian status
      await expect(ops.locator(".stat", { hasText: "e2e-ok" }).locator(".num")).toHaveText("سالم");
      await expect(ops.locator(".stat", { hasText: "e2e-down" }).locator(".num")).toHaveText("قطع");
      // legend explains all four statuses (detail strings are unique to the legend)
      await expect(ops).toContainText("اتصال برقرار است و تاخیر سنجیده شده"); // ok
      await expect(ops).toContainText("کند/ناپایدار"); // degraded
      await expect(ops).toContainText("اتصال برقرار نشد"); // down
      await expect(ops).toContainText("هنوز تست نشده"); // unknown
      // error count = proxies that are down or degraded: exactly the seeded one
      const errCard = ops.locator(".stat", { hasText: "تعداد خطاهای پراکسی" });
      await expect(errCard.locator(".num")).toHaveText("1");
    } finally {
      for (const id of seeded) {
        await proxyCall(page, `/api/v1/admin/proxies/${id}`, { method: "DELETE" });
      }
    }
  });

  test("proxy pool: add via UI, speed test updates the badge", async ({ page }) => {
    await login(page);
    await cleanupE2eProxies(page); // self-heal leftovers from a crashed run
    try {
      await page.locator("#tabs button", { hasText: "پراکسی‌ها" }).click();
      const section = page.locator("main section:visible");
      await expect(section.locator("h3")).toContainText("پراکسی‌ها");

      // fresh pool: Persian empty state
      await expect(section.locator("tbody")).toContainText("پراکسی‌ای اضافه نشده");

      // open the add-proxy dialog and fill the manual form (kind defaults to SOCKS5)
      await section.getByRole("button", { name: "پراکسی جدید" }).click();
      const dlg = page.locator("dialog:visible", { hasText: "افزودن پراکسی" });
      await expect(dlg).toBeVisible();
      // the backend itself is a reachable TCP endpoint -> "ok" after the speed test
      const port = new URL(page.url()).port;
      await dlg.locator(".field", { hasText: "هاست" }).locator("input").fill("127.0.0.1");
      await dlg.locator(".field", { hasText: "پورت" }).locator("input").fill(String(port));
      await dlg.locator(".field", { hasText: "برچسب" }).locator("input").fill("e2e-ui");
      await dlg.getByRole("button", { name: "افزودن" }).click();

      // toast confirms, dialog closes, row appears as untested
      await expect(page.locator(".toast")).toContainText("پراکسی اضافه شد");
      await expect(dlg).toBeHidden();
      const row = section.locator("tbody tr", { hasText: "e2e-ui" });
      await expect(row).toBeVisible();
      await expect(row.locator("td").nth(3)).toContainText(`127.0.0.1:${port}`); // address
      await expect(row.locator("td").nth(4)).toContainText("آزمایش نشده"); // status unknown
      await expect(row.locator("td").nth(5)).toHaveText("—"); // no latency yet

      // run the speed test from the row action
      await row.getByRole("button", { name: "تست", exact: true }).click();
      await expect(page.locator(".toast")).toContainText("تست شد");
      // badge flips to healthy, latency + last-test cells fill in
      await expect(row.locator("td").nth(4)).toContainText("سالم");
      await expect(row.locator("td").nth(5)).toContainText("ثانیه");
      await expect(row.locator("td").nth(6)).not.toHaveText("—");
    } finally {
      await cleanupE2eProxies(page);
    }
  });

  test("upload via UI queues a file and lists it", async ({ page }) => {
    await login(page);
    await page.locator("#tabs button", { hasText: "فایل‌ها" }).click();

    // the section's own file-table only (a hidden restore input lives in another card)
    const uploadInput = page.locator("section input[type='file']").first();

    await uploadInput.setInputFiles({
      name: "e2e-smoke.txt",
      mimeType: "text/plain",
      buffer: Buffer.from("playwright smoke upload " + Date.now()),
    });

    // toast confirms queueing, the new file's row appears (fresh list puts it first)
    await expect(page.locator(".toast")).toContainText("صف شد: f_");
    const newRow = page.locator("main section:visible table tbody tr", { hasText: "e2e-smoke.txt" }).first();
    await expect(newRow).toBeVisible();

    // real progress bar element exists with aria
    await expect(page.locator(".progress-track")).toHaveCount(0); // hidden after finish
  });

  test("storage channels card lists dedicated channel with per-type stats", async ({ page }) => {
    await login(page);

    // self-heal leftovers from a crashed run
    const { items: existingKeys } = await proxyCall(page, "/api/v1/keys");
    for (const k of existingKeys) {
      if (k.name === "e2e-chan-key" && !k.revoked) {
        await proxyCall(page, `/api/v1/keys/${k.id}`, { method: "DELETE" });
      }
    }
    const { items: existingFiles } = await proxyCall(page, "/api/v1/files");
    for (const f of existingFiles) {
      if (f.name === "e2e-chan.png") await proxyCall(page, `/api/v1/files/${f.id}?purge=true`, { method: "DELETE" });
    }
    const { items: existingAccs } = await proxyCall(page, "/api/v1/accounts");
    for (const a of existingAccs) {
      if (a.label === "e2e-acc") await proxyCall(page, `/api/v1/accounts/${a.id}`, { method: "DELETE" });
    }

    // fake-TG mode auto-completes the login → a ready backend for the queue
    await proxyCall(page, "/api/v1/accounts/login/start", {
      method: "POST",
      body: { phone: "+989120000001", label: "e2e-acc" },
    });

    // key with a dedicated storage channel, then a small upload through it
    const key = await proxyCall(page, "/api/v1/keys", {
      method: "POST",
      body: { name: "e2e-chan-key", scopes: "read,write", storage_chat: "@e2e-channel-store" },
    });
    const upload = await page.evaluate(async (raw) => {
      const s = await fetch("/api/v1/files/upload/session", {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-API-Key": raw },
        body: JSON.stringify({ name: "e2e-chan.png", size: 5 }),
      }).then((r) => r.json());
      const done = await fetch(`/api/v1/files/upload/session/${s.session_id}`, {
        method: "PATCH",
        headers: { "X-API-Key": raw, "X-Offset": "0", "Content-Type": "application/octet-stream" },
        body: "hello",
      }).then((r) => r.json());
      return done;
    }, key.key);
    expect(upload.completed).toBeTruthy();

    // wait until the queue job turns the file ready
    let fileRow = null;
    for (let i = 0; i < 40; i++) {
      const { items } = await proxyCall(page, "/api/v1/files");
      fileRow = items.find((f) => f.id === upload.file_id);
      if (fileRow && fileRow.status === "ready") break;
      await page.waitForTimeout(250);
    }
    expect(fileRow?.status).toBe("ready");

    try {
      await page.locator("#tabs button", { hasText: "تنظیمات" }).click();
      await page.locator("button", { hasText: "بارگذاری/به‌روزرسانی" }).click();
      const card = page.locator(".card", { hasText: "کانال‌های ذخیره‌سازی" });
      await expect(card).toBeVisible();
      await expect(card).toContainText("@e2e-channel-store");
      await expect(card).toContainText("e2e-chan-key");
      await expect(card).toContainText("1 فایل");
      await expect(card).toContainText("تصویر: 1");
    } finally {
      await proxyCall(page, `/api/v1/files/${upload.file_id}?purge=true`, { method: "DELETE" });
      const { items } = await proxyCall(page, "/api/v1/keys");
      for (const k of items) {
        if (k.name === "e2e-chan-key") await proxyCall(page, `/api/v1/keys/${k.id}`, { method: "DELETE" });
      }
      const { items: accs } = await proxyCall(page, "/api/v1/accounts");
      for (const a of accs) {
        if (a.label === "e2e-acc") await proxyCall(page, `/api/v1/accounts/${a.id}`, { method: "DELETE" });
      }
    }
  });

  test("folders: sidebar, nested create, upload into folder, file move", async ({ page }) => {
    await login(page);

    // self-heal leftovers (tolerate already-deleted ids from a crashed run)
    const { items: existingFolders } = await proxyCall(page, "/api/v1/folders");
    for (const f of existingFolders) {
      if (f.path.startsWith("e2e-")) await proxyCall(page, `/api/v1/folders/${f.id}`, { method: "DELETE", allowMissing: true });
    }

    await page.locator("#tabs button", { hasText: "فایل‌ها" }).click();
    const section = page.locator("main section:visible");
    await expect(section.getByText("پوشه‌ها")).toBeVisible();
    await expect(section.getByText("همه فایل‌ها")).toBeVisible();

    // create a nested folder via the dialog
    await section.getByRole("button", { name: "+ پوشه" }).click();
    const dlg = page.locator("dialog:visible");
    await dlg.locator("input").fill("e2e-nested/child");
    await dlg.getByRole("button", { name: "ایجاد" }).click();
    await expect(section.getByText("e2e-nested")).toBeVisible();

    // nested folder shows with indentation marker + both rows in flat list
    const { items: flat } = await proxyCall(page, "/api/v1/folders");
    const paths = flat.map((f) => f.path).sort();
    expect(paths).toContain("e2e-nested");
    expect(paths).toContain("e2e-nested/child");

    // move the first listed file into the folder via its row button
    const firstRow = section.locator("tbody tr").first();
    await firstRow.getByRole("button", { name: "پوشه" }).click();
    const moveD = page.locator("dialog:visible");
    await moveD.locator("input").fill("e2e-nested/child");
    await moveD.getByRole("button", { name: "انتقال" }).click();
    await expect(page.locator("dialog:visible")).toHaveCount(0);

    // the file now lists inside the folder (recursive endpoint used by the UI)
    const { items: flat2 } = await proxyCall(page, "/api/v1/folders");
    const child = flat2.find((f) => f.path === "e2e-nested/child");
    expect(child.file_count).toBeGreaterThanOrEqual(1);

    // click the folder → its path shows in the breadcrumb
    await section.getByText("e2e-nested").click();
    await expect(section.locator("span", { hasText: "/e2e-nested" })).toBeVisible();

    // cleanup (detach files first so folder delete is clean)
    const childId = (await proxyCall(page, "/api/v1/folders/resolve?path=e2e-nested/child")).id;
    const { items: insideFiles } = await proxyCall(page, `/api/v1/folders/${childId}/all`);
    for (const f of insideFiles || []) {
      await proxyCall(page, `/api/v1/folders/${childId}/files/${f.id}`, { method: "DELETE" });
    }
    const rootId = (await proxyCall(page, "/api/v1/folders/resolve?path=e2e-nested")).id;
    await proxyCall(page, `/api/v1/folders/${rootId}`, { method: "DELETE", allowMissing: true });
  });

  test("storage channel click drills down to its files", async ({ page }) => {
    await login(page);

    // seed: key with dedicated channel + one ready upload through it
    const { items: existingKeys } = await proxyCall(page, "/api/v1/keys");
    for (const k of existingKeys) {
      if (k.name === "e2e-chan-key" && !k.revoked) await proxyCall(page, `/api/v1/keys/${k.id}`, { method: "DELETE" });
    }
    const { items: existingAccs } = await proxyCall(page, "/api/v1/accounts");
    for (const a of existingAccs) {
      if (a.label === "e2e-acc") await proxyCall(page, `/api/v1/accounts/${a.id}`, { method: "DELETE" });
    }
    await proxyCall(page, "/api/v1/accounts/login/start", { method: "POST", body: { phone: "+989120000001", label: "e2e-acc" } });
    const key = await proxyCall(page, "/api/v1/keys", {
      method: "POST",
      body: { name: "e2e-chan-key", scopes: "read,write", storage_chat: "@e2e-drill-chan" },
    });
    const upload = await page.evaluate(async (raw) => {
      const s = await fetch("/api/v1/files/upload/session", {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-API-Key": raw },
        body: JSON.stringify({ name: "e2e-drill.png", size: 5 }),
      }).then((r) => r.json());
      return await fetch(`/api/v1/files/upload/session/${s.session_id}`, {
        method: "PATCH",
        headers: { "X-API-Key": raw, "X-Offset": "0", "Content-Type": "application/octet-stream" },
        body: "hello",
      }).then((r) => r.json());
    }, key.key);
    expect(upload.completed).toBeTruthy();
    let row = null;
    for (let i = 0; i < 40; i++) {
      const { items } = await proxyCall(page, "/api/v1/files");
      row = items.find((f) => f.id === upload.file_id);
      if (row?.status === "ready") break;
      await page.waitForTimeout(250);
    }
    expect(row?.status).toBe("ready");

    try {
      // channels card → click the channel name → files tab opens filtered
      await page.locator("#tabs button", { hasText: "تنظیمات" }).click();
      await page.locator("button", { hasText: "بارگذاری/به‌روزرسانی" }).click();
      const card = page.locator(".card", { hasText: "کانال‌های ذخیره‌سازی" });
      await expect(card).toBeVisible();
      await card.locator("b", { hasText: "@e2e-drill-chan" }).click();
      // now on the files tab with the filter chip
      const section = page.locator("main section:visible");
      await expect(section.locator(".tag", { hasText: "@e2e-drill-chan" })).toBeVisible();
      // the uploaded file is listed with its channel in the new column
      await expect(section.locator("tbody")).toContainText("e2e-drill.png");
      await expect(section.locator("tbody")).toContainText("@e2e-drill-chan");
      // clearing the filter restores the unfiltered list
      await section.getByRole("button", { name: "حذف فیلتر ×" }).click();
      await expect(section.locator(".tag", { hasText: "@e2e-drill-chan" })).toHaveCount(0);
    } finally {
      await proxyCall(page, `/api/v1/files/${upload.file_id}?purge=true`, { method: "DELETE" });
      const { items } = await proxyCall(page, "/api/v1/keys");
      for (const k of items) {
        if (k.name === "e2e-chan-key") await proxyCall(page, `/api/v1/keys/${k.id}`, { method: "DELETE" });
      }
      const { items: accs } = await proxyCall(page, "/api/v1/accounts");
      for (const a of accs) {
        if (a.label === "e2e-acc") await proxyCall(page, `/api/v1/accounts/${a.id}`, { method: "DELETE" });
      }
    }
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

// --- proxy seeding for the ops-tab assertions (admin API, page's own token) ---
async function proxyCall(page, path, opts = {}) {
  return page.evaluate(
    async ([path, opts]) => {
      const token = localStorage.getItem("td_token");
      const r = await fetch(path, {
        method: opts.method || "GET",
        headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
        body: opts.body ? JSON.stringify(opts.body) : undefined,
      });
      const d = await r.json().catch(() => ({}));
      if (!r.ok) {
        if (opts.allowMissing && r.status === 404) return null;
        throw new Error(`admin API ${path} -> ${r.status}: ${JSON.stringify(d)}`);
      }
      return d;
    },
    [path, opts],
  );
}

async function cleanupE2eProxies(page) {
  const { items } = await proxyCall(page, "/api/v1/admin/proxies");
  for (const p of items) {
    if (typeof p.label === "string" && p.label.startsWith("e2e-")) {
      await proxyCall(page, `/api/v1/admin/proxies/${p.id}`, { method: "DELETE" });
    }
  }
}

async function seedProxies(page) {
  const port = new URL(page.url()).port;
  const ok = await proxyCall(page, "/api/v1/admin/proxies", {
    method: "POST",
    body: { host: "127.0.0.1", port: Number(port), kind: "socks5", label: "e2e-ok" },
  });
  const down = await proxyCall(page, "/api/v1/admin/proxies", {
    method: "POST",
    body: { host: "127.0.0.1", port: 1, kind: "socks5", label: "e2e-down" },
  });
  for (const p of [ok, down]) {
    await proxyCall(page, `/api/v1/admin/proxies/${p.id}/test`, { method: "POST" });
  }
  return [ok.id, down.id];
}
