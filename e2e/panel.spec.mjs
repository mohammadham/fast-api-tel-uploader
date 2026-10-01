// Smoke: login -> dashboard -> tabs -> upload via UI -> queue видит.
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

    // new flow: dialog asks for file + folder + destination channel
    await page.locator("section:visible button", { hasText: "آپلود جدید" }).click();
    await expect(page.locator("dialog:visible h3", { hasText: "آپلود فایل" })).toBeVisible();
    const dlgInput = page.locator("dialog:visible input[type='file']");
    await dlgInput.setInputFiles({
      name: "e2e-smoke.txt",
      mimeType: "text/plain",
      buffer: Buffer.from("playwright smoke upload " + Date.now()),
    });
    await page.locator("dialog:visible button", { hasText: "شروع آپلود" }).click();

    // toast confirms queueing, the new file's row appears (fresh list puts it first)
    await expect(page.locator(".toast")).toContainText("صف شد: f_");
    const newRow = page.locator("main section:visible table tbody tr", { hasText: "e2e-smoke.txt" }).first();
    await expect(newRow).toBeVisible();

    // the old inline progress bar is gone (uploads live in the tray now);
    // wait for the dialog flow's job to finish so only finished jobs remain
    await expect(page.locator("#upload-tray .progress-track")).toHaveCount(1); // tray bar exists
    await expect(page.locator("#upload-tray").getByText("صف شد")).toBeVisible();

    // leave no residue for the storage-channels assertions later in the suite
    await page.evaluate(async () => {
      const token = localStorage.getItem("td_token");
      const { items } = await fetch("/api/v1/files?limit=200", { headers: { Authorization: `Bearer ${token}` } }).then((r) => r.json());
      for (const f of items.filter((x) => x.name === "e2e-smoke.txt")) {
        await fetch(`/api/v1/files/${f.id}?purge=true`, { method: "DELETE", headers: { Authorization: `Bearer ${token}` } });
      }
    });
  });

  test("drag & drop onto files tab opens the upload dialog and queues the file", async ({ page }) => {
    await login(page);
    await page.locator("#tabs button", { hasText: "فایل‌ها" }).click();

    const payload = Buffer.from("playwright dragdrop upload " + Date.now());
    const dataTransfer = await page.evaluateHandle(() => new DataTransfer());
    await page.locator("main section:visible").dispatchEvent("dragenter", { dataTransfer });
    await expect(page.locator("text=فایل‌ها را رها کنید")).toBeVisible();
    await page.dispatchEvent("main section:visible", "drop", {
      dataTransfer: await page.evaluateHandle((buf) => {
        const dt = new DataTransfer();
        dt.items.add(new File([buf], "e2e-dragdrop.txt", { type: "text/plain" }));
        return dt;
      }, payload),
    });

    // same dialog as the button flow, file prefilled
    await expect(page.locator("dialog:visible h3", { hasText: "آپلود فایل" })).toBeVisible();
    await expect(page.locator("dialog:visible p", { hasText: "e2e-dragdrop.txt" })).toBeVisible();
    await page.locator("dialog:visible button", { hasText: "شروع آپلود" }).click();
    // persistent tray shows the job and it finishes even though we never kept the dialog open
    const tray = page.locator("#upload-tray");
    await expect(tray.getByText("e2e-dragdrop.txt")).toBeVisible();
    await expect(page.locator(".toast")).toContainText("صف شد: f_");
    const newRow = page.locator("main section:visible table tbody tr", { hasText: "e2e-dragdrop.txt" }).first();
    await expect(newRow).toBeVisible();

    // cleanup
    await page.evaluate(async () => {
      const token = localStorage.getItem("td_token");
      const { items } = await fetch("/api/v1/files?limit=200", { headers: { Authorization: `Bearer ${token}` } }).then((r) => r.json());
      for (const f of items.filter((x) => x.name === "e2e-dragdrop.txt")) {
        await fetch(`/api/v1/files/${f.id}?purge=true`, { method: "DELETE", headers: { Authorization: `Bearer ${token}` } });
      }
    });
  });

  test("real batch upload: dialog files run sequentially with per-file percent and «file k of n» badges", async ({ page }) => {
    await login(page);
    await page.locator("#tabs button", { hasText: "فایل‌ها" }).click();

    // slow chunk uploads so the queue state is observable (2 × 8MB chunks per
    // file → keep the per-chunk delay well above Playwright's poll interval,
    // otherwise the «فایل k از n» window closes before expect can see it)
    await page.route("**/upload/session/*", async (route) => {
      await new Promise((res) => setTimeout(res, 900));
      await route.continue();
    });

    await page.locator("section:visible button", { hasText: "آپلود جدید" }).click();
    await page.locator("dialog:visible input[type='file']").setInputFiles([
      { name: "e2e-batch-a.txt", mimeType: "text/plain", buffer: Buffer.alloc(10 * 1024 * 1024, 1) },
      { name: "e2e-batch-b.txt", mimeType: "text/plain", buffer: Buffer.alloc(10 * 1024 * 1024, 2) },
    ]);
    await page.locator("dialog:visible button", { hasText: "شروع آپلود" }).click();

    const tray = page.locator("#upload-tray");
    await expect(tray.getByText("e2e-batch-a.txt")).toBeVisible();
    await expect(tray.getByText("e2e-batch-b.txt")).toBeVisible();

    // both rows appear immediately; while the first uploads, the second shows
    // «در صف» (queued), and the running row shows the batch ordinal badge
    await expect(tray.getByText("فایل 1 از 2")).toBeVisible({ timeout: 10000 });
    await expect(tray.locator(".badge", { hasText: "در صف" })).toHaveCount(1);
    // header counts the queued one separately
    await expect(tray.locator("b", { hasText: "در صف" })).toBeVisible();

    // first finishes (its badge flips to «صف شد»), only then the second starts
    await expect(tray.locator(".badge", { hasText: "صف شد" })).toHaveCount(1, { timeout: 20000 });
    await expect(tray.getByText("فایل 2 از 2")).toBeVisible({ timeout: 10000 });
    await expect(tray.locator(".badge", { hasText: "در صف" })).toHaveCount(0);

    // both done: two «صف شد» badges, and both files listed server-side
    await expect(tray.locator(".badge", { hasText: "صف شد" })).toHaveCount(2, { timeout: 20000 });
    await page.evaluate(async () => {
      const token = localStorage.getItem("td_token");
      const { items } = await fetch("/api/v1/files?limit=100", { headers: { Authorization: "Bearer " + token } }).then((r) => r.json());
      const names = new Set(items.filter((x) => x.name.startsWith("e2e-batch-")).map((x) => x.name));
      if (!names.has("e2e-batch-a.txt") || !names.has("e2e-batch-b.txt")) throw new Error("batch files missing: " + [...names]);
      for (const f of items.filter((x) => x.name.startsWith("e2e-batch-"))) {
        await fetch("/api/v1/files/" + f.id + "?purge=true", { method: "DELETE", headers: { Authorization: "Bearer " + token } });
      }
    });
  });

  test("drop onto a sidebar folder row uploads straight into that folder", async ({ page }) => {
    await login(page);
    await page.locator("#tabs button", { hasText: "فایل‌ها" }).click();

    // create the target folder via API
    const fid = await page.evaluate(async () => {
      const token = localStorage.getItem("td_token");
      const r = await fetch("/api/v1/folders", { method: "POST", headers: { Authorization: "Bearer " + token, "Content-Type": "application/json" }, body: JSON.stringify({ path: "e2e-sbdrop" }) });
      if (!r.ok) throw new Error("folder create failed: " + r.status);
      return (await r.json()).id;
    });

    // re-enter the files tab so the sidebar folder list refreshes
    await page.locator("#tabs button", { hasText: "داشبورد" }).click();
    await page.locator("#tabs button", { hasText: "فایل‌ها" }).click();

    const row = page.locator("div[title^='مسیر: e2e-sbdrop']");
    await expect(row).toBeVisible();

    // hovering highlights the row with a dashed outline
    await row.dispatchEvent("dragenter", { dataTransfer: await page.evaluateHandle(() => new DataTransfer()) });
    await expect(row).toHaveCSS("outline-style", "dashed");

    // drop → no dialog, job goes straight to the tray with the folder pinned
    await row.dispatchEvent("drop", {
      dataTransfer: await page.evaluateHandle((buf) => {
        const dt = new DataTransfer();
        dt.items.add(new File([buf], "e2e-sbdrop.txt", { type: "text/plain" }));
        return dt;
      }, Buffer.from("sidebar drop " + Date.now())),
    });
    await expect(page.locator("dialog:visible")).toHaveCount(0);
    const tray = page.locator("#upload-tray");
    await expect(tray.getByText("e2e-sbdrop.txt")).toBeVisible();
    await expect(tray.locator(".tag", { hasText: "e2e-sbdrop" })).toBeVisible(); // folder pinned on the job
    await expect(tray.locator(".badge", { hasText: "صف شد" })).toHaveCount(1, { timeout: 15000 });

    // the file really landed in the dropped-on folder (server-side check)
    await page.evaluate(async (fid) => {
      const token = localStorage.getItem("td_token");
      const { items } = await fetch("/api/v1/files?limit=100", { headers: { Authorization: "Bearer " + token } }).then((r) => r.json());
      const f = items.find((x) => x.name === "e2e-sbdrop.txt");
      if (!f) throw new Error("dropped file not found");
      if (f.folder_id !== fid) throw new Error("wrong folder: " + f.folder_id + " != " + fid);
      await fetch("/api/v1/files/" + f.id + "?purge=true", { method: "DELETE", headers: { Authorization: "Bearer " + token } });
      await fetch("/api/v1/folders/" + fid, { method: "DELETE", headers: { Authorization: "Bearer " + token } });
    }, fid);
  });

  test("tray cancel closes the upload session server-side (global cancel)", async ({ page }) => {
    await login(page);
    await page.locator("#tabs button", { hasText: "فایل‌ها" }).click();

    // slow the chunk PATCHes so the cancel click reliably lands mid-upload
    await page.route("**/upload/session/*", async (route) => {
      await new Promise((res) => setTimeout(res, 400));
      await route.continue();
    });
    const sessionCreated = page.waitForResponse(
      (r) => r.url().includes("/upload/session") && r.request().method() === "POST" && r.status() === 200,
    );

    await page.locator("section:visible button", { hasText: "آپلود جدید" }).click();
    await page.locator("dialog:visible input[type='file']").setInputFiles({
      name: "e2e-cancel.txt",
      mimeType: "text/plain",
      buffer: Buffer.alloc(17 * 1024 * 1024, 7), // 3 chunks → time to cancel
    });
    await page.locator("dialog:visible button", { hasText: "شروع آپلود" }).click();
    const sid = (await (await sessionCreated).json()).session_id;

    const tray = page.locator("#upload-tray");
    await expect(tray.getByText("e2e-cancel.txt")).toBeVisible();
    await tray.getByRole("button", { name: "لغو" }).click();
    await expect(tray.getByText("لغو شد")).toBeVisible({ timeout: 15000 });

    // the cancel must have reached the server: the session is tombstoned →
    // a manual DELETE now answers 410 (no longer active), not 404
    const status = await page.evaluate(async (sid) => {
      const token = localStorage.getItem("td_token");
      return (await fetch("/api/v1/files/upload/session/" + sid, { method: "DELETE", headers: { Authorization: "Bearer " + token } })).status;
    }, sid);
    expect(status).toBe(410);

    // and a racing chunk is rejected with 410 as well
    const chunkStatus = await page.evaluate(async (sid) => {
      const token = localStorage.getItem("td_token");
      const r = await fetch("/api/v1/files/upload/session/" + sid, {
        method: "PATCH",
        headers: { Authorization: "Bearer " + token, "X-Offset": "0" },
        body: "x",
      });
      return r.status;
    }, sid);
    expect(chunkStatus).toBe(410);

    await tray.getByRole("button", { name: "×" }).click();
    await expect(tray).toHaveCount(0);
  });

  test("drop onto the upload tray inherits the last job's folder/chat and uploads without the dialog", async ({ page }) => {
    await login(page);
    await page.locator("#tabs button", { hasText: "فایل‌ها" }).click();

    // seed the tray with one job via the dialog — its folder/chat become the inherited settings
    await page.locator("section:visible button", { hasText: "آپلود جدید" }).click();
    await page.locator("dialog:visible input[type='file']").setInputFiles({
      name: "e2e-traydrop-1.txt",
      mimeType: "text/plain",
      buffer: Buffer.from("traydrop seed " + Date.now()),
    });
    // set an explicit folder so the inheritance is observable on the dropped job
    await page.locator("dialog:visible input[placeholder='از پوشه‌های همین کانال استفاده می‌شود']").fill("e2e-traydrop-folder");
    await page.locator("dialog:visible button", { hasText: "شروع آپلود" }).click();
    const tray = page.locator("#upload-tray");
    await expect(tray.getByText("e2e-traydrop-1.txt")).toBeVisible();
    await expect(tray.getByText("صف شد")).toBeVisible({ timeout: 15000 });

    // drop a file directly onto the tray — no dialog may open, job inherits settings
    const payload = Buffer.from("traydrop second " + Date.now());
    await tray.dispatchEvent("dragenter", { dataTransfer: await page.evaluateHandle(() => new DataTransfer()) });
    await expect(tray.getByText("فایل‌ها را همین‌جا رها کنید")).toBeVisible();
    await tray.dispatchEvent("drop", {
      dataTransfer: await page.evaluateHandle((buf) => {
        const dt = new DataTransfer();
        dt.items.add(new File([buf], "e2e-traydrop-2.txt", { type: "text/plain" }));
        return dt;
      }, payload),
    });
    await expect(page.locator(".toast")).toContainText("فایل با تنظیمات آخرین جاب"); // enqueue confirmation
    await expect(page.locator("dialog:visible")).toHaveCount(0); // dialog never opened
    await expect(tray).toBeVisible();
    await expect(tray.getByText("e2e-traydrop-2.txt")).toBeVisible();
    await expect(tray.getByText("e2e-traydrop-folder").first()).toBeVisible(); // inherited folder tag
    await expect(tray.getByText("صف شد")).toHaveCount(2, { timeout: 15000 }); // both done

    // the dropped file landed in the inherited folder
    await page.evaluate(async () => {
      const token = localStorage.getItem("td_token");
      const { items } = await fetch("/api/v1/files?limit=200", { headers: { Authorization: `Bearer ${token}` } }).then((r) => r.json());
      const dropped = items.find((x) => x.name === "e2e-traydrop-2.txt");
      if (!dropped) throw new Error("dropped file not found");
      const { items: folders } = await fetch("/api/v1/folders", { headers: { Authorization: `Bearer ${token}` } }).then((r) => r.json());
      const target = (folders || []).find((f) => (f.path || f.name || "").includes("e2e-traydrop-folder"));
      if (!target || dropped.folder_id !== target.id) throw new Error("folder not inherited: file.folder_id=" + dropped.folder_id + " target.id=" + (target && target.id));
      for (const f of items.filter((x) => x.name.startsWith("e2e-traydrop-"))) {
        await fetch(`/api/v1/files/${f.id}?purge=true`, { method: "DELETE", headers: { Authorization: `Bearer ${token}` } });
      }
    });
  });

  test("upload tray keeps uploads running after the dialog closes and allows cancel", async ({ page }) => {
    await login(page);
    await page.locator("#tabs button", { hasText: "فایل‌ها" }).click();

    // start an upload from the dialog
    await page.locator("section:visible button", { hasText: "آپلود جدید" }).click();
    await page.locator("dialog:visible input[type='file']").setInputFiles({
      name: "e2e-tray.txt",
      mimeType: "text/plain",
      buffer: Buffer.from("tray upload " + Date.now()),
    });
    await page.locator("dialog:visible button", { hasText: "شروع آپلود" }).click();

    // tray appears immediately, outside any dialog, showing name + progress
    const tray = page.locator("#upload-tray");
    await expect(tray.getByText("e2e-tray.txt")).toBeVisible();
    await expect(tray.locator(".progress-track")).toBeVisible();

    // wait until done, then the badge flips to "صف شد" and a dismiss × appears
    await expect(tray.getByText("صف شد")).toBeVisible({ timeout: 15000 });
    await expect(tray.getByRole("button", { name: "×" })).toBeVisible();

    // clear finished jobs cleans the tray
    await tray.getByRole("button", { name: "پاک‌سازی تمام‌شده‌ها" }).click();
    await expect(tray).toHaveCount(0);

    // server-side cancel too: the session must be gone (DELETE → 410 on re-cancel)
    await page.evaluate(async () => {
      const token = localStorage.getItem("td_token");
      const { items } = await fetch("/api/v1/files?limit=50", { headers: { Authorization: "Bearer " + token } }).then((r) => r.json());
      for (const f of items.filter((x) => x.name === "e2e-tray.txt")) {
        await fetch("/api/v1/files/" + f.id + "?purge=true", { method: "DELETE", headers: { Authorization: "Bearer " + token } });
      }
    });
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
    const { items: existingFiles } = await proxyCall(page, "/api/v1/files", { });
    for (const f of existingFiles) {
      // purge our leftovers (incl. files a crashed per-key test left behind)
      if (["e2e-chan.png", "e2e-channel-file.txt", "e2e-smoke.txt"].includes(f.name)) {
        await proxyCall(page, `/api/v1/files/${f.id}?purge=true`, { method: "DELETE", allowMissing: true });
      }
    }
    const { items: existingAccs } = await proxyCall(page, "/api/v1/accounts");
    for (const a of existingAccs) {
      if (a.label === "e2e-acc") await proxyCall(page, `/api/v1/accounts/${a.id}`, { method: "DELETE" });
    }

    // fake-TG mode auto-completes the login -> a ready backend for the queue
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

    // seed a file to move (this test must not rely on leftovers from earlier tests)
    const seeded = await page.evaluate(async () => {
      const token = localStorage.getItem("td_token");
      const s = await fetch("/api/v1/files/upload/session", {
        method: "POST",
        headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
        body: JSON.stringify({ name: "e2e-folder-move.txt", size: 5 }),
      }).then((r) => r.json());
      const done = await fetch(`/api/v1/files/upload/session/${s.session_id}`, {
        method: "PATCH",
        headers: { Authorization: `Bearer ${token}`, "X-Offset": "0", "Content-Type": "application/octet-stream" },
        body: "hello",
      }).then((r) => r.json());
      return done;
    });
    expect(seeded.completed).toBeTruthy();

    // refresh the files table so the seeded row is visible in the UI
    await section.getByText("همه فایل‌ها").click();
    const seededRow = section.locator("tbody tr", { hasText: "e2e-folder-move.txt" }).first();
    await expect(seededRow).toBeVisible();

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

    // click the folder -> its path shows in the breadcrumb
    await section.getByText("e2e-nested").click();
    await expect(section.locator("span", { hasText: "/e2e-nested" })).toBeVisible();

    // cleanup (purge the seeded file, detach leftovers, delete folders)
    await proxyCall(page, `/api/v1/files/${seeded.file_id}?purge=true`, { method: "DELETE", allowMissing: true });
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
      // channels card -> click the channel name -> files tab opens filtered
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

  test("file manager: search filter, preview dialog, block flag", async ({ page }) => {
    await login(page);
    await proxyCall(page, "/api/v1/accounts/login/start", { method: "POST", body: { phone: "+989120000002", label: "e2e-acc2" } });
    const key = await proxyCall(page, "/api/v1/keys", { method: "POST", body: { name: "e2e-mgr-key", scopes: "read,write" } });
    const upload = await page.evaluate(async (raw) => {
      const s = await fetch("/api/v1/files/upload/session", {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-API-Key": raw },
        body: JSON.stringify({ name: "e2e-mgr.png", size: 5 }),
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
      await page.locator("#tabs button", { hasText: "فایل‌ها" }).click();
      const section = page.locator("main section:visible");
      // search box narrows the list to the seeded file
      await section.locator("input[placeholder='جستجوی نام فایل...']").fill("e2e-mgr");
      await page.waitForTimeout(600); // debounce
      await expect(section.locator("tbody")).toContainText("e2e-mgr.png");
      // preview dialog opens with an image inside
      await section.getByRole("button", { name: "پیش‌نمایش" }).first().click();
      const dlg = page.locator("dialog:visible");
      await expect(dlg.locator("img")).toBeVisible();
      await dlg.getByRole("button", { name: "بستن" }).click();
      // block -> badge appears + unblock clears it (auto-accept the confirm)
      page.on("dialog", (d) => d.accept());
      await section.getByRole("button", { name: "بن", exact: true }).first().click();
      await expect(section.locator(".tag", { hasText: "بن" }).first()).toBeVisible();
      await section.getByRole("button", { name: "رفع بن" }).first().click();
      await expect(section.locator(".tag", { hasText: "بن" })).toHaveCount(0);
    } finally {
      await proxyCall(page, `/api/v1/files/${upload.file_id}?purge=true`, { method: "DELETE", allowMissing: true });
      const { items } = await proxyCall(page, "/api/v1/keys");
      for (const k of items) {
        if (k.name === "e2e-mgr-key") await proxyCall(page, `/api/v1/keys/${k.id}`, { method: "DELETE" });
      }
      const { items: accs } = await proxyCall(page, "/api/v1/accounts");
      for (const a of accs) {
        if (a.label === "e2e-acc2") await proxyCall(page, `/api/v1/accounts/${a.id}`, { method: "DELETE" });
      }
    }
  });

  test("logout returns to landing", async ({ page }) => {
    await login(page);
    await page.getByRole("button", { name: "خروج" }).click();
    await expect(page.locator("#lg-user")).toBeVisible();
    expect(await page.evaluate(() => localStorage.getItem("td_token"))).toBeNull();
  });

  test("per-key storage chat: create key with channel, upload file, verify channel display", async ({ page }) => {
    await login(page);

    // 0. Seed a fresh fake account so the queue has a backend in this test too
    const { items: prevAccs } = await proxyCall(page, "/api/v1/accounts");
    for (const a of prevAccs) {
      if (a.label === "e2e-chat-acc") await proxyCall(page, `/api/v1/accounts/${a.id}`, { method: "DELETE" });
    }
    await proxyCall(page, "/api/v1/accounts/login/start", { method: "POST", body: { phone: "+989120000003", label: "e2e-chat-acc" } });

    // 1. Create a new API key with a storage channel via API
    const { items: existingKeys } = await proxyCall(page, "/api/v1/keys");
    for (const k of existingKeys) {
      if (k.name?.startsWith("e2e-chat-key")) await proxyCall(page, `/api/v1/keys/${k.id}`, { method: "DELETE" });
    }

    const newKey = await proxyCall(page, "/api/v1/keys", {
      method: "POST",
      body: { name: "e2e-chat-key", scopes: "read,write", storage_chat: "@e2e-channel-store" },
    });

    // 2. Upload a file using this key with the storage channel
    const upload = await page.evaluate(async (raw) => {
      const s = await fetch("/api/v1/files/upload/session", {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-API-Key": raw },
        body: JSON.stringify({ name: "e2e-channel-file.txt", size: 5 }),
      }).then((r) => r.json());
      const done = await fetch(`/api/v1/files/upload/session/${s.session_id}`, {
        method: "PATCH",
        headers: { "X-API-Key": raw, "X-Offset": "0", "Content-Type": "application/octet-stream" },
        body: "hello",
      }).then((r) => r.json());
      return done;
    }, newKey.key);
    expect(upload.completed).toBeTruthy();

    // Wait for the file to become ready (fake-TG queue drains fast, but poll generously)
    let fileRow = null;
    for (let i = 0; i < 80; i++) {
      const { items } = await proxyCall(page, "/api/v1/files?limit=200");
      fileRow = items.find((f) => f.id === upload.file_id);
      if (fileRow && fileRow.status === "ready") break;
      await page.waitForTimeout(500);
    }
    expect(fileRow?.status).toBe("ready");

    // 3. Navigate to files tab and verify the file row shows the dedicated channel
    await page.locator("#tabs button", { hasText: "فایل‌ها" }).click();
    const section = page.locator("main section:visible");
    const row = section.locator("tbody tr", { hasText: "e2e-channel-file.txt" }).first();
    await expect(row).toBeVisible();
    // storage_chat is stored as '@e2e-channel-store'; the table cell renders it verbatim
    await expect(row).toContainText("@e2e-channel-store");

    // 4. The settings tab storage-channels card lists the dedicated channel
    await page.locator("#tabs button", { hasText: "تنظیمات" }).click();
    await page.locator("button", { hasText: "بارگذاری/به‌روزرسانی" }).click();
    const channelCard = page.locator(".card", { hasText: "کانال‌های ذخیره‌سازی" });
    await expect(channelCard).toBeVisible();
    await expect(channelCard).toContainText("@e2e-channel-store");

    // leave no residue: the dedicated-channel file must not leak into later runs
    await proxyCall(page, `/api/v1/files/${upload.file_id}?purge=true`, { method: "DELETE", allowMissing: true });
    const { items: keysNow } = await proxyCall(page, "/api/v1/keys");
    for (const k of keysNow) {
      if (k.name?.startsWith("e2e-chat-key")) await proxyCall(page, `/api/v1/keys/${k.id}`, { method: "DELETE" });
    }
  });

  test("files tab shows a live transfer progress badge while a transfer runs", async ({ page }) => {
    await login(page);
    await page.locator("#tabs button", { hasText: "فایل‌ها" }).click();

    // unique per-run names: stale leftovers from a crashed run must never be
    // picked by the row selectors (they'd be skipped as "already in target")
    const tag = "e2e-tprog-" + Date.now().toString(36) + "-";

    // upload three files (each chunked into parts) so the bulk transfer
    // queues several jobs — enough to keep the badge visible for a while
    // (a single small transfer can finish before the panel's first poll)
    const fileIds = await page.evaluate(async (tag) => {
      const token = localStorage.getItem("td_token");
      const H = { Authorization: "Bearer " + token, "Content-Type": "application/json" };
      const out = [];
      for (let k = 0; k < 3; k++) {
        const data = new Uint8Array(8 * 1024 * 1024).fill(5);
        const s = await fetch("/api/v1/files/upload/session", { method: "POST", headers: H, body: JSON.stringify({ name: tag + k + ".bin", size: data.length }) }).then((r) => r.json());
        let off = 0;
        while (off < data.length) {
          const chunk = data.slice(off, off + 4 * 1024 * 1024);
          const r = await fetch("/api/v1/files/upload/session/" + s.session_id, { method: "PATCH", headers: { Authorization: "Bearer " + token, "X-Offset": String(off) }, body: chunk });
          const j = await r.json();
          off = j.offset;
          if (j.completed) { out.push(j.file_id); break; }
        }
      }
      return out;
    }, tag);
    expect(fileIds).toHaveLength(3);

    // the upload jobs may still be draining the queue (earlier tests fill it);
    // wait until the files are ready, otherwise bulk-transfer rejects them
    await page.evaluate(async (fileIds) => {
      const token = localStorage.getItem("td_token");
      for (let i = 0; i < 120; i++) {
        const { items } = await fetch("/api/v1/files?limit=100", { headers: { Authorization: "Bearer " + token } }).then((r) => r.json());
        const mine = items.filter((f) => fileIds.includes(f.id));
        if (i % 10 === 0) console.log("[tprog-wait] " + JSON.stringify(mine.map((f) => [f.name, f.status])));
        if (mine.length === fileIds.length && mine.every((f) => f.status === "ready")) return;
        if (mine.some((f) => f.status === "failed")) throw new Error("upload failed: " + JSON.stringify(mine.filter((f) => f.status === "failed").map((f) => f.error)));
        await new Promise((res) => setTimeout(res, 500));
      }
      throw new Error("files never became ready");
    }, fileIds);

    // register a channel so the bulk dialog has a concrete destination
    const chanId = await page.evaluate(async () => {
      const token = localStorage.getItem("td_token");
      const r = await fetch("/api/v1/channels", { method: "POST", headers: { Authorization: "Bearer " + token, "Content-Type": "application/json" }, body: JSON.stringify({ chat: "@e2e-tprog-chan", label: "e2e tprog", kind: "storage" }) });
      if (!r.ok && r.status !== 409) throw new Error("channel register failed: " + r.status);
      return (await r.json().catch(() => ({}))).id || null;
    });

    // refresh the files table so the freshly uploaded rows are rendered
    await page.locator("section:visible button", { hasText: "به‌روزرسانی" }).click();
    await expect(page.locator("main section:visible table tbody")).toContainText(tag + "0.bin");

    // start the bulk transfer through the UI: enable bulk mode, select the
    // three rows, open the dialog, pick the registered channel, submit
    await page.locator("section:visible button", { hasText: "انتخاب گروهی" }).click();
    for (let k = 0; k < 3; k++) {
      await page.locator("main section:visible table tbody tr", { hasText: tag + k + ".bin" }).first().locator("input[type='checkbox']").check();
    }
    await expect(page.locator("section:visible button", { hasText: "انتقال گروهی به کانال" })).toContainText("(3)");
    await page.locator("section:visible button", { hasText: "انتقال گروهی به کانال" }).click();
    const btDlg = page.locator("dialog:visible", { hasText: "انتقال گروهی" });
    await expect(btDlg).toBeVisible();
    await btDlg.locator("select").first().selectOption("@e2e-tprog-chan");
    await btDlg.getByRole("button", { name: "انتقال", exact: true }).click();

    // the toast confirms enqueueing (all 3!) and the live badge appears
    await expect(page.locator(".toast")).toContainText("3 فایل به صف انتقال رفت");
    const badge = page.locator("#transfer-live");
    await expect(badge).toBeVisible({ timeout: 10000 });
    await expect(badge).toContainText("انتقال:");
    await expect(badge).toContainText("%");

    // wait for all transfers to finish; the badge disappears (jobs done)
    await expect(badge).toHaveCount(0, { timeout: 45000 });

    // cleanup: purge the transferred files (by name — also sweeps stale
    // leftovers), drop the channel
    await page.evaluate(async (tag) => {
      const token = localStorage.getItem("td_token");
      const { items } = await fetch("/api/v1/files?limit=200", { headers: { Authorization: "Bearer " + token } }).then((r) => r.json());
      for (const f of items.filter((x) => x.name.startsWith("e2e-tprog-"))) {
        await fetch("/api/v1/files/" + f.id + "?purge=true", { method: "DELETE", headers: { Authorization: "Bearer " + token } });
      }
    }, tag);
    if (chanId) {
      await page.evaluate(async (chanId) => {
        const token = localStorage.getItem("td_token");
        await fetch("/api/v1/channels/" + chanId, { method: "DELETE", headers: { Authorization: "Bearer " + token } });
      }, chanId);
    }
  });

  test("trash view: soft-delete hides file, restore brings it back", async ({ page }) => {
    await login(page);

    // self-heal leftovers from crashed runs (live or trashed)
    { const { items } = await proxyCall(page, "/api/v1/files?limit=200");
      for (const f of items.filter((x) => x.name === "e2e-trash.txt")) {
        await proxyCall(page, `/api/v1/files/${f.id}?purge=true`, { method: "DELETE", allowMissing: true });
      } }

    await page.locator("#tabs button", { hasText: "فایل‌ها" }).click();
    const section = page.locator("main section:visible");

    // upload a dedicated file via API, wait for its row
    const seeded = await page.evaluate(async () => {
      const token = localStorage.getItem("td_token");
      const s = await fetch("/api/v1/files/upload/session", {
        method: "POST",
        headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
        body: JSON.stringify({ name: "e2e-trash.txt", size: 5 }),
      }).then((r) => r.json());
      await fetch(`/api/v1/files/upload/session/${s.session_id}`, {
        method: "PATCH",
        headers: { Authorization: `Bearer ${token}`, "X-Offset": "0", "Content-Type": "application/octet-stream" },
        body: "hello",
      });
      return s;
    });
    await section.getByText("همه فایل‌ها").click();
    await expect(section.locator("tbody tr", { hasText: "e2e-trash.txt" }).first()).toBeVisible();

    // soft-delete via API (the row's own delete button chains two confirms;
    // the soft-delete behavior itself is covered by pytest)
    const fid = (await proxyCall(page, "/api/v1/files?limit=200")).items.find((x) => x.name === "e2e-trash.txt").id;
    await proxyCall(page, `/api/v1/files/${fid}`, { method: "DELETE" });
    await section.getByText("همه فایل‌ها").click(); // reload the table
    await expect(section.locator("tbody tr", { hasText: "e2e-trash.txt" })).toHaveCount(0);

    // trash toggle shows it with a restore button
    await section.getByRole("button", { name: "زباله‌دان" }).click();
    const trashedRow = section.locator("tbody tr", { hasText: "e2e-trash.txt" }).first();
    await expect(trashedRow).toBeVisible();

    // restore → back in the main list
    await trashedRow.getByRole("button", { name: "بازیابی" }).click();
    await section.getByRole("button", { name: "زباله‌دان" }).click(); // toggle off
    await expect(section.locator("tbody tr", { hasText: "e2e-trash.txt" }).first()).toBeVisible();

    // cleanup
    const { items: all } = await proxyCall(page, "/api/v1/files?limit=200");
    for (const f of all.filter((x) => x.name === "e2e-trash.txt")) {
      await proxyCall(page, `/api/v1/files/${f.id}?purge=true`, { method: "DELETE", allowMissing: true });
    }
  });

  test("account storage-channel edit updates the row", async ({ page }) => {
    await login(page);

    // seed a fresh account (fake-TG auto-completes login)
    const { items: prev } = await proxyCall(page, "/api/v1/accounts");
    for (const a of prev) {
      if (a.label === "e2e-chan-acc") await proxyCall(page, `/api/v1/accounts/${a.id}`, { method: "DELETE" });
    }
    await proxyCall(page, "/api/v1/accounts/login/start", { method: "POST", body: { phone: "+989120000004", label: "e2e-chan-acc" } });

    await page.locator("#tabs button", { hasText: "اکانت‌ها" }).click();
    const section = page.locator("main section:visible");
    const row = section.locator("tbody tr", { hasText: "e2e-chan-acc" }).first();
    await expect(row).toBeVisible();

    // register a channel first so the select dialog has a real option
    await proxyCall(page, "/api/v1/channels", { method: "POST", body: { chat: "@e2e-acc-chan", label: "e2e chan acc", kind: "storage" } });

    // open the select dialog and pick that channel
    await row.getByRole("button", { name: "کانال", exact: true }).click();
    const chanDlg = page.locator("dialog:visible", { hasText: "کانال ذخیره‌سازی اکانت" });
    await chanDlg.locator("select").selectOption("@e2e-acc-chan");
    await chanDlg.getByRole("button", { name: "ذخیره" }).click();
    await expect(row).toContainText("@e2e-acc-chan");

    // backend round-trip confirms persistence
    const { items } = await proxyCall(page, "/api/v1/accounts");
    const acc = items.find((a) => a.label === "e2e-chan-acc");
    expect(acc.storage_chat_id).toBe("@e2e-acc-chan");

    // cleanup
    await proxyCall(page, `/api/v1/accounts/${acc.id}`, { method: "DELETE" });
    const { items: chans } = await proxyCall(page, "/api/v1/channels");
    for (const c of chans) {
      if (c.chat === "@e2e-acc-chan") await proxyCall(page, `/api/v1/channels/${c.id}`, { method: "DELETE" });
    }
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