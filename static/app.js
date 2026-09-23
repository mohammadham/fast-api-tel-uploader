/* TelegramDrive panel - Vue 3 (no build step, vendored vue.global.prod.js) */
const { createApp, reactive, ref, computed, onMounted } = Vue;

const app = createApp({
  setup() {
    /* ---------- auth state ---------- */
    const token = ref(localStorage.getItem("td_token") || "");
    const refresh = ref(localStorage.getItem("td_refresh") || "");
    const login = reactive({ user: "", pass: "", err: "", busy: false });

    /* ---------- ui state ---------- */
    const tab = ref("dash");
    const tabList = [
      { id: "dash", label: "داشبورد" },
      { id: "accounts", label: "اکانت‌ها" },
      { id: "bots", label: "بات‌ها" },
      { id: "eitaa", label: "ایتا" },
      { id: "keys", label: "کلیدها" },
      { id: "files", label: "فایل‌ها" },
      { id: "queue", label: "صف" },
      { id: "audit", label: "لاگ‌ها" },
    ];
    const toast = reactive({ msg: "", err: false });
    let toastTimer = null;
    function showToast(msg, ms = 2600, isErr = false) {
      toast.msg = msg; toast.err = isErr;
      clearTimeout(toastTimer);
      toastTimer = setTimeout(() => (toast.msg = ""), ms);
    }

    /* ---------- data ---------- */
    const health = ref([]);
    const accounts = ref([]);
    const bots = ref([]);
    const eitaas = ref([]);
    const keys = ref([]);
    const files = ref([]);
    const jobs = ref([]);
    const audit = ref([]);
    const queueStats = ref({});
    const overview = ref(null);
    const uploadBusy = ref(false);
    const uploadProgress = ref("");
    const uploadPct = ref(0); // 0-100, real XHR progress
    const uploadSpeed = ref(0); // MB/s
    const uploadTimeStart = ref(0); // timestamp
    const nowSec = Math.floor(Date.now() / 1000);

    /* ---------- dialogs ---------- */
    const accDlg = reactive({ open: false, step: 1, phone: "", label: "", code: "", pass: "", msg: "", loginId: "" });
    const botDlg = reactive({ open: false, token: "", label: "", msg: "" });
    const eitDlg = reactive({ open: false, token: "", chat: "", label: "", msg: "" });
    const keyDlg = reactive({ open: false, name: "", scopes: "upload,download", rpm: 120, quota: 0, backend: "", result: "" });
    const qrDlg = reactive({ open: false, url: "", slug: "" });

    /* ---------- api helper (with refresh) ---------- */
    async function api(path, opts = {}) {
      opts.headers = { ...(opts.headers || {}), Authorization: "Bearer " + token.value };
      if (opts.json) { opts.headers["Content-Type"] = "application/json"; opts.body = JSON.stringify(opts.json); }
      const resp = await fetch(path, opts);
      if (resp.status === 401 && refresh.value) {
        const r = await fetch("/api/v1/auth/refresh", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ refresh_token: refresh.value }) });
        if (r.ok) {
          const d = await r.json();
          token.value = d.access_token;
          localStorage.setItem("td_token", token.value);
          return api(path, opts);
        }
        logout();
        throw new Error("نشست منقضی شد؛ دوباره وارد شوید");
      }
      if (!resp.ok) {
        const d = await resp.json().catch(() => ({}));
        throw new Error(d.detail || resp.statusText || "خطای ناشناخته");
      }
      return resp.json();
    }

    /* ---------- formatting ---------- */
    function fmtBytes(n) {
      n = Number(n) || 0;
      const u = ["B", "KB", "MB", "GB", "TB"]; let i = 0;
      while (n >= 1024 && i < u.length - 1) { n /= 1024; i++; }
      return n.toFixed(i ? 1 : 0) + " " + u[i];
    }
    function fmtTime(ts) {
      if (!ts) return "-";
      return new Date(ts * 1000).toLocaleString("fa-IR");
    }

    /* ---------- auth actions ---------- */
    async function submitLogin() {
      login.err = ""; login.busy = true;
      try {
        const r = await fetch("/api/v1/auth/login", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ username: login.user, password: login.pass }) });
        const d = await r.json();
        if (!r.ok) throw new Error(d.detail || "ورود ناموفق بود");
        token.value = d.access_token; refresh.value = d.refresh_token;
        localStorage.setItem("td_token", token.value);
        localStorage.setItem("td_refresh", refresh.value);
        login.pass = "";
        await switchTab("dash");
        showToast("خوش آمدید");
      } catch (e) { login.err = e.message || "نام کاربری یا رمز اشتباه است"; }
      login.busy = false;
    }
    async function logout() {
      try { await fetch("/api/v1/auth/logout", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ refresh_token: refresh.value }) }); } catch (e) {}
      localStorage.clear();
      token.value = ""; refresh.value = "";
    }

    /* ---------- tabs & loaders ---------- */
    const loaders = {};
    function switchTab(id) {
      tab.value = id;
      const l = loaders[id];
      return l ? l() : Promise.resolve();
    }

    const dashCards = computed(() => {
      const ov = overview.value;
      if (!ov) return [];
      return [
        ["فایل‌های آماده", `${ov.files.ready} / ${ov.files.total}`],
        ["حجم ذخیره‌شده", fmtBytes(ov.files.bytes_stored)],
        ["ترافیک سرو شده", fmtBytes(ov.traffic.bytes_served)],
        ["دانلودها", ov.traffic.downloads],
        ["اکانت‌های فعال", `${ov.accounts.ready} / ${ov.accounts.total}`],
        ["بات‌ها", ov.bots],
        ["صف (در انتظار)", ov.queue.pending],
        ["آپ‌تایم", Math.round(ov.uptime / 60) + " دقیقه"],
      ];
    });

    loaders.dash = async () => {
      try {
        const [ov, accs] = await Promise.all([api("/api/v1/admin/overview"), api("/api/v1/accounts")]);
        overview.value = ov;
        health.value = accs.items || [];
      } catch (e) { showToast("خطا: " + e.message, 4000, true); }
    };
    loaders.accounts = async () => {
      try { const d = await api("/api/v1/accounts"); accounts.value = d.items || []; }
      catch (e) { showToast("خطا: " + e.message, 4000, true); }
    };
    loaders.bots = async () => {
      try { const d = await api("/api/v1/bots"); bots.value = d.items || []; }
      catch (e) { showToast("خطا: " + e.message, 4000, true); }
    };
    loaders.eitaa = async () => {
      try { const d = await api("/api/v1/eitaa"); eitaas.value = d.items || []; }
      catch (e) { showToast("خطا: " + e.message, 4000, true); }
    };
    loaders.keys = async () => {
      try { const d = await api("/api/v1/keys"); keys.value = d.items || []; }
      catch (e) { showToast("خطا: " + e.message, 4000, true); }
    };
    loaders.files = async () => {
      try { const d = await api("/api/v1/files"); files.value = d.items || []; }
      catch (e) { showToast("خطا: " + e.message, 4000, true); }
    };
    loaders.queue = async () => {
      try {
        const [s, j] = await Promise.all([api("/api/v1/queue/stats"), api("/api/v1/queue/jobs")]);
        queueStats.value = { ...s, paused: (s.paused || []).join(",") || "-" };
        jobs.value = j.items || [];
      } catch (e) { showToast("خطا: " + e.message, 4000, true); }
    };
    loaders.audit = async () => {
      try { const d = await api("/api/v1/admin/audit"); audit.value = d.items || []; }
      catch (e) { showToast("خطا: " + e.message, 4000, true); }
    };

    /* ---------- accounts ---------- */
    function accOpen() { Object.assign(accDlg, { open: true, step: 1, phone: "", label: "", code: "", pass: "", msg: "", loginId: "" }); }
    async function accSend() {
      try {
        const d = await api("/api/v1/accounts/login/start", { method: "POST", json: { phone: accDlg.phone, label: accDlg.label } });
        if (d.status === "ready") { showToast("اکانت آماده شد"); accDlg.open = false; loaders.accounts(); return; }
        accDlg.loginId = d.login_id;
        accDlg.step = 2;
        accDlg.msg = "کد ارسال شد؛ وارد کنید:";
      } catch (e) { accDlg.msg = e.message; }
    }
    async function accDone() {
      try {
        const d = await api("/api/v1/accounts/login/complete", { method: "POST", json: { login_id: accDlg.loginId, code: accDlg.code, password: accDlg.pass } });
        if (d.status === "password_needed") { accDlg.msg = "رمز 2FA لازم است؛ وارد کرده و دوباره بزنید"; return; }
        showToast("اکانت متصل شد");
        accDlg.open = false; loaders.accounts();
      } catch (e) { accDlg.msg = e.message; }
    }
    async function accToggle(a) { await api(`/api/v1/accounts/${a.id}/toggle`, { method: "POST" }); loaders.accounts(); }
    async function accTest(a) { const d = await api(`/api/v1/accounts/${a.id}/test`, { method: "POST" }); showToast(d.ok ? "اتصال سالم" : "خطا: " + d.error, 3000, !d.ok); }
    async function accReset(a) { await api(`/api/v1/accounts/${a.id}/reset`, { method: "POST" }); showToast("ریست شد"); loaders.accounts(); }
    async function accDelete(a) { if (confirm("حذف اکانت؟")) { await api(`/api/v1/accounts/${a.id}`, { method: "DELETE" }); loaders.accounts(); } }

    /* ---------- bots ---------- */
    async function botSave() {
      try {
        const d = await api("/api/v1/bots", { method: "POST", json: { token: botDlg.token, label: botDlg.label } });
        showToast("بات @" + d.username + " متصل شد");
        Object.assign(botDlg, { open: false, token: "", label: "", msg: "" });
        loaders.bots();
      } catch (e) { botDlg.msg = e.message; }
    }
    async function botToggle(b) { await api(`/api/v1/bots/${b.id}/toggle`, { method: "POST" }); loaders.bots(); }
    async function botDelete(b) { if (confirm("حذف بات؟")) { await api(`/api/v1/bots/${b.id}`, { method: "DELETE" }); loaders.bots(); } }

    /* ---------- eitaa ---------- */
    async function eitSave() {
      try {
        await api("/api/v1/eitaa", { method: "POST", json: { token: eitDlg.token, chat_id: eitDlg.chat, label: eitDlg.label } });
        showToast("ایتایار متصل شد");
        Object.assign(eitDlg, { open: false, token: "", chat: "", label: "", msg: "" });
        loaders.eitaa();
      } catch (e) { eitDlg.msg = e.message; }
    }
    async function eitToggle(e) { await api(`/api/v1/eitaa/${e.id}/toggle`, { method: "POST" }); loaders.eitaa(); }
    async function eitTest(e) { const d = await api(`/api/v1/eitaa/${e.id}/test`, { method: "POST" }); showToast(d.ok ? "ارسال آزمایشی موفق" : "خطا: " + d.error, 3000, !d.ok); }
    async function eitDelete(e) { if (confirm("حذف توکن ایتا؟")) { await api(`/api/v1/eitaa/${e.id}`, { method: "DELETE" }); loaders.eitaa(); } }

    /* ---------- keys ---------- */
    function keyOpen() { Object.assign(keyDlg, { open: true, name: "", scopes: "upload,download", rpm: 120, quota: 0, backend: "", result: "" }); }
    async function keySave() {
      try {
        const d = await api("/api/v1/keys", { method: "POST", json: { name: keyDlg.name, scopes: keyDlg.scopes, rpm: keyDlg.rpm || 120, daily_quota_gb: keyDlg.quota || 0, backend: keyDlg.backend || undefined } });
        keyDlg.result = d.key;
        loaders.keys();
      } catch (e) { showToast(e.message, 4000, true); }
    }
    function copyKey() { navigator.clipboard.writeText(keyDlg.result); showToast("کپی شد"); }
    async function keyRevoke(k) { if (confirm("این کلید برای همیشه باطل شود؟")) { await api(`/api/v1/keys/${k.id}`, { method: "DELETE" }); loaders.keys(); } }

    /* ---------- files ---------- */
    const uploadPct = ref(0); // 0-100, real XHR progress
    let cancelXHR = null;
    async function startResumableUpload(file) {
      // Create upload session
      const r = await fetch("/api/v1/files/upload/session", {
        method: "POST",
        headers: { "Authorization": "Bearer " + token.value, "Content-Type": "application/json" },
        body: JSON.stringify({ name: file.name, size: file.size, mime: file.type || "application/octet-stream" })
      });
      if (!r.ok) { const d = await r.json(); throw new Error(d.detail || "session create failed"); }
      const { session_id, chunk_size, offset } = await r.json();
      let completed = false;
      let currentOffset = offset;

      // Upload chunks
      while (!completed) {
        const chunk = file.slice(currentOffset, Math.min(currentOffset + chunk_size, file.size));
        const r = await fetch("/api/v1/files/upload/session/" + session_id, {
          method: "PATCH",
          headers: { "Authorization": "Bearer " + token.value, "X-Offset": currentOffset },
          body: chunk
        });
        if (!r.ok) {
          const d = await r.json(); throw new Error(d.detail || "chunk upload failed");
        }
        const result = await r.json();
        currentOffset = result.offset;
        completed = result.completed;
      }

      // Get final file info
      const infoR = await fetch("/api/v1/files", {
        method: "GET",
        headers: { "Authorization": "Bearer " + token.value }
      });
      if (!infoR.ok) throw new Error("Could not get file list");
      const files = await infoR.json();
      return files.items[files.items.length - 1];
    }
    function cancelCurrentUpload() {
      if (cancelXHR && cancelXHR.readyState < 4) {
        cancelXHR.abort();
        showToast("آپلود لغو شد", 2000);
      }
    }
    async function upload(ev) {
      const f = ev.target.files[0]; if (!f) return;
      uploadBusy.value = true; uploadPct.value = 0; uploadProgress.value = f.name + " (" + fmtBytes(f.size) + ")";
      uploadTimeStart.value = Math.floor(Date.now() / 1000);
      try {
        const d = await startResumableUpload(f);
        showToast("صف شد: " + d.file_id);
        uploadProgress.value = "";
        cancelXHR = null; // reset after success
        loaders.files();
      } catch (e) { uploadProgress.value = ""; showToast("خطا: " + e.message, 4000, true); }
      cancelXHR = null; // reset after error too
      uploadBusy.value = false; uploadPct.value = 0;
      ev.target.value = "";
    }
    async function cancelUpload() {
      if (!confirm("آپلود لغو شود؟ اطلاعات فایل در صف باقی می‌ماند.")) return;
      cancelCurrentUpload();
    }
    async function fileLink(f) {
      const slug = prompt("اسلاگ لینک (خالی = خودکار):", "");
      if (slug === null) return;
      const pwd = prompt("رمز لینک (خالی = بدون رمز):", "");
      if (pwd === null) return;
      const d = await api(`/api/v1/files/${f.id}/share`, { method: "POST", json: { slug, password: pwd } });
      navigator.clipboard.writeText(d.url);
      qrDlg.url = d.url; qrDlg.slug = d.slug || slug; qrDlg.open = true;
      showToast("لینک عمومی ساخته شد", 4000);
    }
    async function fileDelete(f) {
      if (!confirm("حذف فایل؟")) return;
      const purge = confirm("حذف نهایی از تلگرام هم انجام شود؟ (OK = نهایی، Cancel = زباله‌دان)");
      await api(`/api/v1/files/${f.id}${purge ? "?purge=true" : ""}`, { method: "DELETE" });
      showToast(purge ? "حذف نهایی شد" : "به زباله‌دان رفت");
      loaders.files();
    }
    async function fileRestore(f) { await api(`/api/v1/files/${f.id}/restore`); showToast("بازیابی شد"); loaders.files(); }
    async function readLastFile() {
      try {
        const d = await api("/api/v1/files");
        const files = d.items || [];
        if (!files.length) { showToast("فایلی یافت نشد", 3000); return; }
        const last = files[files.length - 1];
        const content = await fetch("/api/v1/files/" + last.id + "/content");
        if (!content.ok) { showToast("خطا در خوانش", 2000); return; }
        showToast("فایل خوانده شد: " + last.name, 2000);
        // If it's text, show in prompt to add to DB
        const text = await content.text().catch(() => "");
        if (text && confirm("محتوای متن detected. به دیتابیس اضافه شود؟")) {
          await api("/api/v1/keys", { method: "POST", json: { name: last.name, scopes: "download", rpm: 120, backend: "custom" } });
          showToast("متن به دیتابیس اضافه شد", 3000);
        }
      } catch (e) { showToast("خطا: " + e.message, 4000, true); }
    }

    /* ---------- queue ---------- */
    async function qPause() { await api("/api/v1/queue/pause", { method: "POST", json: { kind: "upload" } }); showToast("آپلود متوقف شد"); loaders.queue(); }
    async function qResume() { await api("/api/v1/queue/resume", { method: "POST", json: { kind: "upload" } }); showToast("آپلود ادامه یافت"); loaders.queue(); }
    async function qPurge() { await api("/api/v1/queue/purge", { method: "POST" }); loaders.queue(); }
    async function backupDB() { await api("/api/v1/admin/backup"); }
    async function restoreDB() { const f = document.getElementById('restoreInput').files[0]; if (!f) return; const formData = new FormData(); formData.append('backup', f); await api("/api/v1/admin/restore", { method: "POST", body: formData, json: false }); }

    /* ---------- boot ---------- */
    onMounted(() => { if (token.value) switchTab("dash"); });

    return {
      token, login, tab, tabList, toast,
      health, accounts, bots, eitaas, keys, files, jobs, audit, queueStats,
      uploadBusy, uploadProgress, uploadPct, nowSec, dashCards,
      accDlg, botDlg, eitDlg, keyDlg, qrDlg,
      doLogin: submitLogin, logout, switchTab, fmtBytes, fmtTime,
      accOpen, accSend, accDone, accToggle, accTest, accReset, accDelete,
      botSave, botToggle, botDelete,
      eitSave, eitToggle, eitTest, eitDelete,
      keyOpen, keySave, copyKey, keyRevoke,
      upload, fileLink, fileDelete, fileRestore,
      qPause, qResume, qPurge,
    };
  },
});
app.mount("#app");
