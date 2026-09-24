<template>

  <!-- Landing / Login -->
  <div v-if="!token" class="center">
    <div class="card login-card">
      <div style="text-align:center">
        <svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" style="width:44px;height:44px;color:var(--accent)">
          <path d="M21 8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16Z"/>
          <path d="m3.3 7 8.7 5 8.7-5"/><path d="M12 22V12"/>
        </svg>
        <h1 style="font-size:22px;margin:12px 0 4px">TelegramDrive</h1>
        <p class="muted" style="font-size:14px;margin:0">پنل مدیریت فایل با تلگرام و ایتا</p>
      </div>

      <form @submit.prevent="doLogin">
        <div class="field">
          <label for="lg-user">نام کاربری</label>
          <input id="lg-user" v-model="login.user" type="text" autocomplete="username" required>
        </div>
        <div class="field" style="margin-top:10px">
          <label for="lg-pass">رمز عبور</label>
          <input id="lg-pass" v-model="login.pass" type="password" autocomplete="current-password" required>
        </div>
        <button type="submit" class="primary" style="width:100%;margin-top:16px" :disabled="login.busy">
          {{ login.busy ? "در حال ورود..." : "ورود به پنل" }}
        </button>
      </form>
      <div class="err" style="text-align:center" role="alert">{{ login.err }}</div>
      <p class="muted" style="font-size:12px;text-align:center;margin:0">TelegramDrive • نسخه ۲.۰ (Vue)</p>
    </div>
  </div>

  <!-- App (authenticated) -->
  <template v-else>
    <header>
      <div class="brand">
        <svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
          <path d="M21 8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16Z"/>
          <path d="m3.3 7 8.7 5 8.7-5"/><path d="M12 22V12"/>
        </svg>
        <span>TelegramDrive</span>
      </div>
      <nav id="tabs">
        <button v-for="t in tabList" :key="t.id" :class="{active: tab === t.id}" @click="switchTab(t.id)">{{ t.label }}</button>
      </nav>
      <button class="ghost" @click="logout">خروج</button>
    </header>

    <main>
      <!-- Dashboard -->
      <section v-show="tab === 'dash'">
        <div class="cards">
          <div class="stat" v-for="c in dashCards" :key="c[0]"><div class="lbl">{{ c[0] }}</div><div class="num">{{ c[1] }}</div></div>
        </div>
        <h3 style="font-size:16px">وضعیت اکانت‌ها</h3>
        <div class="health-grid">
          <div v-for="a in health" :key="a.id" class="health-item" :class="{bad: a.status !== 'ready'}">
            <b>{{ a.label || a.id }}</b> <span class="badge" :class="a.status">{{ a.status }}</span>
            <div class="muted" style="font-size:12px">آپلود {{ a.uploads_done }} • دانلود {{ a.downloads_done }}</div>
            <div v-if="a.flood_until > nowSec" class="warn" style="font-size:12px">flood تا {{ fmtTime(a.flood_until) }}</div>
            <div v-if="a.last_error" class="err" style="font-size:12px">{{ a.last_error }}</div>
          </div>
          <p v-if="!health.length" class="muted">اکانتی ثبت نشده</p>
        </div>
      </section>

      <!-- Accounts -->
      <section v-show="tab === 'accounts'">
        <div class="toolbar">
          <h3 style="margin:0;flex:1;font-size:16px">اکانت‌های تلگرام</h3>
          <button class="primary" @click="accOpen">اکانت جدید</button>
        </div>
        <div class="card wide">
          <table>
            <thead><tr><th>شناسه</th><th>برچسب</th><th>تلفن</th><th>وضعیت</th><th>آپلودها</th><th>دانلودها</th><th>حجم آپلود</th><th>عملیات</th></tr></thead>
            <tbody>
              <tr v-for="a in accounts" :key="a.id">
                <td>{{ a.id }}</td><td>{{ a.label }}</td><td dir="ltr">{{ a.phone }}</td>
                <td><span class="badge" :class="a.status">{{ a.status }}</span></td>
                <td>{{ a.uploads_done }}</td><td>{{ a.downloads_done }}</td><td>{{ fmtBytes(a.bytes_up) }}</td>
                <td>
                  <button @click="accToggle(a)">{{ a.enabled ? "غیرفعال" : "فعال" }}</button>
                  <button @click="accTest(a)">تست</button>
                  <button @click="accReset(a)">ریست</button>
                  <button class="danger" @click="accDelete(a)">حذف</button>
                </td>
              </tr>
              <tr v-if="!accounts.length"><td colspan="8" class="muted">اکانتی نیست</td></tr>
            </tbody>
          </table>
        </div>
      </section>

      <!-- Bots -->
      <section v-show="tab === 'bots'">
        <div class="toolbar">
          <h3 style="margin:0;flex:1;font-size:16px">ربات‌های تلگرام</h3>
          <button class="primary" @click="botDlg.open = true">بات جدید</button>
        </div>
        <div class="card wide">
          <table>
            <thead><tr><th>شناسه</th><th>برچسب</th><th>وضعیت</th><th>خطا</th><th>عملیات</th></tr></thead>
            <tbody>
              <tr v-for="b in bots" :key="b.id">
                <td>{{ b.id }}</td><td>{{ b.label }}</td>
                <td><span class="badge" :class="b.status">{{ b.status }}</span></td>
                <td class="muted">{{ b.last_error }}</td>
                <td>
                  <button @click="botToggle(b)">{{ b.enabled ? "غیرفعال" : "فعال" }}</button>
                  <button class="danger" @click="botDelete(b)">حذف</button>
                </td>
              </tr>
              <tr v-if="!bots.length"><td colspan="5" class="muted">باتی نیست</td></tr>
            </tbody>
          </table>
        </div>
      </section>

      <!-- Eitaa -->
      <section v-show="tab === 'eitaa'">
        <div class="toolbar">
          <h3 style="margin:0;flex:1;font-size:16px">ایتایار</h3>
          <button class="primary" @click="eitDlg.open = true">اکانت جدید</button>
        </div>
        <div class="card wide">
          <table>
            <thead><tr><th>شناسه</th><th>برچسب</th><th>کانال مقصد</th><th>وضعیت</th><th>عملیات</th></tr></thead>
            <tbody>
              <tr v-for="e in eitaas" :key="e.id">
                <td>{{ e.id }}</td><td>{{ e.label }}</td><td dir="ltr">{{ e.chat_id || "-" }}</td>
                <td><span class="badge" :class="e.status">{{ e.status }}</span></td>
                <td>
                  <button @click="eitToggle(e)">{{ e.enabled ? "غیرفعال" : "فعال" }}</button>
                  <button @click="eitTest(e)">تست</button>
                  <button class="danger" @click="eitDelete(e)">حذف</button>
                </td>
              </tr>
              <tr v-if="!eitaas.length"><td colspan="5" class="muted">توکنی ثبت نشده</td></tr>
            </tbody>
          </table>
        </div>
      </section>

      <!-- Keys -->
      <section v-show="tab === 'keys'">
        <div class="toolbar">
          <h3 style="margin:0;flex:1;font-size:16px">کلیدهای API</h3>
          <button class="primary" @click="keyOpen">کلید جدید</button>
        </div>
        <div class="card wide">
          <table>
            <thead><tr><th>شناسه</th><th>نام</th><th>پیشوند</th><th>سکوپ‌ها</th><th>ذخیره‌سازی</th><th>RPM</th><th>مصرف امروز</th><th>وضعیت</th><th>عملیات</th></tr></thead>
            <tbody>
              <tr v-for="k in keys" :key="k.id">
                <td>{{ k.id }}</td><td>{{ k.name }}</td>
                <td dir="ltr"><code>{{ k.key_prefix }}...</code></td>
                <td>{{ k.scopes }}</td><td>{{ k.backend || "پیش‌فرض" }}</td>
                <td>{{ k.rpm }}</td><td>{{ fmtBytes(k.used_bytes_today) }}</td>
                <td><span class="badge" :class="k.revoked ? 'revoked' : 'ready'">{{ k.revoked ? "revoked" : "active" }}</span></td>
                <td><button v-if="!k.revoked" class="danger" @click="keyRevoke(k)">Revoke</button></td>
              </tr>
              <tr v-if="!keys.length"><td colspan="9" class="muted">کلیدی نیست</td></tr>
            </tbody>
          </table>
        </div>
      </section>

      <!-- Files -->
      <section v-show="tab === 'files'">
        <div class="toolbar">
          <h3 style="margin:0;flex:1;font-size:16px">فایل‌ها</h3>
          <button class="primary" :disabled="uploadBusy" @click="$refs.fileInput.click()">
            {{ uploadBusy ? "در حال آپلود..." : "آپلود جدید" }}
          </button>
          <input ref="fileInput" type="file" style="display:none" @change="upload">
        </div>
        <p v-if="uploadProgress" class="muted">
          {{ uploadProgress }} — {{ uploadPct }}%
          <span v-if="uploadSpeed > 0" class="muted" style="margin-left:12px">~{{ uploadSpeed }} MB/s</span>
          <span class="progress-track" role="progressbar" :aria-valuenow="uploadPct" aria-valuemin="0" aria-valuemax="100">
            <span class="progress-fill" :style="{ width: uploadPct + '%' }"></span>
          </span>
          <button class="ghost" size="sm" @click="cancelUpload" style="position:absolute;right:12px;top:50%;transform:translateY(-50%);padding:4px 8px;font-size:12px" :disabled="uploadPct === 0">Cancel</button>
        </p>
        <button class="ghost" @click="readLastFile" style="margin-left:8px">خوانش آخرین فایل</button>
        <div class="card wide">
          <table>
            <thead><tr><th>شناسه</th><th>نام</th><th>حجم</th><th>وضعیت</th><th>ذخیره</th><th>دانلود</th><th>تاریخ</th><th>عملیات</th></tr></thead>
            <tbody>
              <tr v-for="f in files" :key="f.id">
                <td dir="ltr"><code>{{ f.id }}</code></td>
                <td>{{ f.name }}</td><td>{{ fmtBytes(f.size) }}</td>
                <td><span class="badge" :class="f.status">{{ f.status }}</span></td>
                <td>{{ f.backend || "tg" }}</td><td>{{ f.downloads }}</td>
                <td class="muted">{{ fmtTime(f.created_at) }}</td>
                <td>
                  <button @click="fileLink(f)">لینک</button>
                  <button v-if="f.deleted_at" @click="fileRestore(f)">بازیابی</button>
                  <button class="danger" @click="fileDelete(f)">حذف</button>
                </td>
              </tr>
              <tr v-if="!files.length"><td colspan="8" class="muted">فایلی نیست</td></tr>
            </tbody>
          </table>
        </div>
      </section>

      <!-- Queue -->
      <section v-show="tab === 'queue'">
        <div class="toolbar">
          <h3 style="margin:0;flex:1;font-size:16px">صف انتظار</h3>
          <button @click="qPause">توقف آپلود</button>
          <button @click="qResume">ادامه آپلود</button>
          <button class="danger" @click="qPurge">پاک کردن صف</button>
          <button class="ghost" style="margin-left:8px" @click="backupDB">بکاپ</button>
          <input type="file" style="display:none" id="restoreInput" @change="restoreDB"/><button class="ghost" style="margin-left:8px" @click="document.getElementById('restoreInput').click()">بازگردانی</button>
        </div>
        <div class="cards">
          <div class="stat" v-for="(v, k) in queueStats" :key="k"><div class="lbl">{{ k }}</div><div class="num">{{ v }}</div></div>
        </div>
        <div class="card wide">
          <table>
            <thead><tr><th>شناسه</th><th>نوع</th><th>اولویت</th><th>وضعیت</th><th>تلاش</th><th>خطا</th></tr></thead>
            <tbody>
              <tr v-for="j in jobs" :key="j.id">
                <td dir="ltr"><code>{{ j.id }}</code></td><td>{{ j.kind }}</td><td>{{ j.priority }}</td>
                <td><span class="badge" :class="j.status">{{ j.status }}</span></td>
                <td>{{ j.attempts }}</td><td class="muted">{{ j.error }}</td>
              </tr>
              <tr v-if="!jobs.length"><td colspan="6" class="muted">جابی نیست</td></tr>
            </tbody>
          </table>
        </div>
      </section>

      <!-- Audit -->
      <section v-show="tab === 'audit'">
        <h3 style="font-size:16px;margin-top:0">لاگ‌های سیستم</h3>
        <div class="card wide">
          <table>
            <thead><tr><th>زمان</th><th>کاربر</th><th>اکشن</th><th>هدف</th><th>IP</th></tr></thead>
            <tbody>
              <tr v-for="(a, i) in audit" :key="i">
                <td class="muted">{{ fmtTime(a.ts) }}</td><td>{{ a.actor }}</td>
                <td>{{ a.action }}</td><td dir="ltr">{{ a.target }}</td><td dir="ltr">{{ a.ip }}</td>
              </tr>
              <tr v-if="!audit.length"><td colspan="5" class="muted">لاگی نیست</td></tr>
            </tbody>
          </table>
        </div>
      </section>
    </main>

    <!-- Dialogs -->
    <dialog :open="accDlg.open" @close="accDlg.open = false">
      <h3>افزودن اکانت تلگرام</h3>
      <template v-if="accDlg.step === 1">
        <div class="field"><label>تلفن</label><input v-model="accDlg.phone" type="text" placeholder="+98912..."></div>
        <div class="field"><label>برچسب (اختیاری)</label><input v-model="accDlg.label" type="text"></div>
        <button class="primary" style="width:100%" @click="accSend">ارسال کد تایید</button>
      </template>
      <template v-else>
        <div class="field"><label>کد تایید</label><input v-model="accDlg.code" type="text" placeholder="12345"></div>
        <div class="field"><label>رمز 2FA (اختیاری)</label><input v-model="accDlg.pass" type="password"></div>
        <button class="primary" style="width:100%" @click="accDone">اتمام</button>
      </template>
      <p class="err">{{ accDlg.msg }}</p>
      <button class="ghost" style="width:100%" @click="accDlg.open = false">انصراف</button>
    </dialog>

    <dialog :open="botDlg.open" @close="botDlg.open = false">
      <h3>افزودن ربات</h3>
      <div class="field"><label>توکن ربات</label><input v-model="botDlg.token" type="password" placeholder="123456:ABC-DEF..."></div>
      <div class="field"><label>برچسب (اختیاری)</label><input v-model="botDlg.label" type="text"></div>
      <button class="primary" style="width:100%" @click="botSave">ذخیره</button>
      <p class="err">{{ botDlg.msg }}</p>
      <button class="ghost" style="width:100%" @click="botDlg.open = false">انصراف</button>
    </dialog>

    <dialog :open="eitDlg.open" @close="eitDlg.open = false">
      <h3>افزودن ایتایار</h3>
      <div class="field"><label>توکن ایتا</label><input v-model="eitDlg.token" type="password"></div>
      <div class="field"><label>کانال مقصد</label><input v-model="eitDlg.chat" type="text" dir="ltr" placeholder="123456789"></div>
      <div class="field"><label>برچسب (اختیاری)</label><input v-model="eitDlg.label" type="text"></div>
      <button class="primary" style="width:100%" @click="eitSave">ذخیره</button>
      <p class="err">{{ eitDlg.msg }}</p>
      <button class="ghost" style="width:100%" @click="eitDlg.open = false">انصراف</button>
    </dialog>

    <dialog :open="keyDlg.open" @close="keyDlg.open = false">
      <h3>ایجاد کلید API</h3>
      <div class="field"><label>نام</label><input v-model="keyDlg.name" type="text"></div>
      <div class="field"><label>سکوپ‌ها (با ویرگول)</label><input v-model="keyDlg.scopes" type="text" dir="ltr" placeholder="upload,download"></div>
      <div style="display:flex;gap:10px">
        <div class="field" style="flex:1"><label>RPM</label><input v-model.number="keyDlg.rpm" type="number"></div>
        <div class="field" style="flex:1"><label>سهمیه روزانه (GB)</label><input v-model.number="keyDlg.quota" type="number"></div>
      </div>
      <div class="field">
        <label>بک‌اند</label>
        <select v-model="keyDlg.backend">
          <option value="">پیش‌فرض (تلگرام)</option>
          <option value="eitaa">ایتا</option>
        </select>
      </div>
      <button class="primary" style="width:100%" @click="keySave">ایجاد</button>
      <div v-if="keyDlg.result" style="background:var(--panel2);padding:12px;border-radius:8px">
        <p class="muted" style="font-size:12px;margin:0 0 6px">کلید (فقط همین یک بار نمایش داده می‌شود):</p>
        <code style="word-break:break-all">{{ keyDlg.result }}</code>
        <button style="margin-top:8px;width:100%" @click="copyKey">کپی</button>
      </div>
      <button class="ghost" style="width:100%" @click="keyDlg.open = false">انصراف</button>
    </dialog>

    <dialog :open="qrDlg.open" @close="qrDlg.open = false">
      <h3>QR لینک عمومی</h3>
      <div style="background:#fff;padding:12px;border-radius:8px;display:flex;justify-content:center">
        <img v-if="qrDlg.slug" :src="'/' + qrDlg.slug + '/qr'" alt="QR" width="220" height="220">
      </div>
      <p dir="ltr" style="word-break:break-all;font-size:13px">{{ qrDlg.url }}</p>
      <button class="ghost" style="width:100%" @click="qrDlg.open = false">بستن</button>
    </dialog>      <!-- Proxies tab -->
      <section v-show="tab === 'proxies'">
        <div class="toolbar">
          <h3 style="margin:0;flex:1;font-size:16px">پراکسی‌های تلگرام</h3>
          <button class="ghost" :disabled="proxiesTesting" @click="proxyTestAll">{{ proxiesTesting ? "در حال تست…" : "تست سرعت همه" }}</button>
          <button class="ghost" @click="proxyApply">اعمال اتصال مجدد</button>
          <button class="primary" @click="proxyOpen">پراکسی جدید</button>
        </div>
        <p class="muted" style="font-size:12px;margin:0 0 10px">
          فعال/غیرفعال کردن استفاده از پراکسی از تب <b>تنظیمات</b> → گروه «پراکسی» انجام می‌شود.
          لیست بر اساس سرعت (کم‌ترین تاخیر) مرتب است و انتخاب پراکسی برای اتصال‌های جدید به همین ترتیب انجام می‌شود.
        </p>
        <div class="card wide">
          <table>
            <thead><tr><th>#</th><th>برچسب</th><th>نوع</th><th>آدرس</th><th>وضعیت</th><th>تاخیر</th><th>آخرین تست</th><th>عملیات</th></tr></thead>
            <tbody>
              <tr v-for="p in proxiesList" :key="p.id" :style="p.enabled ? '' : 'opacity:.5'">
                <td>{{ p.id }}</td>
                <td>{{ p.label || '—' }}</td>
                <td><span class="badge">{{ p.kind }}</span></td>
                <td dir="ltr">{{ p.host }}:{{ p.port }}</td>
                <td><span class="badge" :class="p.status">{{ proxyStatusLabels[p.status] || p.status }}</span></td>
                <td :style="p.latency_ms >= 0 ? 'color:var(--accent);font-weight:600' : ''">{{ fmtLatency(p.latency_ms) }}</td>
                <td class="muted" style="font-size:12px">{{ p.last_checked_at ? fmtTime(p.last_checked_at) : '—' }}</td>
                <td>
                  <button :disabled="p._testing" @click="proxyTestOne(p)">{{ p._testing ? "…" : "تست" }}</button>
                  <button @click="proxyToggle(p)">{{ p.enabled ? "غیرفعال" : "فعال" }}</button>
                  <button class="danger" @click="proxyDelete(p)">حذف</button>
                </td>
              </tr>
              <tr v-if="!proxiesList.length"><td colspan="8" class="muted">پراکسی‌ای اضافه نشده — اتصال مستقیم استفاده می‌شود</td></tr>
            </tbody>
          </table>
        </div>
      </section>

      <!-- Add proxy dialog -->
      <dialog :open="proxyDlg.open" @close="proxyDlg.open = false">
        <h3>افزودن پراکسی تلگرام</h3>
        <div class="field">
          <label>لینک اشتراک‌گذاری (tg://proxy یا t.me/proxy یا socks5://user:pass@host:port)</label>
          <input v-model="proxyDlg.link" type="text" dir="ltr" placeholder="tg://proxy?server=..&port=..&secret=..">
        </div>
        <p class="muted" style="font-size:12px;margin:0 0 8px">— یا ورود دستی:</p>
        <div style="display:flex;gap:10px">
          <div class="field" style="flex:1">
            <label>نوع</label>
            <select v-model="proxyDlg.kind">
              <option value="mtproto">MTProto</option>
              <option value="socks5">SOCKS5</option>
              <option value="http">HTTP</option>
            </select>
          </div>
          <div class="field" style="flex:2"><label>هاست</label><input v-model="proxyDlg.host" type="text" dir="ltr"></div>
          <div class="field" style="flex:1"><label>پورت</label><input v-model="proxyDlg.port" type="number" dir="ltr"></div>
        </div>
        <template v-if="proxyDlg.kind !== 'mtproto'">
          <div style="display:flex;gap:10px">
            <div class="field" style="flex:1"><label>نام کاربری (اختیاری)</label><input v-model="proxyDlg.username" type="text" dir="ltr"></div>
            <div class="field" style="flex:1"><label>رمز (اختیاری)</label><input v-model="proxyDlg.password" type="password" dir="ltr"></div>
          </div>
        </template>
        <div class="field"><label>برچسب (اختیاری)</label><input v-model="proxyDlg.label" type="text"></div>
        <p class="err">{{ proxyDlg.msg }}</p>
        <button class="primary" style="width:100%" @click="proxySave">افزودن</button>
        <button class="ghost" style="width:100%" @click="proxyDlg.open = false">انصراف</button>
      </dialog>

      <!-- Settings tab -->
      <section v-show="tab === 'settings'">
        <div class="toolbar">
          <h3 style="margin:0;flex:1;font-size:16px">تنظیمات سیستم (Runtime)</h3>
          <button class="ghost" @click="resetSettings">بازگشت به پیش‌فرض</button>
          <button class="ghost" v-show="dirtyCount" @click="discardSettings">لغو تغییرات</button>
          <button class="primary" :disabled="settingsBusy" @click="saveSettings">ذخیره و اعمال</button>
        </div>
        <div class="card wide" v-show="dirtyCount" style="margin-bottom:14px;border-color:var(--warn);padding:10px 14px;display:flex;align-items:center;gap:10px">
          <b style="font-size:13px">{{ dirtyCount }} مقدار ویرایش شده اما هنوز ذخیره نشده است.</b>
          <span class="muted" style="font-size:12px">برای اعمال، «ذخیره و اعمال» را بزنید.</span>
        </div>
        <div v-for="g in settingsGroups" :key="g.id" class="card wide" style="margin-bottom:14px">
          <h3 style="font-size:15px;margin:0 0 10px">{{ g.title }}</h3>
          <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:10px">
            <div class="field" v-for="it in g.items" :key="it.key">
              <label>{{ settingLabels[it.key] || it.key }} <span class="muted" style="font-size:11px">{{ fmtSettingHint(it) }} ({{ it.source === 'db' ? 'ذخیره‌شده' : 'پیش‌فرض env' }})</span></label>
              <select v-if="it.key === 'default_backend'" v-model="settingsDraft[it.key]"
                :style="settingsErrors[it.key] ? 'border-color:var(--err)' : ''">
                <option value="telegram">تلگرام</option>
                <option value="eitaa">ایتا</option>
              </select>
              <select v-else-if="it.key === 'proxy_strategy'" v-model="settingsDraft[it.key]"
                :style="settingsErrors[it.key] ? 'border-color:var(--err)' : ''">
                <option value="speed">سریع‌ترین (بر اساس تست سرعت)</option>
                <option value="rr">چرخشی (Round-Robin)</option>
              </select>
              <select v-else-if="it.key === 'proxy_enabled'" v-model="settingsDraft[it.key]">
                <option value="0">غیرفعال — اتصال مستقیم</option>
                <option value="1">فعال — از پراکسی‌ها استفاده شود</option>
              </select>
              <input v-else v-model="settingsDraft[it.key]" :type="it.type === 'int' ? 'number' : 'text'"
                :class="{ invalid: settingsErrors[it.key] }"
                :style="settingsErrors[it.key] ? 'border-color:var(--err)' : ''"
                @input="onSettingInput(it)">
              <p class="err" v-if="settingsErrors[it.key]" style="margin:4px 0 0;font-size:12px">{{ settingsErrors[it.key] }}</p>
            </div>
          </div>
        </div>

        <div class="card wide" style="margin-bottom:14px">
          <h3 style="font-size:15px;margin:0 0 10px">نودهای متصل (چندسرور)</h3>
          <table>
            <thead><tr><th>نود</th><th>هاست</th><th>ورکرها (dl/ul)</th><th>آخرین ضربان</th></tr></thead>
            <tbody>
              <tr v-for="n in nodesList" :key="n.node_id">
                <td dir="ltr">{{ n.node_id }}</td><td dir="ltr">{{ n.hostname }}</td>
                <td>{{ n.workers_dl }} / {{ n.workers_ul }}</td>
                <td>{{ fmtTime(n.last_heartbeat) }}</td>
              </tr>
              <tr v-if="!nodesList.length"><td colspan="4" class="muted">نودی ثبت نشده (حالت تک‌سرور)</td></tr>
            </tbody>
          </table>
        </div>

        <div class="card wide">
          <h3 style="font-size:15px;margin:0 0 10px">راه‌اندازی اولیه (استارتر)</h3>
          <p class="muted" style="font-size:13px;margin:0 0 8px" v-if="setupNeeded">سیستم هنوز تکمیل راه‌اندازی نشده است.</p>
          <p class="muted" style="font-size:13px;margin:0 0 8px" v-else>راه‌اندازی اولیه انجام شده است. ✅</p>
          <button class="primary" @click="setupDlg.open = true; setupDlg.step = 1">باز کردن ویزارد</button>
        </div>
      </section>

      <!-- Setup wizard dialog -->
      <dialog :open="setupDlg.open" @close="setupDlg.open = false">
        <h3>راه‌اندازی اولیه — مرحله {{ setupDlg.step }} از ۳</h3>
        <template v-if="setupDlg.step === 1">
          <p class="muted" style="font-size:13px">رمز ادمین را تغییر دهید (اختیاری — خالی بگذارید تا تغییر نکند).</p>
          <div class="field"><label>رمز جدید</label><input v-model="setupDlg.pass" type="password" autocomplete="new-password"></div>
          <div class="field"><label>تکرار رمز</label><input v-model="setupDlg.pass2" type="password" autocomplete="new-password"></div>
        </template>
        <template v-else-if="setupDlg.step === 2">
          <p class="muted" style="font-size:13px">برای افزودن اکانت تلگرام/بات/ایتایار از تب‌های «اکانت‌ها»، «بات‌ها» و «ایتا» استفاده کنید، سپس برگردید و مرحله بعد را بزنید.</p>
        </template>
        <template v-else>
          <div class="field">
            <label>بک‌اند پیش‌فرض ذخیره‌سازی</label>
            <select v-model="setupDlg.backend">
              <option value="telegram">تلگرام</option>
              <option value="eitaa">ایتا</option>
            </select>
          </div>
        </template>
        <p class="err">{{ setupDlg.msg }}</p>
        <button class="primary" style="width:100%" @click="setupNext">{{ setupDlg.step === 3 ? "تکمیل راه‌اندازی" : "مرحله بعد" }}</button>
        <button class="ghost" style="width:100%" @click="setupSkip">فعلاً نه</button>
      </dialog>

    <!-- Toast -->
    <div class="toast" :class="{error: toast.err}" v-show="toast.msg" role="status" aria-live="polite">{{ toast.msg }}</div>
  </template>
</template>

<script setup>
import { ref, reactive, computed, onMounted } from "vue";

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
      { id: "proxies", label: "پراکسی‌ها" },
      { id: "settings", label: "تنظیمات" },
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
    const nodesList = ref([]);
    const proxiesList = ref([]);
    const proxiesBusy = ref(false);
    const proxiesTesting = ref(false);
    const uploadBusy = ref(false);
    const uploadProgress = ref("");
    const uploadPct = ref(0); // 0-100, real XHR progress
    const uploadSpeed = ref(0); // MB/s
    const uploadTimeStart = ref(0); // timestamp
    const nowSec = Math.floor(Date.now() / 1000);

    /* ---------- dialogs ---------- */
    const proxyDlg = reactive({ open: false, link: "", host: "", port: "", kind: "socks5", label: "", username: "", password: "", msg: "" });
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
        checkSetup();
        showToast("خوش آمدید");
      } catch (e) { login.err = e.message || "نام کاربری یا رمز اشتباه است"; }
      login.busy = false;
    }
