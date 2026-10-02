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

    <!-- admin pressure banner: upload-queue stall / telegram flood (global) -->
    <div v-if="pressureSummary" class="pressure-banner" role="alert">
      <span class="pb-icon">⚠</span>
      <span>{{ pressureSummary }}</span>
      <div class="pb-detail">
        <template v-if="pressure.stall && pressure.stall.waiting">{{ pressure.stall.waiting }} جاب منتظر — قدیمی‌ترین {{ Math.round(pressure.stall.oldest_waiting_s) }} ثانیه؛ سقف آپلود همزمان {{ pressure.stall.gate_capacity }}.</template>
        <template v-if="pressure.flooded && pressure.flooded.length"> بک‌اندهای در فشار: {{ pressure.flooded.map(f => f.key + ' (' + f.remaining_s + 's)').join('، ') }}.</template>
      </div>
    </div>

    <main>
      <!-- Dashboard -->
      <section v-show="tab === 'dash'">
        <div class="cards">
          <div class="stat" v-for="c in dashCards" :key="c[0]"><div class="lbl">{{ c[0] }}</div><div class="num">{{ c[1] }}</div></div>
        </div>
        <h3 style="font-size:16px">سلامت پراکسی‌های تلگرام</h3>
        <div class="card wide" v-if="overview && overview.proxies">
          <div class="cards" style="margin:0">
            <div class="stat"><div class="lbl">وضعیت استخر</div><div class="num">{{ overview.proxies.enabled ? "فعال" : "خاموش" }}</div></div>
            <div class="stat"><div class="lbl">زنده (سالم)</div><div class="num" style="color:var(--ok,#22C55E)">{{ overview.proxies.alive }}</div></div>
            <div class="stat"><div class="lbl">قطع (down)</div><div class="num" style="color:var(--err,#EF4444)">{{ overview.proxies.down }}</div></div>
            <div class="stat"><div class="lbl">علامت‌خورده (fallback)</div><div class="num" style="color:var(--warn,#F59E0B)">{{ overview.proxies.dead_marked }}</div></div>
            <div class="stat"><div class="lbl">آزمایش‌نشده</div><div class="num">{{ overview.proxies.unknown }}</div></div>
            <div class="stat"><div class="lbl">کم‌ترین تاخیر</div><div class="num">{{ overview.proxies.best_latency_ms >= 0 ? overview.proxies.best_latency_ms + " ms" : "—" }}</div></div>
            <div class="stat"><div class="lbl">fallbackهای این نود</div><div class="num">{{ overview.proxies.fallback_count }}</div></div>
          </div>
          <p class="muted" style="font-size:13px;margin:10px 0 0">
            آخرین fallback: {{ proxyLastFallback }}
          </p>
        </div>
        <p class="muted" v-else style="font-size:13px">هیچ پراکسی‌ای ثبت نشده است.</p>
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

      <!-- Operational Status -->
      <section v-show="tab === 'ops'">
        <div class="toolbar">
          <h3 style="margin:0;flex:1;font-size:16px">وضعیت عملیاتی</h3>
        </div>
        <div class="card wide" style="margin-bottom:14px">
          <div class="stat" :class="{warn: proxyErrorCount > 10}" title="تعداد پراکسی‌هایی که وضعیت “قطع” یا “زعیف” دارند"><div class="lbl">تعداد خطاهای پراکسی</div><div class="num" :style="{color: proxyErrorCount > 10 ? 'var(--err)' : 'var(--ok,#22C55E)'}">{{ proxyErrorCount }}</div></div>
        </div>
        <div class="card wide">
          <h4>حالت پراکسی‌ها</h4>
          <div class="stat" v-for="p in proxiesList" :key="p.id" :style="p.status === 'down' ? 'color:var(--err)' : p.status === 'degraded' ? 'color:var(--warn)' : ''" :title="proxyTooltip(p)"><div class="lbl">{{ p.label || p.host }}:{{ p.port }}</div><div class="num" :style="{color: p.status === 'down' ? 'var(--err)' : p.status === 'degraded' ? 'var(--warn)' : ''}">{{ proxyStatusLabels[p.status] || p.status }}</div></div>
          <p v-if="!proxiesList.length" class="muted">پراکسی‌ای اضافه نشده</p>
          <div style="display:flex;flex-wrap:wrap;gap:8px 20px;margin-top:14px;padding-top:10px;border-top:1px dashed var(--border,#475569)">
            <div v-for="d in proxyStatusLegend" :key="d.status" style="display:flex;align-items:flex-start;gap:7px;max-width:300px;font-size:12px">
              <span :style="{ width: '10px', height: '10px', borderRadius: '50%', background: d.color, flex: 'none', marginTop: '4px' }" aria-hidden="true"></span>
              <span><b>{{ proxyStatusLabels[d.status] }}</b> <span class="muted">{{ proxyStatusDetails[d.status] }}</span></span>
            </div>
          </div>
        </div>
        <div class="card wide">
          <h4>نودهای متصل</h4>
          <div class="stat" v-for="n in nodesList" :key="n.node_id"><div class="lbl">{{ n.node_id }}</div><div class="num">{{ n.hostname || '—' }}</div></div>
          <p v-if="!nodesList.length" class="muted">نودی ثبت نشده (حالت تک‌سرور)</p>
        </div>
        <div class="card wide">
          <h4>صف انتظار</h4>
          <div class="stat" v-for="(v, k) in queueStats" :key="k"><div class="lbl">{{ k }}</div><div class="num">{{ v }}</div></div>
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
            <thead><tr><th>شناسه</th><th>برچسب</th><th>تلفن</th><th>وضعیت</th><th>آپلودها</th><th>دانلودها</th><th>حجم آپلود</th><th>هندل ۲۴س</th><th>کانال ذخیره</th><th>عملیات</th></tr></thead>
            <tbody>
              <tr v-for="a in accounts" :key="a.id">
                <td>{{ a.id }}</td><td>{{ a.label }}</td><td dir="ltr">{{ a.phone }}</td>
                <td><span class="badge" :class="a.status">{{ a.status }}</span></td>
                <td>{{ a.uploads_done }}</td><td>{{ a.downloads_done }}</td><td>{{ fmtBytes(a.bytes_up) }}</td>
                <td>{{ a.handled_24h ?? 0 }}</td>
                <td dir="ltr"><code style="font-size:11px">{{ a.storage_chat_id || 'default' }}</code></td>
                <td>
                  <button @click="accToggle(a)">{{ a.enabled ? "غیرفعال" : "فعال" }}</button>
                  <button @click="accTest(a)">تست</button>
                  <button @click="accChatOpen(a)">کانال</button>
                  <button @click="accReset(a)">ریست</button>
                  <button class="danger" @click="accDelete(a)">حذف</button>
                </td>
              </tr>
              <tr v-if="!accounts.length"><td colspan="10" class="muted">اکانتی نیست</td></tr>
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
            <thead><tr><th>شناسه</th><th>برچسب</th><th>وضعیت</th><th>هندل ۲۴س</th><th>خطا</th><th>عملیات</th></tr></thead>
            <tbody>
              <tr v-for="b in bots" :key="b.id">
                <td>{{ b.id }}</td><td>{{ b.label }}</td>
                <td><span class="badge" :class="b.status">{{ b.status }}</span></td>
                <td>{{ b.handled_24h ?? 0 }}</td>
                <td class="muted">{{ b.last_error }}</td>
                <td>
                  <button @click="botToggle(b)">{{ b.enabled ? "غیرفعال" : "فعال" }}</button>
                  <button @click="botTest(b)">تست</button>
                  <button class="danger" @click="botDelete(b)">حذف</button>
                </td>
              </tr>
              <tr v-if="!bots.length"><td colspan="6" class="muted">باتی نیست</td></tr>
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

      <!-- Channels (storage registry + backup/restore) -->
      <section v-show="tab === 'channels'">
        <div class="toolbar">
          <h3 style="margin:0;flex:1;font-size:16px">کانال‌های ذخیره‌سازی</h3>
          <button class="ghost" @click="loaders.channels()">به‌روزرسانی</button>
          <button class="primary" @click="chanDlg.open = true">کانال جدید</button>
        </div>
        <p class="muted" style="font-size:12px;margin:0 0 10px">
          کانال‌هایی که فایل‌ها در آن‌ها ذخیره می‌شوند. «تست» یک ارسال آزمایشی با اکانت‌ها/بات‌ها می‌زند؛
          «بکاپ» فهرست کامل فایل‌های این کانال را به‌صورت JSON داخل خود کانال ذخیره می‌کند (و یک نسخه هم دانلود می‌شود)؛
          «بازگردانی» از همان JSON فایل‌ها را به دیتابیس برمی‌گرداند (ادغام؛ ردیف‌های موجود حفظ می‌شوند).
        </p>
        <div class="card wide">
          <table>
            <thead><tr><th>شناسه</th><th>کانال</th><th>برچسب</th><th>نوع</th><th>وضعیت</th><th>فایل‌ها</th><th>آخرین بکاپ</th><th>عملیات</th></tr></thead>
            <tbody>
              <tr v-for="c in channels" :key="c.id">
                <td>{{ c.id }}</td>
                <td dir="ltr"><code>{{ c.chat }}</code><span v-if="c.is_system_default" class="tag" style="background:var(--ok,#22C55E);color:#08120b;font-size:10px;margin-inline-start:6px">پیش‌فرض</span></td>
                <td>{{ c.label || "—" }}</td>
                <td>{{ c.kind === "eitaa" ? "ایتا" : "تلگرام" }}</td>
                <td>
                  <span class="badge" :class="c.status">{{ c.status }}</span>
                  <span v-if="!c.enabled" class="tag" style="font-size:10px;margin-inline-start:4px">غیرفعال</span>
                  <div v-if="c.last_error" class="muted" dir="ltr" style="font-size:10px;max-width:220px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" :title="c.last_error">{{ c.last_error }}</div>
                </td>
                <td>{{ c.files || 0 }} <span class="muted" style="font-size:11px">({{ fmtBytes(c.bytes || 0) }})</span></td>
                <td class="muted" style="font-size:11px">{{ chanFmtBackup(c) }}</td>
                <td>
                  <button :disabled="chanBusy[c.id]" @click="chanTest(c)">{{ chanBusy[c.id] ? "…" : "تست" }}</button>
                  <button :disabled="chanBusy[c.id]" @click="chanBackup(c)">{{ chanBusy[c.id] ? "…" : "بکاپ" }}</button>
                  <button :disabled="chanBusy[c.id]" @click="chanRestore(c)">{{ chanBusy[c.id] ? "…" : "بازگردانی" }}</button>
                  <button @click="openChannelFiles({ chat: c.chat })">فایل‌ها</button>
                  <button @click="chanRename(c)">برچسب</button>
                  <button @click="chanToggle(c)">{{ c.enabled ? "غیرفعال" : "فعال" }}</button>
                  <button class="danger" @click="chanDelete(c)">حذف</button>
                </td>
              </tr>
              <tr v-if="defaultChannelRow && !channels.some(c => c.chat === defaultChannelRow.chat)">
                <td>۰</td>
                <td dir="ltr"><code>{{ defaultChannelRow.chat }}</code><span class="tag" style="background:var(--ok,#22C55E);color:#08120b;font-size:10px;margin-inline-start:6px">پیش‌فرض سیستم</span></td>
                <td>مقصد پیش‌فرض آپلودها</td>
                <td>تلگرام</td>
                <td><span class="badge">default</span></td>
                <td>{{ defaultChannelRow.files }} <span class="muted" style="font-size:11px">({{ fmtBytes(defaultChannelRow.bytes) }})</span></td>
                <td class="muted" style="font-size:11px">—</td>
                <td><button @click="openChannelFiles({ chat: defaultChannelRow.chat })">فایل‌ها</button></td>
              </tr>
              <tr v-if="!channels.length && !defaultChannelRow"><td colspan="8" class="muted">کانالی ثبت نشده — کانال ذخیره‌سازی (تنظیمات) یا هر کانال دیگری را همین‌جا اضافه کنید</td></tr>
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
            <thead><tr><th>شناسه</th><th>نام</th><th>پیشوند</th><th>سکوپ‌ها</th><th>ذخیره‌سازی</th><th>کانال</th><th>RPM</th><th>مصرف امروز</th><th>وضعیت</th><th>عملیات</th></tr></thead>
            <tbody>
              <tr v-for="k in keys" :key="k.id">
                <td>{{ k.id }}</td><td>{{ k.name }}</td>
                <td dir="ltr"><code>{{ k.key_prefix }}...</code></td>
                <td>{{ k.scopes }}</td><td>{{ k.backend || "پیش‌فرض" }}</td>
                <td dir="ltr">
                  <template v-if="!k.revoked">
                    <code v-if="k.storage_chat" style="font-size:11px">{{ k.storage_chat }}</code>
                    <button v-else class="ghost" style="padding:2px 8px;font-size:11px" @click="keyEditChat(k)">+ کانال</button>
                  </template>
                  <span v-else class="muted">—</span>
                </td>
                <td>{{ k.rpm }}</td><td>{{ fmtBytes(k.used_bytes_today) }}</td>
                <td><span class="badge" :class="k.revoked ? 'revoked' : 'ready'">{{ k.revoked ? "revoked" : "active" }}</span></td>
                <td>
                  <button v-if="!k.revoked" @click="keyEditChat(k)">کانال</button>
                  <button v-if="!k.revoked" class="danger" @click="keyRevoke(k)">Revoke</button>
                </td>
              </tr>
              <tr v-if="!keys.length"><td colspan="10" class="muted">کلیدی نیست</td></tr>
            </tbody>
          </table>
        </div>
      </section>

      <!-- Files -->
      <section v-show="tab === 'files'" style="position:relative" @dragover.prevent="filesDragDepth > 0 || (filesDragDepth = 1)" @dragenter.prevent="filesDragDepth++" @dragleave.prevent="filesDragDepth = Math.max(0, filesDragDepth - 1)" @drop.prevent="onFilesDrop">
        <div class="toolbar">
          <h3 style="margin:0;flex:1;font-size:16px">فایل‌ها</h3>
          <span v-if="transferJobs.length" id="transfer-live" class="tag" style="display:inline-flex;align-items:center;gap:6px;font-size:11px;padding:3px 10px" :title="transferJobs.map(j => (j.file_name || j.file_id) + ' → ' + (j.target_chat || '?') + ' (' + j.pct + '%)').join('\n')">
            <span style="width:8px;height:8px;border-radius:50%;background:var(--warn,#F59E0B);display:inline-block" :style="transferRunning ? 'animation: pulse 1.2s ease-in-out infinite' : ''"></span>
            انتقال: {{ transferJobs.length }} • {{ transferJobs.reduce((m, j) => Math.max(m, j.pct), 0) }}%
          </span>
          <button class="primary" :disabled="uploadDlg.busy" @click="uploadPick">
            {{ uploadDlg.busy ? "در حال آپلود…" : "آپلود جدید" }}
          </button>
          <button class="ghost" @click="loaders.channels(); switchTab('files')">به‌روزرسانی</button>
        </div>
        <div v-if="filesDragDepth > 0 && !uploadDlg.open" style="position:absolute;inset:0;background:rgba(59,130,246,.12);border:2px dashed var(--accent,#3b82f6);z-index:30;display:flex;align-items:center;justify-content:center;pointer-events:none">
          <b style="background:var(--panel,#141822);padding:10px 18px;border-radius:10px;border:1px solid var(--accent,#3b82f6)">فایل‌ها را رها کنید تا دیالوگ آپلود باز شود</b>
        </div>
        <div v-if="channelFilter" style="margin-bottom:10px;display:flex;align-items:center;gap:8px">
          <span class="tag" style="font-size:12px">کانال: <b dir="ltr">{{ channelFilter }}</b></span>
          <span v-if="channelFilterIsDefault" class="tag" style="font-size:11px">پیش‌فرض سیستم (فایل‌های بدون کانال هم اینجا هستند)</span>
          <button class="ghost" style="padding:2px 10px;font-size:11px" @click="clearChannelFilter">حذف فیلتر ×</button>
        </div>
        <div v-if="channelFilter" class="card wide" style="margin-bottom:14px;border-color:var(--border,#2a2f3a)">
          <h3 style="font-size:15px;margin:0 0 10px">آمار کانال {{ channelFilter }}</h3>
          <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:8px">
            <div class="stat" style="background:var(--panel2)">
              <div class="lbl" style="font-size:12px">تعداد فایل</div>
              <div class="num" style="font-size:24px">{{ filesInChannelTotal }}</div>
            </div>
            <div class="stat" style="background:var(--panel2)">
              <div class="lbl" style="font-size:12px">حجم کلی</div>
              <div class="num" style="font-size:18px">{{ fmtBytes(filesInChannelBytes) }}</div>
            </div>
            <div v-if="filesInChannelByType && filesInChannelByType.length > 0" class="stat" style="background:var(--panel2)">
              <div class="lbl" style="font-size:12px">نوع اصلی</div>
              <div class="muted" style="font-size:11px">{{ filesInChannelByType[0].key }}: {{ filesInChannelByType[0].count }} فایل</div>
            </div>
          </div>
        </div>
        <div v-if="bulkMode" style="margin-bottom:10px;display:flex;align-items:center;gap:8px">
          <input type="checkbox" id="select-all-files" v-model="selectAllFiles" @change="toggleSelectAll" class="checkbox" dir="ltr">
          <label for="select-all-files" class="muted" style="font-size:12px;cursor:pointer">انتخاب همه {{ files.length }}</label>
          <span v-if="selectedFiles.size > 0" class="muted" style="font-size:12px;color:var(--ok)">{{ selectedFiles.size }} فایل انتخاب شده</span>
        </div>
        <div v-else style="margin-bottom:10px;display:flex;align-items:center;gap:8px;flex-wrap:wrap">
          <input v-if="currentFolderId !== null" v-model="folderQuery" dir="auto" placeholder="جستجو در این پوشه..." style="max-width:220px;padding:5px 10px;font-size:13px" @input="folderSearch">
          <input v-else v-model="filesQuery" dir="auto" placeholder="جستجوی نام فایل..." style="max-width:220px;padding:5px 10px;font-size:13px" @input="filesSearch">
          <select v-model="filesMime" style="padding:5px 8px;font-size:13px" @change="loaders.files()">
            <option value="">همه انواع</option>
            <option value="image/">تصویر</option>
            <option value="video/">ویدیو</option>
            <option value="audio/">صوت</option>
            <option value="text/">متن/کد</option>
            <option value="application/pdf">PDF</option>
            <option value="application/zip">آرشیو</option>
          </select>
          <select v-model="filesOrder" style="padding:5px 8px;font-size:13px" @change="loaders.files()">
            <option value="date">جدیدترین</option>
            <option value="name">نام</option>
            <option value="size">حجم</option>
            <option value="downloads">بیشترین دانلود</option>
          </select>
          <button class="ghost" :style="filesBlockedOnly ? 'border-color:var(--err);color:var(--err)' : ''" style="padding:5px 10px;font-size:12px" @click="filesBlockedOnly = !filesBlockedOnly; loaders.files()">فقط بن‌شده‌ها</button>
          <button class="ghost" :style="filesTrashed ? 'border-color:var(--warn);color:var(--warn)' : ''" style="padding:5px 10px;font-size:12px" @click="filesTrashed = !filesTrashed; filesOffset = 0; loaders.files()">🗑 زباله‌دان</button>
          <button class="ghost" :style="bulkMode ? 'border-color:var(--accent);color:var(--accent)' : ''" style="padding:5px 10px;font-size:12px" @click="toggleBulkMode">انتخاب گروهی</button>
          <select v-model="filesChannelPick" style="padding:5px 8px;font-size:13px" @change="applyChannelPick">
            <option value="">همه کانال‌ها ({{ filesTotal }})</option>
            <option v-if="channelsDefaultChat" value="__default__">کانال پیش‌فرض ({{ channelsDefaultChat }}) — {{ channelDefaultStats.files }}</option>
            <option v-for="c in channels" :key="c.id" :value="c.chat">{{ c.label ? c.label + " — " : "" }}{{ c.chat }} — {{ c.files || 0 }}</option>
          </select>
          <span class="muted" style="font-size:12px">{{ filesTotal }} فایل</span>
        </div>
        <div v-if="bulkMode" style="margin-bottom:10px;display:flex;gap:8px;align-items:center;flex-wrap:wrap">
          <button class="ghost" @click="bulkMode = false; selectedFiles.value.clear(); selectAllFiles = false" style="padding:5px 10px;font-size:12px">انصراف از حالت گروهی</button>
          <button class="danger" @click="bulkDeleteSelected" style="padding:5px 10px;font-size:12px;background:var(--err);color:#fff">حذف گروهی ({{ selectedFiles.size }})</button>
          <button class="ghost" @click="bulkBlockSelected" style="padding:5px 10px;font-size:12px">بن گروهی ({{ selectedFiles.size }})</button>
          <button class="ghost" @click="bulkMoveSelectedDlg.open = true" style="padding:5px 10px;font-size:12px">انتصال گروهی به پوشه...</button>
          <button class="primary" @click="bulkTransferOpen" style="padding:5px 10px;font-size:12px">انتقال گروهی به کانال… ({{ selectedFiles.size }})</button>
          <button class="ghost" :disabled="bulkZipBusy" @click="bulkZipDownload" style="padding:5px 10px;font-size:12px">{{ bulkZipBusy ? "در حال آماده‌سازی…" : "دانلود گروهی (zip)" }}</button>
        </div>
        <div style="display:flex;gap:14px;align-items:flex-start">
          <div class="card" style="width:230px;flex-shrink:0;padding:10px 12px">
            <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:6px">
              <b style="font-size:13px">پوشه‌ها</b>
              <button class="ghost" style="padding:2px 8px;font-size:11px" @click="folderDlg.open = true">+ پوشه</button>
            </div>
            <div style="max-height:320px;overflow:auto">
              <div style="padding:4px 6px;border-radius:6px;cursor:pointer;" :style="sbRowStyle(null)" @click="openFolder(null)" @dragover.prevent="onSidebarDragOver(null)" @dragenter.prevent="onSidebarDragOver(null)" @dragleave="onSidebarDragLeave(null)" @drop.prevent="onSidebarDrop($event, null)" title="رها کردن فایل برای آپلود در ریشه">همه فایل‌ها</div>
              <div v-for="f in foldersFlat" :key="f.id"
                style="padding:4px 6px;border-radius:6px;cursor:pointer;display:flex;justify-content:space-between;gap:6px"
                :style="sbRowStyle(f.id)"
                @click="openFolder(f.id)"
                @dragover.prevent="onSidebarDragOver(f.id)" @dragenter.prevent="onSidebarDragOver(f.id)" @dragleave="onSidebarDragLeave(f.id)" @drop.prevent="onSidebarDrop($event, f)"
                :title="'مسیر: ' + f.path + (f.scope ? ' — کانال: ' + f.scope : '') + ' — فایل را اینجا رها کنید تا در همین پوشه آپلود شود'">
                <span style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap">{{ '\u00A0'.repeat(f.path.split('/').length - 1) + f.name }}<span v-if="f.scope" class="tag" style="font-size:9px;margin-inline-start:4px;background:var(--panel2)">chan</span></span>
                <span class="muted" style="font-size:11px">{{ f.file_count }}</span>
                <span style="display:flex;gap:2px">
                  <a href="#" style="font-size:10px;text-decoration:none" title="تغییر نام" @click.prevent.stop="folderRename(f)">✏️</a>
                  <a href="#" style="font-size:10px;text-decoration:none" title="انتقال" @click.prevent.stop="folderMoveDlg(f)">📁</a>
                  <a href="#" style="font-size:10px;text-decoration:none" title="حذف" @click.prevent.stop="folderDelete(f)">🗑</a>
                </span>
              </div>
              <div v-if="!foldersFlat.length" class="muted" style="font-size:12px;padding:4px 6px">پوشه‌ای نیست</div>
            </div>
          </div>
          <div style="flex:1;min-width:0">
            <div class="muted" style="font-size:12px;margin-bottom:8px;display:flex;align-items:center;gap:8px">
              <span dir="ltr">{{ currentFolderId === null ? '/' : (currentFolderPath || '/') }}</span>
              <input v-model="filesFolderDraft" dir="ltr" placeholder="پوشه برای آپلود (مثل projects/2026)" style="font-size:12px;padding:3px 8px;width:230px">
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
            <thead><tr><th>شناسه</th><th>نام</th><th>حجم</th><th>وضعیت</th><th>ذخیره</th><th>کانال</th><th>دانلود</th><th>تاریخ</th><th>عملیات</th></tr></thead>
            <tbody>
              <tr v-for="f in files" :key="f.id">
                <td><input type="checkbox" :checked="selectedFiles.has(f.id)" @change="toggleSelectFile(f.id)" class="checkbox" dir="ltr" v-if="!bulkMode"></td>
                <td v-if="!bulkMode" dir="ltr"><code>{{ f.id }}</code></td>
                <td v-else dir="ltr"><code style="color:var(--muted)">{{ f.id }}</code></td>
                <td v-if="!bulkMode"><a href="#" style="color:inherit;text-decoration:underline dotted" @click.prevent="filePreview(f)" :title="f.mime">{{ f.name }}</a><span v-if="f.blocked" class="tag" style="background:var(--err);color:#fff;font-size:10px;margin-inline-start:6px">بن</span></td>
                <td v-else><span class="muted">{{ f.name }}</span></td>
                <td>{{ fmtBytes(f.size) }}</td>
                <td><span class="badge" :class="f.status">{{ f.status }}</span></td>
                <td>{{ f.backend || "tg" }}</td>
                <td dir="ltr"><code style="font-size:11px">{{ f.storage_chat || 'default' }}</code></td>
                <td>{{ f.downloads }}</td>
                <td class="muted">{{ fmtTime(f.created_at) }}</td>
                <td v-if="!bulkMode">
                  <button @click="filePreview(f)">پیش‌نمایش</button>
                  <button @click="fileLink(f)">لینک</button>
                  <button @click="fileLinksDlg(f)">لینک‌ها</button>
                  <button @click="fileMoveDlg(f)">پوشه</button>
                  <button v-if="f.blocked" @click="fileBlock(f, false)">رفع بن</button>
                  <button v-else class="danger" @click="fileBlock(f, true)">بن</button>
                  <button v-if="f.deleted_at || filesTrashed" @click="fileRestore(f)">بازیابی</button>
                  <button v-if="filesTrashed" class="danger" @click="fileDelete(f)">حذف نهایی</button>
                  <button v-else class="danger" @click="fileDelete(f)">حذف</button>
                </td>
                <td v-if="bulkMode"><input type="checkbox" :checked="selectedFiles.has(f.id)" @change="toggleSelectFile(f.id)" class="checkbox" dir="ltr"></td>
              </tr>
              <tr v-if="!files.length"><td colspan="9" class="muted">فایلی نیست</td></tr>
            </tbody>
          </table>
        </div>
        <div style="display:flex;gap:8px;align-items:center;justify-content:center;margin-top:10px">
          <button class="ghost" style="padding:4px 10px;font-size:12px" :disabled="filesOffset === 0" @click="filesOffset = Math.max(0, filesOffset - filesLimit); loaders.files()">قبلی</button>
          <span class="muted" style="font-size:12px">صفحه {{ Math.floor(filesOffset / filesLimit) + 1 }} از {{ Math.max(1, Math.ceil(filesTotal / filesLimit)) }}</span>
          <button class="ghost" style="padding:4px 10px;font-size:12px" :disabled="filesOffset + filesLimit >= filesTotal" @click="filesOffset += filesLimit; loaders.files()">بعدی</button>
        </div>
          </div>
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
        <!-- upload-pressure status: stall behind the concurrency gate + flooded backends -->
        <div v-if="pressure.stall && (pressure.stall.waiting > 0 || pressure.stall.stalled)" class="card wide pressure-card">
          <div class="pt-row">
            <span class="badge" :class="pressure.stall.stalled ? 'failed' : 'pending'">{{ pressure.stall.stalled ? "معطلی — هشدار داده شد" : "در انتظار" }}</span>
            <b>{{ pressure.stall.waiting }} جاب آپلود/انتقال پشت سقف همزمانی</b>
            <span class="muted">قدیمی‌ترین {{ Math.round(pressure.stall.oldest_waiting_s) }} ثانیه؛ آستانه هشدار {{ pressure.stall.threshold }} ثانیه؛ سقف {{ pressure.stall.gate_capacity }}؛ ورکر آپلود {{ pressure.stall.upload_workers }}</span>
          </div>
          <div v-if="pressure.flooded.length" class="pt-row">
            <span class="badge degraded">فشار تلگرام</span>
            <b>بک‌اندهای موقتاً خارج از چرخش:</b>
            <span v-for="f in pressure.flooded" :key="f.key" class="badge" :class="f.burst_alerted ? 'failed' : 'degraded'" dir="ltr">{{ f.key }} — {{ f.remaining_s }}s{{ f.burst_alerted ? " ⚠" : "" }}</span>
          </div>
        </div>
        <div class="cards">
          <div class="stat" v-for="(v, k) in queueStats" :key="k"><div class="lbl">{{ k }}</div><div class="num">{{ v }}</div></div>
        </div>
        <div class="card wide">
          <table>
            <thead><tr><th>شناسه</th><th>نوع</th><th>اولویت</th><th>وضعیت</th><th>تلاش</th><th>خطا</th><th>عملیات</th></tr></thead>
            <tbody>
              <tr v-for="j in jobs" :key="j.id">
                <td dir="ltr"><code>{{ j.id }}</code></td><td>{{ j.kind }}</td><td>{{ j.priority }}</td>
                <td><span class="badge" :class="j.status">{{ j.status }}</span></td>
                <td>{{ j.attempts }}</td><td class="muted">{{ j.error }}</td>
                <td>
                  <button v-if="j.status === 'failed'" @click="qRetry(j)">تلاش مجدد</button>
                  <template v-else-if="j.kind === 'upload' && !j.paused && (j.status === 'pending' || j.status === 'running' || j.status === 'retry')">
                    <button @click="qResumeJob(j)">Continue from offset {{ j.resumed_offset || 0 }}</button>
                  </template>
                  <template v-else><span class="muted" style="font-size:12px">—</span></template>
                </td>
              </tr>
              <tr v-if="!jobs.length"><td colspan="7" class="muted">جابی نیست</td></tr>
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
        <div v-if="blockedFilesTotal > 0" class="card wide" style="margin-bottom:14px">
          <h3 style="font-size:15px;margin:0 0 10px">گزارش فایل‌های بن‌شده</h3>
          <p class="muted" style="font-size:12px;margin:0 0 8px">کل {{ blockedFilesTotal }} فایل</p>
          <table>
            <thead><tr><th>زمان</th><th>فایل</th><th>آپلودکننده</th><th>عملیات</th><th>IP</th></tr></thead>
            <tbody>
              <tr v-for="(f, i) in blockedFilesReport" :key="i">
                <td class="muted">{{ fmtTime(f.timestamp) }}</td>
                <td dir="ltr">{{ f.file_name || 'UNKNOWN' }}</td>
                <td>{{ f.uploader || '—' }}</td>
                <td>{{ f.action }}</td>
                <td dir="ltr">{{ f.ip }}</td>
              </tr>
              <tr v-if="!blockedFilesReport.length"><td colspan="5" class="muted">هیچ فایل‌های بن‌شده‌ای نمی‌باشد</td></tr>
            </tbody>
          </table>
        </div>
      </section>

      <!-- Proxies tab -->
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
              <select v-else-if="it.type === 'int' && optionedSettings.includes(it.key)" v-model="settingsDraft[it.key]"
                :style="settingsErrors[it.key] ? 'border-color:var(--err)' : ''"
                @change="onSettingInput(it)">
                <template v-if="it.key === 'fake_tg'">
                  <option value="0">غیرفعال — اتصال واقعی به تلگرام</option>
                  <option value="1">فعال — تلگرام آزمایشی (بدون شبکه، برای تست)</option>
                </template>
                <template v-else-if="it.key === 'eitaa_mode'">
                  <option value="0">تلگرام (پیش‌فرض)</option>
                  <option value="1">ایتا</option>
                </template>
                <template v-else-if="it.key === 'transfer_delete_source'">
                  <option value="0">خاموش — نسخه‌های منبع در کانال قبلی بمانند</option>
                  <option value="1">روشن — پس از انتقال موفق، پیام‌های منبع حذف شوند</option>
                </template>
              </select>
              <input v-else-if="it.key === 'tg_api_hash'" v-model="settingsDraft[it.key]" type="password"
                dir="ltr" autocomplete="off" placeholder="32 کاراکتر هگز از my.telegram.org"
                :class="{ invalid: settingsErrors[it.key] }"
                :style="settingsErrors[it.key] ? 'border-color:var(--err)' : ''"
                @input="onSettingInput(it)">
              <input v-else-if="it.key === 'tg_storage_chat'" v-model="settingsDraft[it.key]"
                dir="ltr" autocomplete="off" placeholder="@username یا -100… (خالی = Saved Messages هر اکانت)"
                :class="{ invalid: settingsErrors[it.key] }"
                :style="settingsErrors[it.key] ? 'border-color:var(--err)' : ''"
                @input="onSettingInput(it)">
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
          <h3 style="font-size:15px;margin:0 0 10px">کانال‌های ذخیره‌سازی</h3>
          <p class="muted" style="font-size:12px;margin:0 0 10px">هر کلید می‌تواند کانال اختصاصی داشته باشد؛ فایل‌های کلید بدون کانال در کانال پیش‌فرض سیستم ذخیره می‌شوند.</p>
          <button class="ghost" style="padding:4px 10px;font-size:12px" :disabled="storageChannelsBusy" @click="loadStorageChannels">{{ storageChannelsBusy ? '...' : 'بارگذاری/به‌روزرسانی' }}</button>
          <div v-if="storageChannelsLoaded" style="margin-top:10px">
            <div v-for="c in storageChannels" :key="c.chat || '(default)'" style="border:1px solid var(--border,#2a2f3a);border-radius:8px;padding:10px 12px;margin-bottom:10px">
              <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap">
                <b dir="ltr" style="font-size:13px;cursor:pointer;text-decoration:underline dotted" title="مدیریت فایل‌های این کانال" @click="openChannelFiles(c)">{{ c.chat || '(بدون کانال)' }}</b>
                <span v-if="c.is_system_default" class="tag" style="background:var(--ok,#22C55E);color:#08120b">پیش‌فرض سیستم</span>
                <span class="tag" style="font-size:11px">{{ c.kind === 'default' ? 'مقصد کلیدهای بدون کانال' : 'اختصاصی' }}</span>
                <span class="muted" style="font-size:12px">{{ c.files }} فایل • {{ fmtBytes(c.bytes) }}</span>
              </div>
              <div class="muted" style="font-size:12px;margin-top:4px" v-if="c.keys.length">
                کلیدها:
                <code v-for="k in c.keys" :key="k.id" style="margin-inline-end:6px">{{ k.name }}{{ k.revoked ? ' (لغوشده)' : '' }}</code>
              </div>
              <div style="margin-top:6px;display:flex;gap:6px;flex-wrap:wrap">
                <span v-for="t in c.by_type" :key="t.key" class="tag" style="font-size:11px">{{ t.label }}: {{ t.count }} ({{ fmtBytes(t.bytes) }})</span>
              </div>
              <div class="muted" style="font-size:12px;margin-top:6px" v-if="!c.by_type.length">هنوز فایل آماده‌ای ندارد</div>
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
    </main>

    <!-- Dialogs -->
    <dialog :open="accDlg.open" @close="accClose">
      <h3>افزودن اکانت تلگرام</h3>
      <template v-if="accDlg.step === 1">
        <div class="field"><label>تلفن</label><input v-model="accDlg.phone" type="text" placeholder="+98912..." :disabled="accDlg.busy"></div>
        <div class="field"><label>برچسب (اختیاری)</label><input v-model="accDlg.label" type="text" :disabled="accDlg.busy"></div>
        <button class="primary" style="width:100%" :disabled="accDlg.busy" @click="accSend">
          <span v-if="accDlg.busy" class="spinner" aria-hidden="true"></span>
          {{ accDlg.busy ? "در حال ارسال کد…" : "ارسال کد تایید" }}
        </button>
      </template>
      <template v-else-if="accDlg.step === 2">
        <div class="field"><label>کد تایید تلگرام</label><input v-model="accDlg.code" type="text" placeholder="12345" inputmode="numeric" :disabled="accDlg.busy" @keydown.enter="accDone"></div>
        <p class="muted" style="margin:0">
          <span v-if="accDlg.resendIn > 0">ارسال مجدد کد تا {{ accTimerFmt }} دیگر</span>
          <span v-else-if="accDlg.resendsLeft <= 0">حداکثر تعداد ارسال مجدد استفاده شد</span>
          <template v-else>
            <a href="#" @click.prevent="accResend">ارسال مجدد کد</a>
            <span v-if="accDlg.resendsLeft < 3"> ({{ accDlg.resendsLeft }} باقی‌مانده)</span>
          </template>
        </p>
        <button class="primary" style="width:100%" :disabled="accDlg.busy || !accDlg.code.trim()" @click="accDone">
          <span v-if="accDlg.busy" class="spinner" aria-hidden="true"></span>
          {{ accDlg.busy ? "در حال بررسی کد…" : "تایید کد" }}
        </button>
      </template>
      <template v-else>
        <p class="muted" style="margin:0">این حساب ورود دو مرحله‌ای دارد — رمز تلگرام را وارد کنید ({{ accDlg.pwLeft }} تلاش باقی مانده). فیلد کد قفل شد.</p>
        <div class="field"><label>رمز 2FA</label><input v-model="accDlg.pass" type="password" :disabled="accDlg.busy" @keydown.enter="accDone"></div>
        <button class="primary" style="width:100%" :disabled="accDlg.busy || !accDlg.pass" @click="accDone">
          <span v-if="accDlg.busy" class="spinner" aria-hidden="true"></span>
          {{ accDlg.busy ? "در حال بررسی رمز…" : "تایید نهایی ورود" }}
        </button>
      </template>
      <p class="err">{{ accDlg.msg }}</p>
      <button class="ghost" style="width:100%" :disabled="accDlg.busy" @click="accClose">انصراف</button>
    </dialog>

    <dialog :open="accChatDlg.open">
      <h3>کانال ذخیره‌سازی اکانت</h3>
      <p class="muted" style="margin:0">مقصد آپلودهای این اکانت. «پیش‌فرض سیستم» یعنی همان کانالی که در تب تنظیمات تعیین شده.</p>
      <div class="field"><label>اکانت</label><input type="text" :value="accChatDlg.label" disabled dir="ltr"></div>
      <div class="field">
        <label>کانال</label>
        <select v-model="accChatDlg.chat" :disabled="accChatDlg.busy">
          <option v-for="o in accChatOptions" :key="o.value" :value="o.value">{{ o.label }}</option>
        </select>
      </div>
      <p class="err">{{ accChatDlg.msg }}</p>
      <button class="primary" style="width:100%" :disabled="accChatDlg.busy" @click="accChatSave">
        <span v-if="accChatDlg.busy" class="spinner" aria-hidden="true"></span>
        {{ accChatDlg.busy ? "در حال ذخیره…" : "ذخیره" }}
      </button>
      <button class="ghost" style="width:100%" :disabled="accChatDlg.busy" @click="accChatDlg.open = false">انصراف</button>
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
      <div class="field"><label>توکن ایتا</label><input v-model="eitDlg.token" type="password" autocomplete="off"></div>
      <div class="field"><label>کانال مقصد</label><input v-model="eitDlg.chat" type="text" dir="ltr" autocomplete="off" placeholder="123456789"></div>
      <div class="field"><label>برچسب (اختیاری)</label><input v-model="eitDlg.label" type="text"></div>
      <button class="primary" style="width:100%" @click="eitSave">ذخیره</button>
      <p class="err">{{ eitDlg.msg }}</p>
      <button class="ghost" style="width:100%" @click="eitDlg.open = false">انصراف</button>
    </dialog>

    <dialog :open="chanDlg.open" @close="chanDlg.open = false">
      <h3>افزودن کانال</h3>
      <div class="field"><label>کانال (@username یا شناسه عددی)</label><input v-model="chanDlg.chat" type="text" dir="ltr" autocomplete="off" placeholder="@my_channel یا -1001234567890"></div>
      <div class="field"><label>برچسب (اختیاری)</label><input v-model="chanDlg.label" type="text" placeholder="مثلاً کانال آرشیو"></div>
      <div class="field"><label>نوع</label>
        <select v-model="chanDlg.kind">
          <option value="storage">کانال ذخیره‌سازی تلگرام</option>
          <option value="eitaa">کانال ایتا (فقط ارسال)</option>
        </select>
      </div>
      <button class="primary" style="width:100%" @click="chanSave">ذخیره</button>
      <p class="err">{{ chanDlg.msg }}</p>
      <button class="ghost" style="width:100%" @click="chanDlg.open = false">انصراف</button>
    </dialog>

    <dialog :open="previewDlg.open" @close="previewDlg.open = false" style="max-width:860px;width:calc(100vw - 40px)">
      <h3 style="margin-top:0">{{ previewDlg.name }}</h3>
      <p class="muted" style="font-size:12px;margin:0 0 8px" dir="ltr">{{ previewDlg.mime }} · {{ fmtBytes(previewDlg.size) }}</p>
      <div style="text-align:center;background:#0b1120;border-radius:8px;padding:8px;min-height:120px">
        <img v-if="previewDlg.kind === 'image'" :src="previewDlg.url" style="max-width:100%;max-height:60vh;border-radius:6px">
        <video v-else-if="previewDlg.kind === 'video'" :src="previewDlg.url" controls autoplay style="max-width:100%;max-height:60vh"></video>
        <audio v-else-if="previewDlg.kind === 'audio'" :src="previewDlg.url" controls style="width:100%"></audio>
        <iframe v-else-if="previewDlg.kind === 'pdf'" :src="previewDlg.url" style="width:100%;height:60vh;border:0;border-radius:6px"></iframe>
        <pre v-else-if="previewDlg.kind === 'text'" style="text-align:start;max-height:60vh;overflow:auto;font-size:12px;color:#e2e8f0"><code>{{ previewDlg.text }}</code></pre>
        <p v-else class="muted">پیش‌نمایش برای این نوع فایل موجود نیست</p>
      </div>
      <div style="display:flex;gap:8px;margin-top:10px">
        <button class="primary" style="flex:1" @click="fileDownload(previewDlg)">دانلود</button>
        <button class="ghost" style="flex:1" @click="previewDlg.open = false">بستن</button>
      </div>
    </dialog>

    <dialog :open="linksDlg.open" @close="linksDlg.open = false">
      <h3>لینک‌های اشتراک — {{ linksDlg.name }}</h3>
      <div v-if="linksDlg.items.length" style="max-height:300px;overflow:auto">
        <div v-for="l in linksDlg.items" :key="l.id" style="display:flex;align-items:center;gap:8px;padding:6px 0;border-bottom:1px solid var(--border,#2a2f3a)">
          <code dir="ltr" style="font-size:11px;flex:1;overflow:hidden;text-overflow:ellipsis">/{{ l.slug }}</code>
          <span class="muted" style="font-size:11px">{{ l.hits }}/{{ l.max_downloads || '∞' }}</span>
          <span v-if="l.disabled" class="tag" style="font-size:10px;background:var(--err);color:#fff">غیرفعال</span>
          <button class="ghost" style="padding:2px 8px;font-size:11px" @click="linkCopy(l)">کپی</button>
          <button class="ghost" style="padding:2px 8px;font-size:11px" @click="linkToggle(l)">{{ l.disabled ? 'فعال' : 'غیرفعال' }}</button>
          <button class="danger" style="padding:2px 8px;font-size:11px" @click="linkDelete(l)">حذف</button>
        </div>
      </div>
      <p v-else class="muted" style="font-size:13px">برای این فایل لینک عمومی‌ای ساخته نشده — از دکمه «لینک» یکی بساز.</p>
      <button class="ghost" style="width:100%;margin-top:10px" @click="linksDlg.open = false">بستن</button>
    </dialog>

    <dialog :open="folderDlg.open" @close="folderDlg.open = false">
      <h3>پوشه جدید</h3>
      <div class="field"><label>مسیر (برای تو در تو از / استفاده کن، مثل projects/2026)</label>
        <input v-model="folderDlg.path" dir="ltr" placeholder="projects/2026/reports"></div>
      <div class="field">
        <label>مختص کانال</label>
        <select v-model="folderDlg.scope" style="width:100%">
          <option v-for="o in folderScopeOptions" :key="o.value" :value="o.value">{{ o.label }}</option>
        </select>
        <p class="muted" style="font-size:11px;margin:4px 0 0">هر کانال می‌تواند پوشه‌بندی مستقل خودش را داشته باشد؛ پوشه‌های عمومی همه‌جا دیده می‌شوند.</p>
      </div>
      <button class="primary" style="width:100%" @click="folderCreate">ایجاد</button>
      <p class="err">{{ folderDlg.msg }}</p>
      <button class="ghost" style="width:100%" @click="folderDlg.open = false">انصراف</button>
    </dialog>

    <dialog :open="moveDlg.open" @close="moveDlg.open = false">
      <h3>انتقال فایل به پوشه</h3>
      <p class="muted" style="font-size:12px">{{ moveDlg.fileName }}</p>
      <div class="field"><label>مسیر پوشه مقصد (خالی = بدون پوشه)</label>
        <input v-model="moveDlg.path" dir="ltr" placeholder="projects/2026"></div>
      <button class="primary" style="width:100%" @click="moveFileDo">انتقال</button>
      <p class="err">{{ moveDlg.msg }}</p>
      <button class="ghost" style="width:100%" @click="moveDlg.open = false">انصراف</button>
    </dialog>

    <dialog :open="bulkMoveSelectedDlg.open" @close="bulkMoveSelectedDlg.open = false">
      <h3>انتقال گروهی به پوشه</h3>
      <p class="muted" style="font-size:12px">{{ selectedFiles.size }} فایل انتخاب شده</p>
      <div class="field"><label>مسیر پوشه مقصد (خالی = بدون پوشه)</label>
        <input v-model="bulkMoveSelectedDlg.path" dir="ltr" placeholder="projects/2026"></div>
      <button class="primary" style="width:100%" :disabled="bulkMoveSelectedDlg.busy" @click="bulkMoveSelectedDo">انتقال گروهی</button>
      <p class="err">{{ bulkMoveSelectedDlg.msg }}</p>
      <button class="ghost" style="width:100%" @click="bulkMoveSelectedDlg.open = false">انصراف</button>
    </dialog>

    <dialog :open="bulkTransferDlg.open" @close="bulkTransferDlg.open = false">
      <h3>انتقال گروهی به کانال</h3>
      <p class="muted" style="font-size:12px">{{ selectedFiles.size }} فایل انتخاب شده — محتوا از کانال فعلی به مقصد منتقل می‌شود (در صف).</p>
      <div class="field">
        <label>کانال مقصد</label>
        <select v-model="bulkTransferDlg.chat" style="width:100%">
          <option v-for="o in bulkTransferChatOptions" :key="o.value" :value="o.value">{{ o.label }}</option>
        </select>
      </div>
      <div class="field">
        <label>پوشه مقصد (اختیاری)</label>
        <select v-model="bulkTransferDlg.folderId" style="width:100%">
          <option value="">بدون تغییر</option>
          <option value="0">بدون پوشه (ریشه)</option>
          <option v-for="f in foldersFlat" :key="f.id" :value="String(f.id)">{{ f.path }}</option>
        </select>
      </div>
      <p class="err">{{ bulkTransferDlg.msg }}</p>
      <button class="primary" style="width:100%" :disabled="bulkTransferDlg.busy || !bulkTransferDlg.chat" @click="bulkTransferDo">
        <span v-if="bulkTransferDlg.busy" class="spinner" aria-hidden="true"></span>
        {{ bulkTransferDlg.busy ? "در حال ارسال به صف…" : "انتقال" }}
      </button>
      <button class="ghost" style="width:100%" :disabled="bulkTransferDlg.busy" @click="bulkTransferDlg.open = false">انصراف</button>
    </dialog>

    <dialog :open="keyDlg.open" @close="keyDlg.open = false">
      <h3>ایجاد کلید API</h3>
      <div class="field"><label>نام</label><input v-model="keyDlg.name" type="text"></div>
      <div class="field"><label>سکوپ‌ها (با ویرگول)</label><input v-model="keyDlg.scopes" type="text" dir="ltr" placeholder="read,write,admin"></div>
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
      <div class="field" v-if="keyDlg.backend !== 'eitaa'">
        <label>کانال ذخیره‌سازی اختصاصی <span class="muted" style="font-size:11px">(اختیاری)</span></label>
        <select v-model="keyDlg.storageChat" style="width:100%">
          <option value="">پیش‌فرض سیستم (بدون کانال اختصاصی)</option>
          <option v-for="o in keyChatOptions" :key="o.value" :value="o.value">{{ o.label }}</option>
        </select>
        <p class="muted" style="font-size:11px;margin:4px 0 0">فایل‌های آپلودشده با این کلید به این کانال می‌روند.</p>
      </div>
      <button class="primary" style="width:100%" @click="keySave">ایجاد</button>
      <div v-if="keyDlg.result !== ''" style="background:var(--panel2);padding:12px;border-radius:8px">
        <p class="muted" style="font-size:12px;margin:0 0 6px">کلید (فقط همین یک بار نمایش داده می‌شود):</p>
        <code style="word-break:break-all">{{ keyDlg.result }}</code>
        <button style="margin-top:8px;width:100%" @click="copyKey">کپی</button>
      </div>
      <button class="ghost" style="width:100%" @click="keyDlg.open = false">انصراف</button>
    </dialog>

    <dialog :open="uploadDlg.open">
      <h3>آپلود فایل</h3>
      <div class="field">
        <label>فایل</label>
        <input type="file" multiple @change="onUploadFileChosen">
      </div>
      <p v-if="uploadDlg.files.length" class="muted" style="margin:0" dir="ltr">{{ uploadDlg.files.length > 1 ? uploadDlg.files.length + " فایل — " : "" }}{{ uploadDlg.files[0].name }}{{ uploadDlg.files.length > 1 ? " …" : "" }} — {{ fmtBytes(uploadDlg.files.reduce((s, f) => s + (f.size || 0), 0)) }}</p>
      <p class="muted" style="margin:0">نام فایل همان‌طور که هست ذخیره می‌شود (بدون تغییر نام).</p>
      <div class="field">
        <label>پوشه مقصد (اختیاری، مثل projects/2026)</label>
        <input v-model="uploadDlg.folder" dir="ltr" placeholder="از پوشه‌های همین کانال استفاده می‌شود">
      </div>
      <div class="field">
        <label>کانال مقصد</label>
        <select v-model="uploadDlg.chat" :disabled="uploadDlg.busy" style="width:100%">
          <option v-for="o in uploadChatOptions" :key="o.value" :value="o.value">{{ o.label }}</option>
        </select>
      </div>
      <p class="err">{{ uploadDlg.msg }}</p>
      <button class="primary" style="width:100%" :disabled="uploadDlg.busy || !uploadDlg.files.length" @click="uploadStart">
        <span v-if="uploadDlg.busy" class="spinner" aria-hidden="true"></span>
        {{ uploadDlg.busy ? "در حال آپلود…" : "شروع آپلود" }}
      </button>
      <button class="ghost" style="width:100%" :disabled="uploadDlg.busy" @click="uploadDlg.open = false">انصراف</button>
    </dialog>

    <dialog :open="keyChatDlg.open">
      <h3>کانال ذخیره‌سازی کلید</h3>
      <p class="muted" style="margin:0">فایل‌های آپلودشده با کلید «{{ keyChatDlg.name }}» به این کانال می‌روند.</p>
      <div class="field">
        <label>کانال</label>
        <select v-model="keyChatDlg.chat" :disabled="keyChatDlg.busy" style="width:100%">
          <option value="">پیش‌فرض سیستم (بدون کانال اختصاصی)</option>
          <option v-for="o in keyChatOptions" :key="o.value" :value="o.value">{{ o.label }}</option>
        </select>
      </div>
      <p class="err">{{ keyChatDlg.msg }}</p>
      <button class="primary" style="width:100%" :disabled="keyChatDlg.busy" @click="keyChatSave">
        <span v-if="keyChatDlg.busy" class="spinner" aria-hidden="true"></span>
        {{ keyChatDlg.busy ? "در حال ذخیره…" : "ذخیره" }}
      </button>
      <button class="ghost" style="width:100%" :disabled="keyChatDlg.busy" @click="keyChatDlg.open = false">انصراف</button>
    </dialog>

    <dialog :open="qrDlg.open" @close="qrDlg.open = false">
      <h3>QR لینک عمومی</h3>
      <div style="background:#fff;padding:12px;border-radius:8px;display:flex;justify-content:center">
        <img v-if="qrDlg.slug" :src="'/' + qrDlg.slug + '/qr'" alt="QR" width="220" height="220">
      </div>
      <p dir="ltr" style="word-break:break-all;font-size:13px">{{ qrDlg.url }}</p>
      <button class="ghost" style="width:100%" @click="qrDlg.open = false">بستن</button>
    </dialog>

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

      <!-- Setup wizard dialog -->
      <dialog :open="setupDlg.open" @close="setupDlg.open = false">
        <h3>راه‌اندازی اولیه — مرحله {{ setupDlg.step + 1 }} از ۴</h3>
        <template v-if="setupDlg.step === 0">
          <p class="muted" style="font-size:13px">
            تنظیمات اولیه فایل <code>.env</code> {{ setupDlg.envFileExists ? "(فایل موجود است — فقط مقادیر ارسالی به‌روزرسانی می‌شوند)" : "(فایل موجود نیست — با ذخیره ساخته می‌شود)" }}.
            مقادیر خالی می‌توانند بعداً هم تنظیم شوند؛ کلید حساس «کلید رمزنگاری» در صورت خالی بودن خودکار تولید می‌شود.
          </p>
          <div v-for="it in setupDlg.envItems" :key="it.key" class="field">
            <label>{{ it.label }} <span class="muted" dir="ltr" style="font-size:11px">{{ it.key }}</span>
              <span v-if="it.required && !it.configured" class="err" style="font-size:11px">(الزامی)</span>
            </label>
            <template v-if="it.secret">
              <div style="display:flex;gap:8px;align-items:center">
                <input v-if="it._new != null" v-model="it._new" :type="it._show ? 'text' : 'password'" :placeholder="it.configured ? 'بدون تغییر' : 'مقدار جدید'" dir="ltr" autocomplete="off">
                <input v-else :value="it.preview || '— تنظیم نشده —'" disabled dir="ltr" style="opacity:.6">
                <button class="ghost" style="white-space:nowrap" @click="it._new = ''">{{ it.configured ? "تغییر" : "تنظیم" }}</button>
              </div>
            </template>
            <input v-else v-model="it.value" dir="ltr" :placeholder="it.configured ? '' : 'خالی → پیش‌فرض سیستم'">
          </div>
          <button class="ghost" style="width:100%" :disabled="setupDlg.envSaving" @click="saveSetupEnv">ذخیره در .env</button>
        </template>
        <template v-else-if="setupDlg.step === 1">
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
          <div class="field" v-if="setupDlg.backend === 'telegram'">
            <label>کانال ذخیره‌سازی تلگرام <span class="muted" style="font-size:11px">(اختیاری — خالی = Saved Messages)</span></label>
            <input v-model="setupDlg.storageChat" dir="ltr" autocomplete="off"
              placeholder="@username یا -100… (شناسه عددی کانال)">
            <p class="muted" style="font-size:11px;margin:4px 0 0">فایل‌های آپلودی به این کانال فرستاده می‌شوند؛ اکانت تلگرام باید دسترسی ارسال داشته باشد.</p>
          </div>
          <div class="field">
            <label>دیتابیس</label>
            <select v-model="setupDlg.dbEngine">
              <option value="sqlite">SQLite (محلی — بدون تنظیمات)</option>
              <option value="postgres" :disabled="!setupDlg.dbInfo || !setupDlg.dbInfo.pg_configured">
                PostgreSQL {{ setupDlg.dbInfo && setupDlg.dbInfo.pg_configured ? (setupDlg.dbInfo.engine === 'postgres' ? '(فعال)' : '(از ری‌استارت بعدی)') : '(در .env تنظیم نشده)' }}
              </option>
            </select>
          </div>
          <p v-if="setupDlg.dbInfo && setupDlg.dbInfo.pg_error" class="err" style="font-size:12px">
            Postgres در دسترس نیست: {{ setupDlg.dbInfo.pg_error }}
          </p>
          <p v-if="setupDlg.dbInfo && setupDlg.dbInfo.hint" class="muted" style="font-size:12px">{{ setupDlg.dbInfo.hint }}</p>
        </template>
        <p class="err">{{ setupDlg.msg }}</p>
        <button class="primary" style="width:100%" @click="setupNext">{{ setupDlg.step === 3 ? "تکمیل راه‌اندازی" : "مرحله بعد" }}</button>
        <button class="ghost" style="width:100%" @click="setupSkip">فعلاً نه</button>
      </dialog>

    <!-- Toast -->
    <div class="toast" :class="{error: toast.err}" v-show="toast.msg" role="status" aria-live="polite">{{ toast.msg }}</div>

    <!-- Persistent upload tray: lives OUTSIDE the dialog; uploads keep going when the dialog closes -->
    <div v-if="uploadJobs.length" id="upload-tray" class="card" :style="{ position:'fixed',bottom:'14px',left:'14px',width:'340px','max-width':'calc(100vw - 28px)','z-index':80,padding:'10px 12px','box-shadow':'0 8px 24px rgba(0,0,0,.35)', outline: trayDrag ? '2px dashed var(--accent,#3b82f6)' : 'none', 'outline-offset': '-4px', background: trayDrag ? 'rgba(59,130,246,.10)' : undefined }" @dragover.prevent="trayDrag = true" @dragenter.prevent="trayDrag = true" @dragleave.self="trayDrag = false" @drop.prevent="onTrayDrop">
      <div v-if="trayDrag" class="muted" style="text-align:center;font-size:11px;padding:2px 0 4px">فایل‌ها را همین‌جا رها کنید — با پوشه/کانالِ آخرین آپلود بالا می‌رود</div>
      <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:6px">
        <b style="font-size:13px">آپلودها ({{ uploadJobs.filter(j => j.status === 'uploading').length }} فعال<template v-if="uploadJobs.some(j => j.status === 'uploading' && !j.started)"> • {{ uploadJobs.filter(j => j.status === 'uploading' && !j.started).length }} در صف</template> / {{ uploadJobs.length }}, فضا آزاد شده: {{ totalFreedBytes > 0 ? fmtBytes(totalFreedBytes) : "۰" }})</b>
        <button class="ghost" style="padding:2px 8px;font-size:11px" @click="clearFinishedUploads" :disabled="!uploadJobs.some(j => j.status !== 'uploading')">پاک‌سازی تمام‌شده‌ها</button>
      </div>
      <div v-for="j in uploadJobs" :key="j.id" style="padding:6px 0;border-top:1px solid var(--border,#2a2f3a)">
        <div style="display:flex;align-items:center;gap:6px">
          <span class="muted" dir="ltr" style="font-size:11px;flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" :title="j.name">{{ j.name }}</span>
          <span v-if="j.folder" class="tag" style="font-size:10px" dir="ltr">{{ j.folder }}</span>
          <span v-if="j.chat" class="tag" style="font-size:10px" dir="ltr">{{ j.chat }}</span>
          <span class="badge" :class="j.status" style="font-size:10px">{{ j.status === 'uploading' ? (j.cancelRequested ? "در حال لغو…" : !j.started ? "در صف" : (j.batchTotal > 1 ? ("فایل " + j.batchIndex + " از " + j.batchTotal) : "در حال آپلود")) : j.status === "done" ? "صف شد" : j.status === "canceled" ? "لغو شد" : "خطا" }}</span>
          <button v-if="j.status === 'uploading'" class="ghost" style="padding:1px 7px;font-size:11px" @click="cancelUploadJob(j)">لغو</button>
          <button v-else class="ghost" style="padding:1px 7px;font-size:11px" @click="dismissUploadJob(j)">×</button>
        </div>
        <div style="display:flex;align-items:center;gap:8px;margin-top:3px">
          <span class="progress-track" style="flex:1" role="progressbar" :aria-valuenow="j.pct" aria-valuemin="0" aria-valuemax="100">
            <span class="progress-fill" :style="{ width: j.pct + '%', background: j.status === 'error' ? 'var(--err,#EF4444)' : j.status === 'canceled' ? 'var(--warn,#F59E0B)' : undefined }"></span>
          </span>
          <span class="muted" style="font-size:11px;min-width:64px;text-align:left">{{ j.pct }}%<span v-if="j.status === 'uploading' && j.speed > 0"> • {{ j.speed.toFixed(1) }} MB/s</span></span>
        </div>
        <p v-if="j.error" class="err" style="margin:2px 0 0;font-size:11px">{{ j.error }}</p>
      </div>
    </div>
  </template>
