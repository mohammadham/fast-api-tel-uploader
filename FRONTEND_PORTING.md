# Frontend Contract — Vite + SFC panel

مهاجرت انجام شد ✅. پنل الان **Vite + Vue 3 SFC** است:

- **منبع حقیقت:** `panel/src/App.vue` (markup + logic)، `panel/src/main.js`،
  `panel/src/style.css`، `panel/index.html` (entry Vite)
- **خروجی سرو‌شده:** `static/index.html` + `static/assets/*` (ساخت `npm run build`
  در `panel/`؛ outDir = `../static` با base `/static/`)
- **مرجع تاریخی:** `panel/legacy/runtime-app.js` و `panel/legacy/vue.global.prod.js`
  (نسخه runtime قبلی، برای مقایسه/rollback نگه داشته می‌شود؛ دیگر سرو نمی‌شود)

## گردش کار تغییرات UI

```bash
cd panel
npm run dev        # dev server (پروکسی دستی به بک‌اند لازم نیست؛ API مستقیم صدا زده می‌شود)
npm run build      # خروجی → ../static (index.html + assets هش‌شده)
```

بعد از هر تغییر: `npm run build` بزنید تا `static/` به‌روز شود — FastAPI همان
`static/index.html` و `static/assets/*` را سرو می‌کند و commit باید شامل
هم منابع (`panel/`) و هم خروجی (`static/`) باشد.

## قواعد

1. UI فقط از طریق `panel/src/App.vue` تغییر کند؛ هرگز `static/index.html` یا
   `static/assets/*` را دستی ویرایش نکنید (فایل‌های هش‌دار rebuild می‌شوند).
2. `${...}` داخل `<template>` ممنوع است (تداخل با delimiterهای Vue — CI گارد دارد).
3. `style.css` توکن‌های دیزاین (dark #0F172A، accent #22C55E، Vazirmatn) را
   نگه می‌دارد؛ تغییر تم فقط از این فایل.
4. `panel/node_modules` کامیت نمی‌شود؛ `package-lock.json` بله.

## نکته‌های مهاجرت (برای مرور)

- اسکریپت `setup()` با `<script setup>` بازنویسی شد: بلاک `return {...}` حذف و
  importها از `vue` اضافه شد (قبلاً از global `Vue` می‌آمد).
- نام‌های alias شده در `return` قدیمی: `doLogin: submitLogin` → در SFC
  `const doLogin = submitLogin;` بعد از تعریف تابع.
- `v-cloak` در entry HTML حفظ شد (بدون آسیب در build).
