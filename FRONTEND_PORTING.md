# Frontend Porting Contract — static/index.html → Vue build

پنل فعلی **Vue 3 runtime** است (`static/index.html` + `static/app.js` با vendored
`vue.global.prod.js`). چون `index.html` خودش template کامپایل‌شده Vue است، هر
چیزی که آنجا اضافه می‌شود **به‌طور خودکار** در خروجی build آینده همان را می‌دهد.

قاعده: **منبع حقیقت = `static/index.html` + `static/app.js`**. هیچ UI ای فقط در
لایه دیگری اضافه نشود.

## قانون طلایی برای تغییرات آینده

هر UI جدید/تغییری مستقیماً در `static/index.html` (markup) و `static/app.js`
(logic) اعمال می‌شود و چون همان فایل‌ها ورودی build هم هستند، خروجی build با
نسخه‌ی runtime هیچ تفاوتی نخواهد داشت.

## در زمان مهاجرت به build (Vite/Vue-CLI)

- `static/index.html` → `src/App.vue` (markup، بدون تغییر منطقی؛ فقط `#app` mount)
- `static/app.js` → `src/main.js` + composables (`api`, `toast`, loaders)
- نکته: `app.js` با `Vue.createApp` از global build استفاده می‌کند؛ در build از
  `import { createApp, ref, reactive, computed } from 'vue'` استفاده می‌شود.
- **`v-cloak`**: در runtime چشمک اولیه را می‌گیرد؛ در build جایگزین می‌شود با
  آپشن `compilerOptions.isCustomElement` برای المان‌های غیر Vue (در صورت وجود).
- توکن‌های CSS (`style.css`) عیناً منتقل می‌شوند (همان design tokens).

## سازگاری باینری ویژگی‌های فعلی با build

| ویژگی | وضعیت در build آینده |
|-------|----------------------|
| تب‌ها، دیالوگ‌ها، toast | همان markup → کار می‌کند |
| تنظیمات + اعتبارسنجی live | همان markup → کار می‌کند |
| ویزارد استارتر | همان markup → کار می‌کند |
| پراکسی‌ها | همان markup → کار می‌کند |
| آپلود resumable (XHR/fetch) | همان توابع → کار می‌کند |
| QR/share | همان markup → کار می‌کند |

## نگه‌داشتن دو خروجی همگام (اگر هر دو لازم شدند)

1. هیچ UI ای بیرون از `index.html`/`app.js` اضافه نکنید.
2. در صورت افزودن کامپوننت SFC در آینده، آن را **از روی `index.html`** بنویسید
   (markup را کپی نکنید — منتقل کنید و `index.html` را مرجع نگه دارید).
3. قبل از کامیت UI جدید، `node --check static/app.js` + یک smoke مرورگری.
