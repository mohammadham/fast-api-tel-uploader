# پلن بازنویسی فرانت‌اند با Vue 3 — TelegramDrive

## تصمیم معماری (ponytail ladder)

1. **Vue 3 بدون build step** — فایل `static/vendor/vue.global.prod.js` (146KB) به‌صورت vendor شده.
   - چرا نه Vite/CLI؟ پروژه Python است، هیچ Node dependency ندارد، و یک پنل ادمین کوچک است. build step یعنی Node toolchain برای یک SPA سه‌فایلی — سربار بی‌مورد.
   - چرا Vue نه React/Preact؟ کاربر صریحاً Vue خواسته. Vue global build با template در HTML بدون JSX/Babel کار می‌کند.
2. **تک‌فایل app.js** — کل state و logic در یک `createApp({...})`. بدون router (تب‌ها state داخلی)، بدون pinia (یک استور)، بدون axios (fetch موجود).
3. **index.html = قالب Vue** — تمام markup داخل `#app` با directiveهای Vue (`v-if`, `v-for`, `@click`, `v-model`). بدون innerHTML دستی.

## ویژگی‌های حفظ‌شده (feature parity)

| بخش | قابلیت‌ها |
|---|---|
| Landing/Login | فرم لاگین، خطای فارسی، ذخیره token/refresh در localStorage |
| Dashboard | کارت‌های آمار، سلامت اکانت‌ها، flood-warn |
| اکانت‌ها | لیست، فعال/غیرفعال، تست، ریست، حذف، لاگین دومرحله‌ای (step1: phone → step2: code+2FA) |
| بات‌ها | لیست، افزودن توکن، toggle، حذف |
| ایتا | لیست، افزودن (token+chat)، toggle، تست، حذف |
| کلیدها | لیست، ایجاد (name/scopes/rpm/quota/backend)، نمایش یک‌باره کلید + کپی، revoke |
| فایل‌ها | لیست، آپلود با progress، لینک عمومی (slug/password)، QR dialog، soft-delete/restore/purge |
| صف | آمار، pause/resume/purge، جدول jobها |
| Audit | جدول لاگ |
| عمومی | toast، esc()، fmtBytes()، badge()، refresh خودکار token |

## ساختار state (ref/reactive)

```
token, refresh        ← localStorage
tab                   ← تب فعال
login: {user, pass, err, busy}
dash: {cards, health}
accounts, bots, eitaas, keys, files, jobs, audit  ← آرایه‌ها
accDlg: {open, step, phone, label, code, pass, msg, loginId}
botDlg: {open, token, label, msg}
eitDlg: {open, token, chat, label, msg}
keyDlg: {open, name, scopes, rpm, quota, backend, result}
qrDlg: {open, url, slug}
toast: {msg, err}
uploadProgress
```

## مراحل

1. ✅ vendor کردن `vue.global.prod.js`
2. بازنویسی `index.html` — قالب کامل Vue در `#app`
3. بازنویسی `app.js` — `createApp` با reactive state، متدها، `mounted` (boot)
4. حذف اسکریپت inline تکراری از index.html (قبلاً جایگزین app.js شده بود)
5. اجرای سرور fake-TG و تست زنده: لاگین → داشبورد → هر تب → دیالوگ‌ها
6. بررسی کنسول مرورگر

## ریسک‌ها

- `v-model` روی dialog داخل `<dialog>` native — از `:open` + watch استفاده می‌کنیم نه showModal دستی.
- XSS: Vue خودش interpolate را امن می‌کند (`{{ }}` بدون v-html).
- فرم لاگین: `@submit.prevent` به‌جای addEventListener.