const doLogin = submitLogin;

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
        ["نودها", ov.nodes != null ? ov.nodes : 1],
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

    /* ---------- telegram proxy pool ---------- */
    const proxyStatusLabels = { ok: "سالم", down: "قطع", degraded: "ضعیف", unknown: "آزمایش نشده" };
    function fmtLatency(ms) {
      if (ms == null || ms < 0) return "—";
      return (Number(ms) / 1000).toFixed(2) + " ثانیه";
    }
    loaders.proxies = async () => {
      try { const d = await api("/api/v1/admin/proxies"); proxiesList.value = d.items || []; }
      catch (e) { showToast("خطا: " + e.message, 4000, true); }
    };
    function proxyOpen() { Object.assign(proxyDlg, { open: true, link: "", host: "", port: "", kind: "socks5", label: "", username: "", password: "", msg: "" }); }
    async function proxySave() {
      try {
        const body = proxyDlg.link.trim()
          ? { link: proxyDlg.link.trim(), label: proxyDlg.label }
          : { host: proxyDlg.host.trim(), port: Number(proxyDlg.port), kind: proxyDlg.kind, label: proxyDlg.label, username: proxyDlg.username, password: proxyDlg.password };
        await api("/api/v1/admin/proxies", { method: "POST", json: body });
        showToast("پراکسی اضافه شد");
        Object.assign(proxyDlg, { open: false });
        loaders.proxies();
      } catch (e) { proxyDlg.msg = e.message; }
    }
    async function proxyDelete(p) { if (confirm("این پراکسی حذف شود؟")) { await api(`/api/v1/admin/proxies/${p.id}`, { method: "DELETE" }); loaders.proxies(); } }
    async function proxyToggle(p) { await api(`/api/v1/admin/proxies/${p.id}`, { method: "PATCH", json: { enabled: !p.enabled } }); loaders.proxies(); }
    async function proxyTestOne(p) {
      p._testing = true;
      try { const d = await api(`/api/v1/admin/proxies/${p.id}/test`, { method: "POST" }); Object.assign(p, d.item); showToast("تست شد: " + fmtLatency(d.item.latency_ms)); }
      catch (e) { showToast("خطا: " + e.message, 4000, true); }
      p._testing = false;
    }
    async function proxyTestAll() {
      proxiesTesting.value = true;
      try {
        const d = await api("/api/v1/admin/proxies/test", { method: "POST", timeout: 60000 });
        proxiesList.value = d.items || [];
        showToast("تست همه پراکسی‌ها انجام شد");
      } catch (e) { showToast("خطا: " + e.message, 4000, true); }
      proxiesTesting.value = false;
    }
    async function proxyApply() {
      try { await api("/api/v1/admin/proxies/apply", { method: "POST" }); showToast("اتصال‌های تلگرام با پراکسی جدید برقرار شد"); }
      catch (e) { showToast("خطا: " + e.message, 4000, true); }
    }

    /* ---------- runtime settings (admin) ---------- */
    const settingsItems = ref([]);
    const settingsDraft = reactive({});
    const settingsBusy = ref(false);
    const settingsErrors = reactive({}); // key → error message

    function validateSetting(it, value) {
      if (it.type === "int") {
        if (value === "" || value === null || value === undefined || Number.isNaN(Number(value)))
          return "عدد وارد کنید";
        if (!Number.isInteger(Number(value))) return "عدد صحیح وارد کنید";
        if (Number(value) < 0) return "باید ≥ 0 باشد";
        const mustBePositive = ["max_upload_size", "split_threshold", "default_key_rpm", "presigned_ttl", "upload_session_ttl_minutes", "download_workers", "upload_workers", "max_concurrent_downloads", "max_concurrent_uploads"];
        if (mustBePositive.includes(it.key) && Number(value) < 1) return "باید ≥ 1 باشد";
      } else if (it.key === "default_backend") {
        if (!["telegram", "eitaa"].includes(value)) return "تلگرام یا ایتا";
      } else if (it.key === "proxy_strategy") {
        if (!["speed", "rr"].includes(value)) return "speed یا rr";
      } else if (it.key === "proxy_enabled") {
        if (![0, 1, "0", "1"].includes(value)) return "فقط ۰ یا ۱";
      } else if (it.key === "proxy_monitor_interval") {
        const n = Number(value);
        if (!(n === 0 || (n >= 2 && n <= 1440))) return "۰=خاموش یا ۲ تا ۱۴۴۰ دقیقه";
      }
      return "";
    }
    function dirtySettings() {
      const updates = {};
      for (const it of settingsItems.value) {
        const v = settingsDraft[it.key];
        if (v !== "" && v != null && String(v) !== String(it.current)) updates[it.key] = v;
      }
      return updates;
    }
    const dirtyCount = computed(() => Object.keys(dirtySettings()).length);
    const settingLabels = {
      max_upload_size: "حداکثر حجم آپلود (بایت)",
      split_threshold: "آستانه تقسیم فایل (بایت)",
      default_key_rpm: "RPM پیش‌فرض کلیدها",
      default_key_daily_quota: "سهمیه روزانه پیش‌فرض (بایت)",
      blocked_extensions: "پسوندهای مسدود",
      presigned_ttl: "TTL لینک امضاشده (ثانیه)",
      upload_session_ttl_minutes: "TTL سشن آپلود (دقیقه)",
      job_max_retries: "حداکثر تلاش مجدد جاب",
      download_workers: "ورکرهای دانلود",
      upload_workers: "ورکرهای آپلود",
      max_concurrent_downloads: "دانلود همزمان هر اکانت",
      max_concurrent_uploads: "آپلود همزمان",
      default_backend: "بک‌اند پیش‌فرض",
      proxy_enabled: "استفاده از پراکسی (۰=خیر، ۱=بله)",
      proxy_strategy: "استراتژی انتخاب پراکسی",
      proxy_monitor_interval: "بازه تست دوره‌ای پراکسی‌ها (دقیقه؛ ۰=خاموش)",
    };
    const settingsGroups = computed(() => {
      const g = { limits: "محدودیت‌ها", links: "لینک و انقضا", queue: "صف و همزمانی", backend: "بک‌اند", proxy: "پراکسی تلگرام" };
      const out = [];
      for (const [gid, title] of Object.entries(g)) {
        out.push({ id: gid, title, items: settingsItems.value.filter((x) => x.group === gid) });
      }
      return out;
    });
    loaders.settings = async () => {
      try {
        const [s, n] = await Promise.all([api("/api/v1/admin/settings"), api("/api/v1/admin/nodes")]);
        settingsItems.value = s.items || [];
        for (const it of settingsItems.value) settingsDraft[it.key] = it.current;
        for (const k of Object.keys(settingsErrors)) delete settingsErrors[k];
        nodesList.value = n.items || [];
      } catch (e) { showToast("خطا: " + e.message, 4000, true); }
    };
    async function saveSettings() {
      // validate every touched field first
      for (const it of settingsItems.value) {
        const v = settingsDraft[it.key];
        const err = (v !== "" && v != null) ? validateSetting(it, v) : "";
        if (err) settingsErrors[it.key] = err; else delete settingsErrors[it.key];
      }
      if (Object.keys(settingsErrors).length) { showToast("خطاهای اعتبارسنجی را برطرف کنید", 4000, true); return; }
      const updates = dirtySettings();
      for (const [k, v] of Object.entries(updates)) updates[k] = settingsItems.value.find(i => i.key === k)?.type === "int" ? Number(v) : String(v);
      if (!Object.keys(updates).length) { showToast("تغییری برای ذخیره نیست"); return; }
      settingsBusy.value = true;
      try {
        await api("/api/v1/admin/settings", { method: "PUT", json: updates });
        showToast("تنظیمات ذخیره و اعمال شد");
        await loaders.settings();
      } catch (e) { showToast("خطا: " + e.message, 4500, true); }
      settingsBusy.value = false;
    }
    async function resetSettings() {
      if (!confirm("همه تنظیمات به پیش‌فرض env برگردد؟")) return;
      await api("/api/v1/admin/settings/reset", { method: "POST" });
      showToast("به پیش‌فرض برگشت");
      await loaders.settings();
    }
    function discardSettings() {
      for (const it of settingsItems.value) settingsDraft[it.key] = it.current;
      for (const k of Object.keys(settingsErrors)) delete settingsErrors[k];
      showToast("تغییرات ذخیره‌نشده لغو شد");
    }
    function onSettingInput(it) {
      const v = settingsDraft[it.key];
      if (v === "" || v == null) { delete settingsErrors[it.key]; return; }
      const err = validateSetting(it, v);
      if (err) settingsErrors[it.key] = err; else delete settingsErrors[it.key];
    }
    function fmtSettingHint(it) {
      if (it.type === "int" && (it.key.includes("size") || it.key.includes("quota"))) {
        const base = dirtySettings()[it.key] != null ? Number(dirtySettings()[it.key]) : it.current;
        return " (= " + fmtBytes(base) + ")";
      }
      return "";
    }

    /* ---------- setup wizard (starter) ---------- */
    const setupDlg = reactive({ open: false, step: 1, pass: "", pass2: "", backend: "telegram", msg: "" });
    const setupNeeded = ref(false);
    async function checkSetup() {
      try {
        const d = await api("/api/v1/admin/setup/status");
        setupNeeded.value = !d.initialized;
        if (!d.initialized) { Object.assign(setupDlg, { open: true, step: 1, pass: "", pass2: "", backend: "telegram", msg: "" }); }
      } catch (e) { /* non-admin or transient: ignore */ }
    }
    async function setupNext() {
      if (setupDlg.step === 1) {
        if (setupDlg.pass || setupDlg.pass2) {
          if (setupDlg.pass.length < 6) { setupDlg.msg = "رمز حداقل ۶ کاراکتر باشد"; return; }
          if (setupDlg.pass !== setupDlg.pass2) { setupDlg.msg = "تکرار رمز مطابقت ندارد"; return; }
        }
        setupDlg.msg = ""; setupDlg.step = 2;
      } else if (setupDlg.step === 2) {
        setupDlg.step = 3;
      } else {
        try {
          await api("/api/v1/admin/setup/complete", { method: "POST", json: { new_password: setupDlg.pass || "", default_backend: setupDlg.backend } });
          setupDlg.open = false; setupNeeded.value = false;
          showToast("راه‌اندازی اولیه کامل شد");
        } catch (e) { setupDlg.msg = e.message; }
      }
    }
    function setupSkip() {
      setupDlg.open = false;
      showToast("می‌توانید بعداً از تب تنظیمات ادامه دهید");
    }

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
    onMounted(() => { if (token.value) { switchTab("dash"); checkSetup(); } });

// bindings auto-exposed by script setup

</script>