</template>

<script setup>
import { ref, reactive, computed, onMounted, onBeforeUnmount, watch } from "vue";

    /* ---------- auth state ---------- */
    const token = ref(localStorage.getItem("td_token") || "");
    const refresh = ref(localStorage.getItem("td_refresh") || "");
    const login = reactive({ user: "", pass: "", err: "", busy: false });

    /* ---------- ui state ---------- */
    const tab = ref("dash");

    /* ---------- ops tab auto-refresh ---------- */
    let opsPollInterval = null;
    onMounted(() => {
      opsPollInterval = setInterval(() => {
        if (!token.value) return; // no polling while on the login screen
        loaders.ops().catch(() => {/* ignore errors during unmount */});
      }, 30000);
    });
    onBeforeUnmount(() => {
      if (opsPollInterval) {
        clearInterval(opsPollInterval);
        opsPollInterval = null;
      }
      if (accTimerId) { clearInterval(accTimerId); accTimerId = null; }
    });
    const tabList = [
      { id: "dash", label: "داشبورد" },
      { id: "accounts", label: "اکانت‌ها" },
      { id: "bots", label: "بات‌ها" },
      { id: "eitaa", label: "ایتا" },
      { id: "channels", label: "کانال‌ها" },
      { id: "keys", label: "کلیدها" },
      { id: "files", label: "فایل‌ها" },
      { id: "queue", label: "صف" },
      { id: "audit", label: "لاگ‌ها" },
      { id: "proxies", label: "پراکسی‌ها" },
      { id: "settings", label: "تنظیمات" },
      { id: "ops", label: "عملیات" },
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
    /* admin pressure alerts: upload-queue stall + flooded telegram backends */
    const pressure = ref({ stall: { waiting: 0, stalled: false, oldest_waiting_s: 0, threshold: 0, gate_capacity: 0, upload_workers: 0, gate_full: false }, flooded: [], summary: "" });
    let pressureTimer = null;
    const pressureSummary = computed(() => pressure.value.summary || "");
    async function pollPressure() {
      try {
        const d = await api("/api/v1/queue/pressure");
        const prev = pressure.value.summary;
        pressure.value = d;
        // fire a toast when a NEW problem appears (not while it persists)
        if (d.summary && d.summary !== prev && tab.value !== "queue") showToast("⚠ " + d.summary, 5000, true);
      } catch (e) { /* admin-only endpoint; banner just stays hidden */ }
    }
    function syncPressurePolling() {
      if (token.value && !pressureTimer) {
        pollPressure();
        pressureTimer = setInterval(pollPressure, 5000);
      } else if (!token.value && pressureTimer) {
        clearInterval(pressureTimer);
        pressureTimer = null;
        pressure.value = { stall: { waiting: 0, stalled: false, oldest_waiting_s: 0, threshold: 0, gate_capacity: 0, upload_workers: 0, gate_full: false }, flooded: [], summary: "" };
      }
    }
    watch(() => token.value, syncPressurePolling, { immediate: true });
    onBeforeUnmount(() => { if (pressureTimer) clearInterval(pressureTimer); });
    const overview = ref(null);
    const nodesList = ref([]);
    const storageChannels = ref([]);
    const storageChannelsLoaded = ref(false);
    const storageChannelsBusy = ref(false);
    /* ---------- folders ---------- */
    const foldersFlat = ref([]);
    const currentFolderId = ref(null); // null = all files (root)
    const currentFolderPath = ref("");
    const filesFolderDraft = ref(""); // X-Folder for panel uploads
    const folderQuery = ref(""); // search query for folder files
    const channelFilter = ref(""); // files tab: filter by storage channel
    const channelFilterIsDefault = ref(false);
    const filesChannelPick = ref(""); // dropdown value: '' | __default__ | chat
    /* ---------- file manager: search/filter/pagination/preview/block/links ---------- */
    const filesQuery = ref("");
    const filesMime = ref("");
    const filesOrder = ref("date");
    const filesBlockedOnly = ref(false);
    const filesTrashed = ref(false);
    const filesTotal = ref(0);
    const filesLimit = ref(50);
    const filesOffset = ref(0);
    const filesInChannelTotal = ref(0);
    const filesInChannelBytes = ref(0);
    const filesInChannelByType = ref([]);
    const previewDlg = reactive({ open: false, id: "", name: "", mime: "", size: 0, kind: "", url: "", text: "" });
    const linksDlg = reactive({ open: false, fileId: "", name: "", items: [] });
    let searchTimer = null;
    const blockedFilesReport = ref([]);
    const blockedFilesTotal = ref(0);
    const selectedFiles = ref(new Set());
    const selectAllFiles = ref(false);
    const bulkMode = ref(false);
    function filesSearch() {
      clearTimeout(searchTimer);
      searchTimer = setTimeout(() => { filesOffset.value = 0; loaders.files(); }, 300);
    }
    function folderSearch() {
      clearTimeout(searchTimer);
      searchTimer = setTimeout(() => { filesOffset.value = 0; loaders.files(); }, 300);
    }
    function _previewKind(mime) {
      const m = (mime || "").toLowerCase();
      if (m.startsWith("image/")) return "image";
      if (m.startsWith("video/")) return "video";
      if (m.startsWith("audio/")) return "audio";
      if (m === "application/pdf") return "pdf";
      if (m.startsWith("text/") || m === "application/json") return "text";
      return "";
    }
    async function filePreview(f) {
      const kind = _previewKind(f.mime);
      Object.assign(previewDlg, { open: true, id: f.id, name: f.name, mime: f.mime, size: f.size, kind, url: "", text: "" });
      if (!kind) return;
      const url = `/api/v1/files/${f.id}/preview`;
      if (kind === "text") {
        try {
          const r = await fetch(url, { headers: { Authorization: "Bearer " + token.value } });
          if (!r.ok) throw new Error(await r.text());
          previewDlg.text = (await r.text()).slice(0, 20000);
        } catch (e) { showToast("خطا در پیش‌نمایش: " + e.message, 4000, true); previewDlg.open = false; }
      } else {
        // <img>/<video>/<iframe> cannot send Authorization headers → mint a
        // short-lived preview token bound to this one file (never expose the
        // long-lived panel JWT in element URLs)
        try {
          const d = await api(`/api/v1/files/${f.id}/preview-token`, { method: "POST", json: {} });
          previewDlg.url = d.url;
        } catch (e) { showToast("خطا در پیش‌نمایش: " + e.message, 4000, true); previewDlg.open = false; }
      }
    }
    function fileDownload(f) {
      // attachment download honoring auth header via fetch+blob
      fetch(`/api/v1/files/${f.id}/content`, { headers: { Authorization: "Bearer " + token.value } })
        .then((r) => { if (!r.ok) throw new Error("download failed"); return r.blob(); })
        .then((b) => { const a = document.createElement("a"); a.href = URL.createObjectURL(b); a.download = f.name; a.click(); URL.revokeObjectURL(a.href); })
        .catch((e) => showToast("خطا: " + e.message, 4000, true));
    }
    async function fileBlock(f, blocked) {
      if (blocked && !confirm("این فایل بن شود؟ همه لینک‌ها و پیش‌نمایش‌ها بسته می‌شود (فایل تلگرامی دست‌نخورده می‌ماند).")) return;
      await api(`/api/v1/files/${f.id}/block`, { method: "PATCH", json: { blocked } });
      showToast(blocked ? "فایل بن شد" : "بن برداشته شد");
      loaders.files();
    }
    async function toggleSelectFile(fileId) {
      if (selectedFiles.value.has(fileId)) {
        selectedFiles.value.delete(fileId);
      } else {
        selectedFiles.value.add(fileId);
      }
      updateSelectAll();
    }
    function updateSelectAll() {
      const allSelected = files.value.length > 0 && selectedFiles.value.size === files.value.length;
      selectAllFiles.value = allSelected;
    }
    function toggleBulkMode() {
      bulkMode.value = !bulkMode.value;
      if (!bulkMode.value) { selectedFiles.value.clear(); selectAllFiles.value = false; }
    }
    async function toggleSelectAll() {
      if (selectAllFiles.value) {
        selectedFiles.value = new Set(files.value.map((f) => f.id));
      } else {
        selectedFiles.value.clear();
      }
      updateSelectAll();
    }
    async function bulkDeleteSelected() {
      if (selectedFiles.value.size === 0) return;
      if (!confirm(`تایید حذف ${selectedFiles.value.size} فایل انتخاب شده؟`)) return;
      for (const fileId of selectedFiles.value) {
        await api(`/api/v1/files/${fileId}`, { method: "DELETE" });
      }
      selectedFiles.value.clear();
      selectAllFiles.value = false;
      showToast("فایل‌های انتخابی حذف شدند");
      loaders.files();
    }
    async function bulkBlockSelected() {
      if (selectedFiles.value.size === 0) return;
      if (!confirm(`تایید بن ${selectedFiles.value.size} فایل انتخاب شده؟`)) return;
      for (const fileId of selectedFiles.value) {
        await api(`/api/v1/files/${fileId}/block`, { method: "PATCH", json: { blocked: true } });
      }
      showToast("فایل‌های انتخابی بن شدند");
      loaders.files();
    }
    async function qRetry(j) {
      try { await api(`/api/v1/queue/retry/${j.id}`, { method: "POST" }); showToast("جاب دوباره صف شد"); loaders.queue(); }
      catch (e) { showToast("خطا: " + e.message, 4000, true); }
    }
    async function folderRename(f) {
      const v = prompt("نام جدید پوشه:", f.name);
      if (!v || v.trim() === f.name) return;
      try { await api(`/api/v1/folders/${f.id}`, { method: "PATCH", json: { name: v.trim() } }); showToast("نام پوشه تغییر کرد"); loaders.files(); }
      catch (e) { showToast("خطا: " + e.message, 4000, true); }
    }
    async function folderMoveDlg(f) {
      const v = prompt("مسیر والد جدید (خالی = ریشه):", f.path.split("/").slice(0, -1).join("/"));
      if (v === null) return;
      try {
        let parent_id = null;
        const p = v.trim().replace(/^\/+/, "");
        if (p) parent_id = (await api("/api/v1/folders/resolve?path=" + encodeURIComponent(p))).id || (await api("/api/v1/folders", { method: "POST", json: { path: p } })).id;
        await api(`/api/v1/folders/${f.id}`, { method: "PATCH", json: { parent_id } });
        showToast("پوشه منتقل شد"); loaders.files();
      } catch (e) { showToast("خطا: " + e.message, 4000, true); }
    }
    async function folderDelete(f) {
      if (!confirm(`پوشه «${f.path}» حذف شود؟ پوشه‌های تو در تو هم حذف می‌شوند (فایل‌ها سالم می‌مانند).`)) return;
      try { await api(`/api/v1/folders/${f.id}`, { method: "DELETE" }); showToast("پوشه حذف شد"); if (currentFolderId.value === f.id) openFolder(null); else loaders.files(); }
      catch (e) { showToast("خطا: " + e.message, 4000, true); }
    }
    async function fileLinksDlg(f) {
      Object.assign(linksDlg, { open: true, fileId: f.id, name: f.name, items: [] });
      try { const d = await api(`/api/v1/files/${f.id}/links`); linksDlg.items = d.items || []; }
      catch (e) { showToast("خطا: " + e.message, 4000, true); }
    }
    function linkCopy(l) { navigator.clipboard.writeText(location.origin + "/d/" + l.slug + "/dl"); showToast("کپی شد"); }
    async function linkToggle(l) {
      await api(`/api/v1/files/${linksDlg.fileId}/links/${l.id}`, { method: "PATCH", json: { disabled: !l.disabled } });
      l.disabled = !l.disabled;
      showToast(l.disabled ? "لینک غیرفعال شد" : "لینک فعال شد");
    }
    async function linkDelete(l) {
      if (!confirm("این لینک حذف شود؟")) return;
      await api(`/api/v1/files/${linksDlg.fileId}/links/${l.id}`, { method: "DELETE" });
      linksDlg.items = linksDlg.items.filter((x) => x.id !== l.id);
      showToast("لینک حذف شد");
    }
    const bulkMoveSelectedDlg = reactive({ open: false, path: "", msg: "", busy: false });
    /* bulk transfer to another storage channel: queue-based re-send of parts */
    const bulkTransferDlg = reactive({ open: false, chat: "", folderId: "", msg: "", busy: false });
    const bulkTransferChatOptions = computed(() => {
      const opts = [];
      if (channelsDefaultChat.value) opts.push({ value: channelsDefaultChat.value, label: `کانال پیش‌فرض سیستم (${channelsDefaultChat.value})` });
      for (const c of channels.value) if (!opts.some(o => o.value === c.chat)) opts.push({ value: c.chat, label: (c.label ? c.label + " — " : "") + c.chat });
      return opts;
    });
    function bulkTransferOpen() {
      if (selectedFiles.value.size === 0) return;
      loaders.channels();
      Object.assign(bulkTransferDlg, { open: true, chat: channelsDefaultChat.value || "", folderId: "", msg: "", busy: false });
    }
    /* bulk zip download: mint a signed url then let the browser save it;
    the archive is built (and cleaned up) server-side on the fly */
    const bulkZipBusy = ref(false);
    async function bulkZipDownload() {
      if (selectedFiles.value.size === 0) return;
      bulkZipBusy.value = true;
      try {
        const r = await api("/api/v1/files/bulk-zip", { method: "POST", json: { file_ids: [...selectedFiles.value] } });
        const skipped = (r.skipped || []).length;
        showToast(`آرشیو ${r.files} فایل آماده شد` + (skipped ? ` (${skipped} فایل رد شد)` : ""), 4000, !!skipped);
        const a = document.createElement("a");
        a.href = r.url;
        a.download = "tgdrive.zip";
        document.body.appendChild(a);
        a.click();
        a.remove();
      } catch (e) { showToast("خطا: " + e.message, 4500, true); }
      bulkZipBusy.value = false;
    }
    async function bulkTransferDo() {
      bulkTransferDlg.msg = "";
      if (!bulkTransferDlg.chat) { bulkTransferDlg.msg = "کانال مقصد را انتخاب کنید"; return; }
      bulkTransferDlg.busy = true;
      try {
        const body = { file_ids: [...selectedFiles.value], storage_chat: bulkTransferDlg.chat };
        if (bulkTransferDlg.folderId !== "") body.folder_id = Number(bulkTransferDlg.folderId);
        const r = await api("/api/v1/files/bulk-transfer", { method: "POST", json: body });
        showToast(`${r.enqueued?.length ?? 0} فایل به صف انتقال رفت` + (r.failed?.length ? `، ${r.failed.length} نادیده گرفته شد` : ""), 4000, !!r.failed?.length);
        bulkTransferDlg.open = false; bulkTransferDlg.chat = ""; bulkTransferDlg.folderId = "";
        selectedFiles.value.clear(); selectAllFiles.value = false;
        loaders.files();
        if (r.enqueued?.length && tab.value === "files") pollTransferProgress(); // refresh the live badge immediately
      } catch (e) { bulkTransferDlg.msg = e.message; }
      finally { bulkTransferDlg.busy = false; }
    }
    async function bulkMoveSelectedDo() {
      bulkMoveSelectedDlg.msg = "";
      if (selectedFiles.value.size === 0) { bulkMoveSelectedDlg.open = false; return; }
      bulkMoveSelectedDlg.busy = true;
      try {
        const path = bulkMoveSelectedDlg.path.trim().replace(/^\/+/, "");
        let target = null;
        if (path) {
          const r = await api("/api/v1/folders/resolve?path=" + encodeURIComponent(path));
          target = r.id || (await api("/api/v1/folders", { method: "POST", json: { path } })).id;
        }
        const r = await api(`/api/v1/folders/${target ?? "0"}/files`, { method: "POST", json: { file_ids: [...selectedFiles.value], target_folder_id: target } });
        showToast(`${r.moved?.length ?? 0} فایل منتقل شد` + (r.failed?.length ? `، ${r.failed.length} ناموفق` : ""), 4000, !!r.failed?.length);
        bulkMoveSelectedDlg.open = false; bulkMoveSelectedDlg.path = "";
        selectedFiles.value.clear(); selectAllFiles.value = false;
        loaders.files();
      } catch (e) { bulkMoveSelectedDlg.msg = e.message; }
      finally { bulkMoveSelectedDlg.busy = false; }
    }
    const folderDlg = reactive({ open: false, path: "", scope: "", msg: "" });
    const folderScopeOptions = computed(() => {
      const opts = [{ value: "", label: "عمومی (همه کانال‌ها)" }];
      if (channelsDefaultChat.value) opts.push({ value: channelsDefaultChat.value, label: `کانال پیش‌فرض (${channelsDefaultChat.value})` });
      for (const c of channels.value) if (!opts.some(o => o.value === c.chat)) opts.push({ value: c.chat, label: (c.label ? c.label + " — " : "") + c.chat });
      return opts;
    });
    const moveDlg = reactive({ open: false, fileId: "", fileName: "", path: "", msg: "" });
    const proxiesList = ref([]);
    const proxiesBusy = ref(false);
    const proxiesTesting = ref(false);
    const uploadBusy = ref(false); // legacy flag; the upload dialog owns busy state
    const uploadProgress = ref("");
    const uploadPct = ref(0); // 0-100, real XHR progress
    const uploadSpeed = ref(0); // MB/s
    const uploadTimeStart = ref(0); // timestamp
    const nowSec = Math.floor(Date.now() / 1000);

    /* ---------- dialogs ---------- */
    const proxyDlg = reactive({ open: false, link: "", host: "", port: "", kind: "socks5", label: "", username: "", password: "", msg: "" });
    const accDlg = reactive({ open: false, step: 1, phone: "", label: "", code: "", pass: "", msg: "", loginId: "", busy: false, resendIn: 0, resendsLeft: 3, pwLeft: 3 });
    const botDlg = reactive({ open: false, token: "", label: "", msg: "" });
    const eitDlg = reactive({ open: false, token: "", chat: "", label: "", msg: "" });
    const keyDlg = reactive({ open: false, name: "", scopes: "read,write", rpm: 120, quota: 0, backend: "", storageChat: "", result: "" });
    const qrDlg = reactive({ open: false, url: "", slug: "" });

    /* ---------- api helper (with refresh) ---------- */
    async function api(path, opts = {}) {
      if (opts.params) { path += (path.includes("?") ? "&" : "?") + new URLSearchParams(opts.params).toString(); delete opts.params; }
      opts.headers = { ...(opts.headers || {}), Authorization: "Bearer " + token.value };
      if (opts.json) { opts.headers["Content-Type"] = "application/json"; opts.body = JSON.stringify(opts.json); }
      const resp = await fetch(path, opts);
      if (resp.status === 401 && refresh.value) {
        const r = await fetch("/api/v1/auth/refresh", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ refresh_token: refresh.value }) });
        const d = await r.json();
        if (r.ok) {
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
      return await resp.json();
    }

    /* ---------- formatting ---------- */
    /* alias kept for legacy code paths */
    /* alias kept for legacy code paths */
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
      const p = l ? l() : Promise.resolve();
      if (id === "channels") loaders.accounts().catch(() => {}); // defaultChannelRow needs the accounts table
      // the files-tab channel filter shows per-channel file counts → keep the registry fresh
      if (id === "files") loaders.channels().catch(() => {});
      return p;
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
    const proxyLastFallback = computed(() => {
      const lf = overview.value?.proxies?.last_fallback;
      if (!lf) return "رخ نداده";
      return `${fmtTime(lf.at)} — ${lf.detail || ""}`;
    });
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
    /* ---------- channels (storage channel registry + backup/restore) ---------- */
    const channels = ref([]);
    const channelDefaultStats = ref({ files: 0, bytes: 0 });
    const channelsDefaultChat = ref("");
    const chanDlg = reactive({ open: false, chat: "", label: "", kind: "storage", msg: "" });
    const chanBusy = reactive({}); // id → busy flag (test/backup/restore in flight)
    loaders.channels = async () => {
      try {
        const d = await api("/api/v1/channels");
        channelsDefaultChat.value = d.default_chat || "";
        channelDefaultStats.value = d.default_stats || { files: 0, bytes: 0 };
        // registry rows missing the aggregate stats (older payloads) get zeros;
        // ready-file counts come from the same aggregate query server-side
        channels.value = (d.items || []).map((c) => ({ files: 0, bytes: 0, ...c }));
      }
      catch (e) { showToast("خطا: " + e.message, 4000, true); }
    };
    async function chanSave() {
      try {
        await api("/api/v1/channels", { method: "POST", json: { chat: chanDlg.chat, label: chanDlg.label, kind: chanDlg.kind } });
        showToast("کانال ثبت شد");
        Object.assign(chanDlg, { open: false, chat: "", label: "", kind: "storage", msg: "" });
        loaders.channels();
      } catch (e) { chanDlg.msg = e.message; }
    }
    async function chanRename(c) {
      const v = prompt("برچسب جدید کانال:", c.label || "");
      if (v === null) return;
      try { await api(`/api/v1/channels/${c.id}`, { method: "PATCH", json: { label: v } }); showToast("برچسب ذخیره شد"); loaders.channels(); }
      catch (e) { showToast("خطا: " + e.message, 4500, true); }
    }
    async function chanToggle(c) { try { await api(`/api/v1/channels/${c.id}/toggle`, { method: "POST" }); loaders.channels(); } catch (e) { showToast("خطا: " + e.message, 4500, true); } }
    async function chanDelete(c) {
      if (!confirm("کانال «" + (c.label || c.chat) + "» از فهرست حذف شود؟ (فایل‌های تلگرامی حذف نمی‌شوند)")) return;
      try { await api(`/api/v1/channels/${c.id}`, { method: "DELETE" }); showToast("کانال حذف شد"); loaders.channels(); }
      catch (e) { showToast("خطا: " + e.message, 4500, true); }
    }
    async function chanTest(c) {
      chanBusy[c.id] = true;
      try {
        const d = await api(`/api/v1/channels/${c.id}/test`, { method: "POST" });
        showToast(d.ok ? "ارتباط برقرار است (" + d.backend + ")" : "خطای ارتباط: " + d.error, 5000, !d.ok);
      } catch (e) { showToast("خطا: " + e.message, 4500, true); }
      chanBusy[c.id] = false;
      loaders.channels();
    }
    async function chanBackup(c) {
      chanBusy[c.id] = true;
      showToast("در حال ساخت و ارسال بکاپ به کانال…", 5000);
      try {
        const d = await api(`/api/v1/channels/${c.id}/backup`);
        showToast("بکاپ در کانال ذخیره شد (" + d.files + " فایل، " + fmtBytes(d.size) + ")", 5000);
        try {
          const j = await api(`/api/v1/channels/${c.id}/content`);
          const blob = new Blob([JSON.stringify(j, null, 2)], { type: "application/json" });
          const a = document.createElement("a");
          a.href = URL.createObjectURL(blob);
          a.download = d.name || ("tgdrive-backup-" + c.id + ".json");
          a.click();
          setTimeout(() => URL.revokeObjectURL(a.href), 5000);
        } catch (e) { /* local copy optional — channel copy is the source of truth */ }
      } catch (e) { showToast("خطا: " + e.message, 5000, true); }
      chanBusy[c.id] = false;
      loaders.channels();
    }
    async function chanRestore(c) {
      if (!confirm("بازگردانی فهرست فایل‌های این کانال از آخرین بکاپ؟ ردیف‌های موجود حفظ می‌شوند (ادغام).")) return;
      chanBusy[c.id] = true;
      try {
        const d = await api(`/api/v1/channels/${c.id}/restore`, { method: "POST" });
        showToast(`بازگردانی شد: ${d.inserted} افزوده، ${d.skipped} موجود`, 6000);
        loaders.files();
      } catch (e) { showToast("خطا: " + e.message, 5000, true); }
      chanBusy[c.id] = false;
      loaders.channels();
    }
    /* virtual row for the system-default channel (settings.tg_storage_chat) when
       it has no registry row of its own — fixes "default channel missing from the list" */
    const defaultChannelRow = computed(() => {
      if (!channelsDefaultChat.value) return null;
      return { chat: channelsDefaultChat.value, files: channelDefaultStats.value.files, bytes: channelDefaultStats.value.bytes };
    });
    function chanFmtBackup(c) {
      if (!c.last_backup_at) return "—";
      return fmtTime(c.last_backup_at) + " • " + (c.last_backup_files || 0) + " فایل" + (c.last_backup_bytes ? " • " + fmtBytes(c.last_backup_bytes) : "") + (c.last_backup_message_id ? " • msg " + c.last_backup_message_id : "");
    }
    loaders.keys = async () => {
      try { const d = await api("/api/v1/keys"); keys.value = d.items || []; }
      catch (e) { showToast("خطا: " + e.message, 4000, true); }
    };
    loaders.files = async () => {
      try {
        const p = new URLSearchParams();
        p.set("limit", String(filesLimit.value));
        p.set("offset", String(filesOffset.value));
        const searchQuery = currentFolderId.value !== null ? folderQuery.value : filesQuery.value; if (searchQuery.trim()) p.set("q", searchQuery.trim());
        if (filesMime.value) p.set("mime", filesMime.value);
        if (filesOrder.value !== "date") p.set("order", filesOrder.value);
        if (filesBlockedOnly.value) p.set("blocked", "1");
        if (filesTrashed.value) p.set("trashed", "1");
        if (channelFilter.value) {
          if (channelFilterIsDefault.value) p.set("default_channel", "1");
          else p.set("storage_chat", channelFilter.value);
        }
        const folderScope = channelFilter.value && !channelFilterIsDefault.value ? channelFilter.value : "";
        const [d, fo] = await Promise.all([api("/api/v1/files?" + p.toString()), api("/api/v1/folders", { params: folderScope ? { scope: folderScope } : {} })]);
        files.value = d.items || [];
        filesTotal.value = d.total || 0;
        foldersFlat.value = fo.items || [];
        // Fetch channel stats if a channel filter is active
        if (channelFilter.value) {
          if (channelFilterIsDefault.value) {
            const defaultChat = await api("/api/v1/admin/settings", { params: { key: "tg_storage_chat" } });
            const defChat = defaultChat.tg_storage_chat || "";
            const stats = await api("/api/v1/files", {
              params: { storage_chat: defChat, default_channel: "1" }
            });
            filesInChannelTotal.value = stats.total || 0;
            filesInChannelBytes.value = 0;
            // Calculate bytes from items
            if (stats.items) {
              filesInChannelBytes.value = stats.items.reduce((sum, f) => sum + (f.size || 0), 0);
            }
            // Get type distribution
            const typeMap = {};
            stats.items?.forEach(f => {
              const mime = f.mime || "";
              const base = mime.split("/")[0] || "other";
              typeMap[base] = (typeMap[base] || 0) + 1;
            });
            filesInChannelByType.value = Object.entries(typeMap)
              .sort((a, b) => b[1] - a[1])
              .slice(0, 3)
              .map(([key, count]) => ({ key, count }));
          } else {
            const stats = await api("/api/v1/files", {
              params: { storage_chat: channelFilter.value }
            });
            filesInChannelTotal.value = stats.total || 0;
            filesInChannelBytes.value = 0;
            if (stats.items) {
              filesInChannelBytes.value = stats.items.reduce((sum, f) => sum + (f.size || 0), 0);
            }
            const typeMap = {};
            stats.items?.forEach(f => {
              const mime = f.mime || "";
              const base = mime.split("/")[0] || "other";
              typeMap[base] = (typeMap[base] || 0) + 1;
            });
            filesInChannelByType.value = Object.entries(typeMap)
              .sort((a, b) => b[1] - a[1])
              .slice(0, 3)
              .map(([key, count]) => ({ key, count }));
          }
        }
      }
      catch (e) { showToast("خطا: " + e.message, 4000, true); }
    };
    /* dropdown channel picker (files tab) — mirrors the channelFilter state used by loaders.files */
    function applyChannelPick() {
      if (!filesChannelPick.value) { channelFilter.value = ""; channelFilterIsDefault.value = false; }
      else if (filesChannelPick.value === "__default__") { channelFilter.value = channelsDefaultChat.value; channelFilterIsDefault.value = true; }
      else { channelFilter.value = filesChannelPick.value; channelFilterIsDefault.value = false; }
      currentFolderId.value = null;
      filesOffset.value = 0;
      loaders.files();
    }
    async function openChannelFiles(c) {
      channelFilter.value = c.chat || "(بدون کانال)";
      channelFilterIsDefault.value = !!c.is_system_default;
      // jump to the files tab with the channel filter applied
      switchTab("files");
      showToast(c.files + " فایل در " + (c.chat || "کانال پیش‌فرض"), 3000);
    }
    function clearChannelFilter() {
      channelFilter.value = "";
      channelFilterIsDefault.value = false;
      loaders.files();
    }
    async function openFolder(id) {
      currentFolderId.value = id;
      channelFilter.value = ""; channelFilterIsDefault.value = false; // mutually exclusive
      filesOffset.value = 0;
      if (id === null) {
        currentFolderPath.value = "";
        filesFolderDraft.value = "";
        await loaders.files();
        return;
      }
      try {
        const d = await api(`/api/v1/folders/${id}/all`);
        files.value = d.items || [];
        currentFolderPath.value = "/" + (d.folder?.path || "");
        filesFolderDraft.value = d.folder?.path || "";
      } catch (e) { showToast("خطا: " + e.message, 4000, true); }
    }
    async function folderCreate() {
      folderDlg.msg = "";
      try {
        const body = {
          ...(folderDlg.path.includes("/") ? { path: folderDlg.path } : { name: folderDlg.path }),
          scope: folderDlg.scope || "",
        };
        await api("/api/v1/folders", { method: "POST", json: body });
        showToast("پوشه ساخته شد");
        folderDlg.open = false; folderDlg.path = ""; folderDlg.scope = "";
        await loaders.files();
      } catch (e) { folderDlg.msg = e.message; }
    }
    function fileMoveDlg(f) {
      Object.assign(moveDlg, { open: true, fileId: f.id, fileName: f.name, path: "", msg: "" });
    }
    async function moveFileDo() {
      moveDlg.msg = "";
      try {
        const path = moveDlg.path.trim().replace(/^\/+/,"");
        if (!path) {
          // detach from any folder
          const f = files.value.find((x) => x.id === moveDlg.fileId);
          if (f?.folder_id) await api(`/api/v1/folders/${f.folder_id}/files/${moveDlg.fileId}`, { method: "DELETE" });
        } else {
          const r = await api("/api/v1/folders/resolve?path=" + encodeURIComponent(path));
          let fid = r.id;
          if (!fid) {
            const c = await api("/api/v1/folders", { method: "POST", json: { path } });
            fid = c.id;
          }
          await api(`/api/v1/folders/${fid}/files/${moveDlg.fileId}`, { method: "POST" });
        }
        showToast("انتقال انجام شد");
        moveDlg.open = false;
        await loaders.files();
        if (currentFolderId.value !== null) await openFolder(currentFolderId.value);
      } catch (e) { moveDlg.msg = e.message; }
    }
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
    loaders.blockedFilesReport = async () => {
      try { const d = await api("/api/v1/admin/blocked-files-report?limit=200"); blockedFilesReport.value = d.items || []; blockedFilesTotal.value = d.total || 0; }
      catch (e) { showToast("خطا: " + e.message, 4000, true); }
    };
    loaders.ops = async () => {
      try {
        const [q, n, p] = await Promise.all([api("/api/v1/queue/stats"), api("/api/v1/admin/nodes"), api("/api/v1/admin/proxies")]);
        // Queue status
        queueStats.value = { ...q, paused: (q.paused || []).join(",") || "-" };
        // Nodes health
        nodesList.value = n.items || [];
        // Proxies status
        proxiesList.value = p.items || [];
        // Count errors from proxy health
        let totalErrors = 0;
        for (const pr of proxiesList.value) {
          if (pr.status === "down" || pr.status === "degraded") totalErrors++;
        }
        // Proxy error count display
        proxyErrorCount.value = totalErrors;
      } catch (e) { showToast("خطا: " + e.message, 4000, true); }
    };

    /* ---------- telegram proxy pool ---------- */
    const proxyStatusLabels = { ok: "سالم", down: "قطع", degraded: "ضعیف", unknown: "آزمایش نشده" };
    const proxyStatusDetails = {
      ok: "اتصال برقرار است و تاخیر سنجیده شده — در انتخاب پراکسی اولویت دارد.",
      down: "اتصال برقرار نشد (timeout یا connection refused) — از انتخاب پراکسی خارج شده است.",
      degraded: "اتصال برقرار است اما کند/ناپایدار — مثل سالم قابل استفاده است ولی بعد از «سالم» انتخاب می‌شود.",
      unknown: "هنوز تست نشده — تا اولین تست، به‌عنوان آخرین گزینه در دسترس است.",
    };
    const proxyStatusLegend = [
      { status: "ok", color: "var(--ok,#22C55E)" },
      { status: "degraded", color: "var(--warn,#F59E0B)" },
      { status: "down", color: "var(--err,#EF4444)" },
      { status: "unknown", color: "var(--muted,#94A3B8)" },
    ];
    function proxyTooltip(p) {
      const lbl = proxyStatusLabels[p.status] || p.status;
      let extra = "";
      if (p.status === "down" && p.last_error) extra = " — خطا: " + p.last_error;
      else if (p.latency_ms != null && p.latency_ms >= 0) extra = " — تاخیر: " + fmtLatency(p.latency_ms);
      return lbl + ": " + (proxyStatusDetails[p.status] || "") + extra;
    }
    const proxyErrorCount = ref(0);
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

    const optionedSettings = ["fake_tg", "eitaa_mode", "transfer_delete_source"]; // int settings rendered as selects
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
      } else if (it.key === "tg_api_hash") {
        const h = String(value).trim();
        // unchanged placeholder-ish env values are allowed; only newly typed
        // values must look like a real 32-char hex hash
        if (h && h !== String(it.current).trim() && !/^[0-9a-fA-F]{32}$/.test(h)) return "هش باید ۳۲ کاراکتر هگز باشد";
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
        if (v === "" || v == null) continue;
        // ints compare numerically so env bools (false) vs draft "0" don't count as dirty
        const changed = it.type === "int" ? Number(v) !== Number(it.current) : String(v) !== String(it.current);
        if (changed) updates[it.key] = v;
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
      max_concurrent_uploads: "سقف آپلود همزمان کل سیستم (فشار روی بک‌اندها)",
      upload_stall_threshold_s: "آستانه هشدار معطلی صف آپلود (ثانیه؛ ۰=خاموش)",
      flood_alert_threshold: "تعداد FloodWait پشت‌سرهم تا هشدار فشار تلگرام",
      flood_alert_cooldown_s: "فاصله تکرار هشدار فشار (ثانیه)",
      default_backend: "بک‌اند پیش‌فرض",
      eitaa_mode: "مقصد ایتا (۰=تلگرام، ۱=ایتا)",
      transfer_delete_source: "پاک‌سازی خودکار منبع پس از انتقال (۰=خاموش، ۱=روشن)",
      fake_tg: "حالت تست (تلگرام آزمایشی درون‌حافظه‌ای)",
      fake_send_delay_s: "تأخیر ارسال در حالت تست (ثانیه؛ شبیه‌سازی کندی تلگرام)",
      tg_api_id: "API ID تلگرام (my.telegram.org)",
      tg_api_hash: "API Hash تلگرام (my.telegram.org)",
      tg_storage_chat: "کانال ذخیره‌سازی (فایل‌ها اینجا ذخیره می‌شوند)",
      proxy_enabled: "استفاده از پراکسی (۰=خیر، ۱=بله)",
      proxy_strategy: "استراتژی انتخاب پراکسی",
      proxy_monitor_interval: "بازه تست دوره‌ای پراکسی‌ها (دقیقه؛ ۰=خاموش)",
    };
    const settingsGroups = computed(() => {
      const g = { limits: "محدودیت‌ها", links: "لینک و انقضا", queue: "صف و همزمانی", backend: "بک‌اند", telegram_api: "تلگرام و ایتا — حالت تست و API", proxy: "پراکسی تلگرام" };
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
        // normalize: ints to numeric strings so option selects match their values
        for (const it of settingsItems.value)
          settingsDraft[it.key] = it.type === "int" ? String(Number(it.current) || 0) : (it.current ?? "");
        for (const k of Object.keys(settingsErrors)) delete settingsErrors[k];
        nodesList.value = n.items || [];
      } catch (e) { showToast("خطا: " + e.message, 4000, true); }
      await loadStorageChannels();
    };
    /* ---------- storage channels report ---------- */
    async function loadStorageChannels() {
      storageChannelsBusy.value = true;
      try {
        const d = await api("/api/v1/admin/storage-channels");
        storageChannels.value = d.items || [];
        storageChannelsLoaded.value = true;
      } catch (e) { showToast("خطا: " + e.message, 4000, true); }
      storageChannelsBusy.value = false;
    }
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
        const res = await api("/api/v1/admin/settings", { method: "PUT", json: updates });
        if (res.applied?.includes("fake_tg")) {
          showToast("حالت تست تغییر کرد و اتصال‌ها بازسازی شدند — وضعیت را در تب اکانت‌ها/ایتا چک کنید", 6000, true);
          await loaders.dash();
        } else if (res.applied?.includes("tg_api_id") || res.applied?.includes("tg_api_hash")) {
          showToast("اعتبارنامه‌ی API ذخیره و اتصال‌ها بازسازی شد", 5000, true);
        }
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
      for (const it of settingsItems.value)
        settingsDraft[it.key] = it.type === "int" ? String(Number(it.current) || 0) : (it.current ?? "");
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
    const setupDlg = reactive({ open: false, step: 0, pass: "", pass2: "", backend: "telegram", dbEngine: "", dbInfo: null, msg: "",
      storageChat: "",
      envItems: [], envFileExists: false, envSaving: false, envGenerated: "" });
    const setupNeeded = ref(false);
    async function loadSetupEnv() {
      try {
        const d = await api("/api/v1/admin/setup/env");
        setupDlg.envItems = d.items || [];
        setupDlg.envFileExists = !!d.file_exists;
      } catch (e) { setupDlg.envItems = []; }
    }
    async function checkSetup() {
      try {
        const [d, st] = await Promise.all([
          api("/api/v1/admin/setup/status"),
          api("/api/v1/admin/settings").catch(() => ({ items: [] })),
        ]);
        setupNeeded.value = !d.initialized;
        if (!d.initialized) {
          const stMap = Object.fromEntries((st.items || []).map((i) => [i.key, i.current]));
          Object.assign(setupDlg, {
            open: true, step: 0, pass: "", pass2: "", backend: "telegram", msg: "", envGenerated: "",
            storageChat: String(stMap.tg_storage_chat ?? ""),
            dbEngine: d.database?.engine === "postgres" ? "postgres" : (d.database?.pg_configured ? "" : "sqlite"),
            dbInfo: d.database || null,
          });
          await loadSetupEnv();
        }
      } catch (e) { /* non-admin or transient: ignore */ }
    }
    async function saveSetupEnv() {
      setupDlg.envSaving = true;
      try {
        const values = {};
        for (const it of setupDlg.envItems) {
          if (it.secret) {
            if (it._new) values[it.key] = it._new;           // new secret value typed by admin
          } else if (it.value != null && it.value !== "") {
            values[it.key] = it.value;
          }
        }
        const d = await api("/api/v1/admin/setup/env", { method: "PUT", json: { values, generate_secret: true } });
        if (d.restart_keys && d.restart_keys.length) {
          showToast("تغییر " + d.restart_keys.length + " کلید بعد از ری‌استارت اعمال می‌شود", 5000);
        } else {
          showToast("فایل .env ذخیره شد" + (d.created ? " (ساخته شد)" : ""));
        }
        await loadSetupEnv();
      } catch (e) { setupDlg.msg = e.message; }
      setupDlg.envSaving = false;
    }
    async function setupNext() {
      if (setupDlg.step === 0) {
        setupDlg.msg = ""; setupDlg.step = 1;
      } else if (setupDlg.step === 1) {
        if (setupDlg.pass || setupDlg.pass2) {
          if (setupDlg.pass.length < 6) { setupDlg.msg = "رمز حداقل ۶ کاراکتر باشد"; return; }
          if (setupDlg.pass !== setupDlg.pass2) { setupDlg.msg = "تکرار رمز مطابقت ندارد"; return; }
        }
        setupDlg.msg = ""; setupDlg.step = 2;
      } else if (setupDlg.step === 2) {
        setupDlg.step = 3;
      } else {
        try {
          await api("/api/v1/admin/setup/complete", { method: "POST", json: {
            new_password: setupDlg.pass || "",
            default_backend: setupDlg.backend,
            storage_chat: (setupDlg.backend === "telegram" ? (setupDlg.storageChat || "").trim() : ""),
            db_engine: setupDlg.dbEngine || "",
          } });
          setupDlg.open = false; setupNeeded.value = false;
          showToast("راه‌اندازی اولیه کامل شد");
          if (setupDlg.dbEngine === "postgres") showToast("انتخاب Postgres در ری‌استارت بعدی اعمال می‌شود", 5000);
        } catch (e) { setupDlg.msg = e.message; }
      }
    }
    function setupSkip() {
      setupDlg.open = false;
      showToast("می‌توانید بعداً از تب تنظیمات ادامه دهید");
    }

    /* ---------- accounts ---------- */
    function accOpen() { Object.assign(accDlg, { open: true, step: 1, phone: "", label: "", code: "", pass: "", msg: "", loginId: "", busy: false, resendIn: 0, resendsLeft: 3, pwLeft: 3 }); }

    /* resend cooldown: resend becomes available after 2 minutes; server caps at 3 */
    let accTimerId = null;
    const accTimerFmt = computed(() => Math.floor(accDlg.resendIn / 60) + ":" + String(accDlg.resendIn % 60).padStart(2, "0"));
    function accStartTimer() {
      accDlg.resendIn = 120;
      if (accTimerId) clearInterval(accTimerId);
      accTimerId = setInterval(() => {
        if (accDlg.resendIn > 0) accDlg.resendIn--;
        if (accDlg.resendIn <= 0 && accTimerId) { clearInterval(accTimerId); accTimerId = null; }
      }, 1000);
    }
    function accStopTimer() { if (accTimerId) { clearInterval(accTimerId); accTimerId = null; } accDlg.resendIn = 0; }
    function accClose() { if (accDlg.busy) return; accStopTimer(); accDlg.open = false; }

    async function accSend() {
      if (accDlg.busy) return;
      if (!accDlg.phone.trim()) { accDlg.msg = "شماره تلفن را وارد کنید (مثلا +98912...)"; return; }
      accDlg.busy = true; accDlg.msg = "";
      try {
        const d = await api("/api/v1/accounts/login/start", { method: "POST", json: { phone: accDlg.phone, label: accDlg.label } });
        if (d.status === "ready") { showToast("اکانت آماده شد"); accDlg.open = false; loaders.accounts(); return; }
        accDlg.loginId = d.login_id;
        accDlg.step = 2;
        accDlg.code = ""; accDlg.pass = "";
        accDlg.msg = "کد ارسال شد — آن را در تلگرام رسمی خود ببینید.";
        accStartTimer();
      } catch (e) { accDlg.msg = e.message; }
      finally { accDlg.busy = false; }
    }
    async function accResend() {
      if (accDlg.busy || accDlg.resendIn > 0 || accDlg.resendsLeft <= 0) return;
      accDlg.busy = true; accDlg.msg = "";
      try {
        const d = await api("/api/v1/accounts/login/resend", { method: "POST", json: { login_id: accDlg.loginId } });
        accDlg.resendsLeft = (d.resends_left == null) ? accDlg.resendsLeft - 1 : d.resends_left;
        accStartTimer();
        accDlg.msg = "کد دوباره ارسال شد.";
      } catch (e) { accDlg.msg = e.message; }
      finally { accDlg.busy = false; }
    }
    async function accDone() {
      if (accDlg.busy) return;
      accDlg.busy = true; accDlg.msg = "";
      try {
        const payload = accDlg.step === 3
          ? { login_id: accDlg.loginId, code: "", password: accDlg.pass }
          : { login_id: accDlg.loginId, code: accDlg.code, password: "" };
        const d = await api("/api/v1/accounts/login/complete", { method: "POST", json: payload });
        if (d.status === "password_needed") {
          accDlg.step = 3; accDlg.pwLeft = 3; accDlg.pass = "";
          accDlg.msg = "رمز دو مرحله‌ای لازم است — فقط فیلد رمز فعال است.";
          return;
        }
        accStopTimer();
        showToast("اکانت متصل شد");
        accDlg.open = false; loaders.accounts();
      } catch (e) {
        const m = String(e.message || "");
        const mt = m.match(/(\d+)\s*تلاش باقی مانده/);
        if (accDlg.step === 3 && mt) accDlg.pwLeft = Number(mt[1]) || 1;
        accDlg.msg = m;
      }
      finally { accDlg.busy = false; }
    }
    async function accToggle(a) { await api(`/api/v1/accounts/${a.id}/toggle`, { method: "POST" }); loaders.accounts(); }
    async function accTest(a) { const d = await api(`/api/v1/accounts/${a.id}/test`, { method: "POST" }); showToast(d.ok ? "اتصال سالم" : "خطا: " + d.error, 3000, !d.ok); }
    async function accReset(a) { await api(`/api/v1/accounts/${a.id}/reset`, { method: "POST" }); showToast("ریست شد"); loaders.accounts(); }
    /* per-account storage channel: pick from registered channels or the system default */
    const accChatDlg = reactive({ open: false, id: 0, label: "", chat: "", msg: "", busy: false });
    const accChatOptions = computed(() => {
      const opts = [];
      if (channelsDefaultChat.value) opts.push({ value: "default", label: `پیش‌فرض سیستم (${channelsDefaultChat.value})` });
      for (const c of channels.value) opts.push({ value: c.chat, label: (c.label ? c.label + " — " : "") + c.chat + (c.is_system_default ? " (پیش‌فرض)" : "") });
      opts.push({ value: "me", label: "Saved Messages (me)" });
      return opts;
    });
    function accChatOpen(a) {
      Object.assign(accChatDlg, { open: true, id: a.id, label: a.label || a.phone, chat: a.storage_chat_id || "me", msg: "", busy: false });
      loaders.channels();
    }
    async function accChatSave() {
      accChatDlg.busy = true; accChatDlg.msg = "";
      try {
        await api(`/api/v1/accounts/${accChatDlg.id}`, { method: "PATCH", json: { storage_chat_id: accChatDlg.chat } });
        showToast("کانال ذخیره‌سازی اکانت به‌روزرسانی شد");
        accChatDlg.open = false;
        loaders.accounts();
      } catch (e) { accChatDlg.msg = e.message; }
      finally { accChatDlg.busy = false; }
    }
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
    async function botTest(b) {
      showToast("در حال تست ربات…");
      try {
        const d = await api(`/api/v1/bots/${b.id}/test`, { method: "POST" });
        showToast(d.ok ? "ربات سالم است (پیام آزمایشی ارسال شد)" : "خطا: " + d.error, 3500, !d.ok);
      } catch (e) { showToast("خطا: " + e.message, 3500, true); }
      loaders.bots();
    }
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
    function keyOpen() { Object.assign(keyDlg, { open: true, name: "", scopes: "read,write", rpm: 120, quota: 0, backend: "", storageChat: "", result: "" }); loaders.channels(); }
    /* per-key storage channel: pick from registered channels / system default */
    const keyChatDlg = reactive({ open: false, id: 0, name: "", chat: "", msg: "", busy: false });
    const keyChatOptions = computed(() => {
      const opts = [];
      if (channelsDefaultChat.value) opts.push({ value: channelsDefaultChat.value, label: `پیش‌فرض سیستم (${channelsDefaultChat.value})` });
      for (const c of channels.value) if (!opts.some(o => o.value === c.chat)) opts.push({ value: c.chat, label: (c.label ? c.label + " — " : "") + c.chat });
      return opts;
    });
    function keyEditChat(k) {
      Object.assign(keyChatDlg, { open: true, id: k.id, name: k.name, chat: k.storage_chat || "", msg: "", busy: false });
      loaders.channels();
    }
    async function keyChatSave() {
      keyChatDlg.busy = true; keyChatDlg.msg = "";
      try {
        await api(`/api/v1/keys/${keyChatDlg.id}`, { method: "PATCH", json: { storage_chat: keyChatDlg.chat } });
        showToast(keyChatDlg.chat ? "کانال ذخیره‌سازی کلید ذخیره شد" : "کلید به کانال پیش‌فرض برگشت");
        keyChatDlg.open = false;
        loaders.keys();
      } catch (e) { keyChatDlg.msg = e.message; }
      finally { keyChatDlg.busy = false; }
    }
    async function keySave() {
      try {
        const d = await api("/api/v1/keys", { method: "POST", json: { name: keyDlg.name, scopes: keyDlg.scopes, rpm: keyDlg.rpm || 120, daily_quota_gb: keyDlg.quota || 0, backend: keyDlg.backend || undefined, storage_chat: (keyDlg.backend === 'eitaa' ? undefined : (keyDlg.storageChat || "").trim() || undefined) } });
        keyDlg.result = d.key;
        loaders.keys();
      } catch (e) { showToast(e.message, 4000, true); }
    }
    function copyKey() { navigator.clipboard.writeText(keyDlg.result); showToast("کپی شد"); }
    async function keyRevoke(k) { if (confirm("این کلید برای همیشه باطل شود؟")) { await api(`/api/v1/keys/${k.id}`, { method: "DELETE" }); loaders.keys(); } }

    /* ---------- files ---------- */
    let cancelXHR = null;
    /* upload options dialog: target folder + storage channel override + original filename */
    const uploadDlg = reactive({ open: false, files: [], folder: "", chat: "", msg: "", busy: false });
    const uploadChatOptions = computed(() => {
      const opts = [{ value: "", label: "پیش‌فرض کلید/سیستم" }];
      if (channelsDefaultChat.value) opts.push({ value: channelsDefaultChat.value, label: `کانال پیش‌فرض (${channelsDefaultChat.value})` });
      for (const c of channels.value) if (!opts.some(o => o.value === c.chat)) opts.push({ value: c.chat, label: (c.label ? c.label + " — " : "") + c.chat });
      return opts;
    });
    /* persistent upload tray state: each queued file becomes a job that runs
       INDEPENDENT of the dialog — closing the dialog never aborts an upload */
    const uploadJobs = ref([]);
    let uploadJobSeq = 0;
    let totalFreedBytes = 0;
    function clearFinishedUploads() { uploadJobs.value = uploadJobs.value.filter((j) => j.status === "uploading"); }
    function dismissUploadJob(j) { uploadJobs.value = uploadJobs.value.filter((x) => x.id !== j.id); }
    function cancelUploadJob(j) {
      if (j.status !== "uploading" || j.cancelRequested) return;
      j.cancelRequested = true;
      j.status = "canceled";
      if (j.controller) { try { j.controller.abort(); } catch (e) { /* already done */ } }
      // server-side too: tombstone the resumable session so racing chunk
      // PATCHes fail and the tmp .part file is deleted; surface freed bytes
      // back to the persistent upload tray
      if (j.sessionId) {
        api(`/api/v1/files/upload/session/${j.sessionId}`, { method: "DELETE" })
          .then(async (r) => {
            const jr = await r.json();
            const freed = Number(jr.bytes_freed ?? jr.part_bytes?.length ?? 0) || 0;
            if (Number.isFinite(freed)) totalFreedBytes += freed;
            showToast(`فایل موقت حذف شد: ${freed > 0 ? fmtBytes(freed) : "هیچ»}`);
          })
          .catch(() => {});
      }
    }
    function uploadPick() { loaders.channels(); switchTab("files"); uploadDlg.open = true; uploadDlg.files = []; }
    function onUploadFileChosen(ev) { uploadDlg.files = Array.from(ev.target.files || []); ev.target.value = ""; if (uploadJobs.length > 0 && uploadJobs[0].folder) { uploadDlg.folder = uploadJobs[0].folder; } if (uploadJobs.length > 0 && uploadJobs[0].chat) { uploadDlg.chat = uploadJobs[0].chat; } }
    /* drag & drop onto the files tab → same upload dialog (folder/channel preserved) */
    const filesDragDepth = ref(0);
    /* live transfer indicator: polls /queue/transfer-progress while the files
    tab is visible and transfer jobs exist; each item keeps its own percent */
    const transferJobs = ref([]);
    let transferPollTimer = null;
    const transferRunning = computed(() => transferJobs.value.some((j) => j.pct > 0));
    async function pollTransferProgress() {
      try {
        const d = await api("/api/v1/queue/transfer-progress");
        transferJobs.value = d.items || [];
      } catch (e) { /* admin-only endpoint; indicator just stays hidden */ }
    }
    function syncTransferPolling() {
      const active = tab.value === "files";
      if (active && !transferPollTimer) {
        pollTransferProgress();
        transferPollTimer = setInterval(pollTransferProgress, 2500);
      } else if (!active && transferPollTimer) {
        clearInterval(transferPollTimer);
        transferPollTimer = null;
        transferJobs.value = [];
      }
    }
    watch(() => tab.value, syncTransferPolling, { immediate: true });
    onBeforeUnmount(() => { if (transferPollTimer) clearInterval(transferPollTimer); });
    /* drop onto a sidebar folder row → upload straight into that folder
    (scoped folders also pin their own channel); the row highlights while the
    file hovers over it */
    const SIDEBAR_ROOT = "__root__"; // hover key for the "all files" row (null means no hover)
    const sidebarDropId = ref(null); // hovered row: folder id or SIDEBAR_ROOT
    function sbRowStyle(id) {
      const key = id === null ? SIDEBAR_ROOT : id;
      if (sidebarDropId.value === key) return { background: "rgba(59,130,246,.18)", outline: "2px dashed var(--accent,#3b82f6)", "outline-offset": "-2px" };
      if (currentFolderId.value === id) return { background: "var(--accent,#3b82f6)", color: "#fff" };
      return {};
    }
    function onSidebarDragOver(id) { sidebarDropId.value = id === null ? SIDEBAR_ROOT : id; }
    function onSidebarDragLeave(id) { const key = id === null ? SIDEBAR_ROOT : id; if (sidebarDropId.value === key) sidebarDropId.value = null; }
    function onSidebarDrop(ev, folder) {
      sidebarDropId.value = null;
      if (tab.value !== "files" || uploadDlg.busy) return;
      const dropped = Array.from(ev.dataTransfer?.files || []);
      if (!dropped.length) return;
      if (filesTrashed.value) filesTrashed.value = false;
      ev.sidebarDropped = true; // onFilesDrop must not open the dialog for this
      filesDragDepth.value = 0;
      const path = folder ? folder.path : "";
      const chat = folder && folder.scope ? folder.scope : ""; // scoped folder pins its channel
      enqueueUploadJobs(dropped.map((f) => [f, path, chat]));
      showToast("آپلود " + dropped.length + " فایل در " + (path || "/"));
      filesFolderDraft.value = path;
    }
    /* drag & drop onto the tray itself → enqueue with the last job's folder/chat */
    const trayDrag = ref(false);
    function onTrayDrop(ev) {
      trayDrag.value = false;
      if (uploadDlg.busy) return;
      const dropped = Array.from(ev.dataTransfer?.files || []);
      if (!dropped.length) return;
      const last = uploadJobs.value[uploadJobs.value.length - 1];
      const folder = (last && last.folder) || "";
      const chat = (last && last.chat) || "";
      if (filesTrashed.value) filesTrashed.value = false;
      enqueueUploadJobs(dropped.map((f) => [f, folder, chat]));
      showToast("آپلود " + dropped.length + " فایل با تنظیمات آخرین جاب");
    }
    function onFilesDrop(ev) {
      filesDragDepth.value = 0;
      if (ev.sidebarDropped) return; // handled by the sidebar folder row
      if (tab.value !== "files" || uploadDlg.busy) return;
      const dropped = Array.from(ev.dataTransfer?.files || []);
      if (!dropped.length) return;
      if (filesTrashed.value) filesTrashed.value = false; // uploads never target trash view
      // prefill: current folder (draft mirrors it) + active channel filter
      uploadDlg.folder = filesFolderDraft.value || "";
      uploadDlg.chat = channelFilterIsDefault.value ? "" : channelFilter.value;
      uploadDlg.files = dropped;
      uploadDlg.msg = "";
      loaders.channels();
      uploadDlg.open = true;
    }
    async function uploadStart() {
      if (!uploadDlg.files.length) { uploadDlg.msg = "فایل را انتخاب کنید"; return; }
      const folder = uploadDlg.folder.trim();
      const chat = uploadDlg.chat;
      const dropped = uploadDlg.files;
      // hand the files over to the persistent tray and close the dialog —
      // the batch uploads file-by-file in the background
      enqueueUploadJobs(dropped.map((f) => [f, folder, chat]));
      uploadDlg.busy = false;
      uploadDlg.files = [];
      uploadDlg.open = false;
      filesFolderDraft.value = folder;
    }
    /* real batch upload: files of one batch run SEQUENTIALLY (one HTTP upload
    at a time) while every file keeps its own tray row with its own percent;
    the row badge shows «فایل k از n». uploadChain tail-queues so a new batch
    never interleaves with an in-flight one. */
    let uploadChain = Promise.resolve();
    function enqueueUploadJobs(pairs) {
      const total = pairs.length;
      let ordinal = 0;
      for (const [f, folder, chat] of pairs) {
        const job = { id: ++uploadJobSeq, name: f.name, folder, chat, pct: 0, speed: 0, status: "uploading", error: "", controller: new AbortController(), batchTotal: total, batchIndex: ++ordinal };
        uploadJobs.value.push(job);
        uploadChain = uploadChain.then(() => runUploadJob(job, f));
      }
    }
    async function runUploadJob(job, file) {
      if (job.status === "canceled") return; // cancelled while still queued
      job.started = true;
      try {
        const fid = await doUpload(file, job.folder, job.chat, job);
        job.status = "done";
        job.pct = 100;
        showToast("صف شد: " + fid);
        loaders.files();
      } catch (e) {
        if (job.status === "canceled") return; // user-initiated abort
        job.status = "error";
        job.error = e.message || "آپلود ناموفق";
      }
    }
    async function doUpload(file, folderPath, storageChat, job = null) {
      // Create upload session (original filename is kept verbatim; the server
      // only strips unsafe chars — no renaming)
      const ctrl = job ? job.controller : null;
      const r = await fetch("/api/v1/files/upload/session", {
        method: "POST",
        signal: ctrl ? ctrl.signal : undefined,
        headers: { "Authorization": "Bearer " + token.value, "Content-Type": "application/json", ...(folderPath ? { "X-Folder": folderPath } : {}), ...(storageChat ? { "X-Storage-Chat": storageChat } : {}) },
        body: JSON.stringify({ name: file.name, size: file.size, mime: file.type || "application/octet-stream" })
      });
      if (!r.ok) { const d = await r.json(); throw new Error(d.detail || "session create failed"); }
      const { session_id, chunk_size, offset } = await r.json();
      if (job) {
        job.sessionId = session_id;
        // cancel arrived before the session existed → close it server-side right away
        if (job.status === "canceled") {
          api(`/api/v1/files/upload/session/${session_id}`, { method: "DELETE" }).catch(() => {});
          throw new DOMException("aborted", "AbortError");
        }
      }
      const t0 = Date.now();
      let completed = false;
      let currentOffset = offset;

      // Upload chunks; the completing chunk response carries the queued file_id
      let result = null;
      while (!completed) {
        if (ctrl && ctrl.signal.aborted) throw new DOMException("aborted", "AbortError");
        const chunk = file.slice(currentOffset, Math.min(currentOffset + chunk_size, file.size));
        const r = await fetch("/api/v1/files/upload/session/" + session_id, {
          method: "PATCH",
          signal: ctrl ? ctrl.signal : undefined,
          headers: { "Authorization": "Bearer " + token.value, "X-Offset": currentOffset },
          body: chunk
        });
        if (!r.ok) {
          const d = await r.json(); throw new Error(d.detail || "chunk upload failed");
        }
        result = await r.json();
        currentOffset = result.offset;
        completed = result.completed;
        // live progress + speed from the actual session offset (per-job when
        // running from the tray, legacy globals kept for the inline bar)
        const pct = file.size ? Math.min(100, Math.round((currentOffset / file.size) * 100)) : 100;
        const secs = (Date.now() - t0) / 1000;
        const speed = secs > 0.3 ? (currentOffset / 1048576 / secs) : 0;
        if (job) { job.pct = pct; job.speed = speed; }
        else { uploadPct.value = pct; uploadSpeed.value = speed; }
      }

      // the completing chunk response carries the queued file_id
      return result.file_id;
    }
    async function startResumableUpload(file) { return { file_id: await doUpload(file, filesFolderDraft.value.trim(), "") }; }
    function cancelCurrentUpload() {
      if (cancelXHR && cancelXHR.readyState < 4) {
        cancelXHR.abort();
        showToast("آپلود لغو شد", 2000);
      }
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
        const last = files[0]; // list is newest-first
        const content = await fetch("/api/v1/files/" + last.id + "/content");
        if (!content.ok) { showToast("خطا در خوانش", 2000); return; }
        showToast("فایل خوانده شد: " + last.name, 2000);
        const blob = await content.blob();
        const text = await blob.slice(0, 512 * 1024).text().catch(() => "");
        if (text && confirm("محتوای متنی تشخیص داده شد. در پنجره پیش‌نمایش باز شود؟")) {
          const kind = (last.mime || "").startsWith("text/") || /\.txt$|\.json$|\.md$/i.test(last.name || "") ? "text" : "text";
          Object.assign(previewDlg, { open: true, id: last.id, name: last.name, mime: last.mime || "text/plain", size: last.size, kind, url: "", text: text.slice(0, 20000) });
          showToast("محتوا برای بازبینی در پیش‌نمایش باز شد", 3000);
        }
      } catch (e) { showToast("خطا: " + e.message, 4000, true); }
    }

    /* ---------- queue ---------- */
    async function qPause() { await api("/api/v1/queue/pause", { method: "POST", json: { kind: "upload" } }); showToast("آپلود متوقف شد"); loaders.queue(); }
    async function qResume() { await api("/api/v1/queue/resume", { method: "POST", json: { kind: "upload" } }); showToast("آپلود ادامه یافت"); loaders.queue(); }
    async function qResumeJob(j) { await api(`/api/v1/queue/resume/${j.id}`, { method: "POST" }); showToast(j.resumed_offset ? `آپلود از offset ${j.resumed_offset} ادامه یافت` : `آپلود ادامه یافت`); loaders.queue(); }
    async function qPurge() { await api("/api/v1/queue/purge", { method: "POST" }); loaders.queue(); }
    async function backupDB() {
      try {
        showToast("در حال تهیه بکاپ…", 4000);
        const d = await api("/api/v1/admin/backup");
        // decode the base64gzip payload server-side format: {ts, tables, data}
        const bin = atob(d.data);
        const bytes = new Uint8Array(bin.length);
        for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
        const blob = new Blob([bytes], { type: "application/gzip" });
        const a = document.createElement("a");
        a.href = URL.createObjectURL(blob);
        a.download = "tgdrive-backup-" + new Date(d.ts * 1000).toISOString().replace(/[:.]/g, "-") + ".json.gz";
        a.click();
        setTimeout(() => URL.revokeObjectURL(a.href), 5000);
        showToast("بکاپ دانلود شد");
      } catch (e) { showToast("خطا: " + e.message, 4000, true); }
    }
    async function restoreDB(ev) {
      const f = (ev && ev.target && ev.target.files && ev.target.files[0]) || (document.getElementById('restoreInput') || {}).files?.[0];
      if (!f) return;
      if (!confirm("بازگردانی از " + f.name + "؟ ردیف‌های موجود بازنویسی نمی‌شوند (ادغام).")) { ev.target.value = ""; return; }
      try {
        const buf = new Uint8Array(await f.arrayBuffer());
        // the panel may receive either the raw gzip (from backupDB) or the API json envelope
        let payload = "";
        try {
          const ds = new DecompressionStream("gzip");
          const stream = new Blob([buf]).stream().pipeThrough(ds);
          payload = btoa(String.fromCharCode(...new Uint8Array(await new Response(stream).arrayBuffer())));
        } catch (e) {
          // not gzip — maybe the user picked the API's json envelope
          const text = new TextDecoder().decode(buf);
          const j = JSON.parse(text);
          payload = j.data || "";
          if (!payload) throw e;
        }
        const res = await api("/api/v1/admin/restore", { method: "POST", json: { data: payload } });
        const restored = Object.entries(res.restored || {}).map(([k, v]) => k + ": " + v).join(", ");
        showToast("بازگردانی انجام شد (" + (restored || "بدون ردیف") + ")", 6000);
      } catch (e) { showToast("خطا در بازگردانی: " + e.message, 5000, true); }
      finally { const inp = document.getElementById('restoreInput'); if (inp) inp.value = ""; }
    }

    /* ---------- boot ---------- */
    onMounted(() => { if (token.value) { switchTab("dash"); checkSetup(); } });

// bindings auto-exposed by script setup

</script>
