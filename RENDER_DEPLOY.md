# 🚀 راه‌اندازی TelegramDrive در Render

این راهنما اپ را روی **Render** با **PostgreSQL مدیریت‌شده** بالا می‌آورد — همان معماری چندسروره‌ای که در پروژه پیاده شده (دیتابیس مشترک + صف توزیع‌شده + رجیستری نودها).

---

## ۰) پیش‌نیازها — قبلاً در کد انجام شده ✅

| # | مورد | توضیح |
|---|------|-------|
| ۱ | **پورت داینامیک** | Render متغیر `PORT` را تزریق می‌کند؛ CMD هر دو Dockerfile الان `sh -c "... --port ${PORT:-8000}"` است |
| ۲ | **`sslmode=require` در URL پستگرس Render** | `PgDatabase.normalize_url` آن را حذف و به `ssl=True` برای asyncpg ترجمه می‌کند (`app/core/db.py`) |
| ۳ | **کلید Fernet با فرمت آزاد** | secret تولیدشده Render هر فرمتی داشته باشد، `security.py` آن را به کلید معتبر مشتق می‌کند |
| ۴ | **اسکیمای کامل PG** | همه جداول (`files`, `jobs`, `settings`, `nodes`, …) به‌طور خودکار در اولین استارت ساخته می‌شوند |
| ۵ | **Blueprint** | فایل `render.yaml` آماده است (وب‌سرویس + دیتابیس) |

---

## ۱) پیش‌نیازهایی که خودتان باید تهیه کنید

- اکانت Render (رایگان شروع می‌کند؛ برای همیشه‌روشن بودن سرویس وب، پلن پولی لازم است)
- کد روی **GitHub/GitLab** push شده باشد
- (اختیاری، برای آپلود واقعی) `api_id` و `api_hash` از [my.telegram.org](https://my.telegram.org) و یک اکانت تلگرام برای لاگین
- (برای بک‌اند ایتا) توکن از [eitaayar.ir](https://eitaayar.ir) + کانال عمومی

> **تست بدون تلگرام:** اگر فقط می‌خواهید اپ را بالا ببینید، `TGDRIVE_FAKE_TG=1` بگذارید — بک‌اند جعلی درون‌حافظه‌ای فعال می‌شود و آپلود/دانلود بدون اکانت واقعی کار می‌کند.

---

## ۲) استقرار با Blueprint (پیشنهادی)

1. داشبورد Render → **New +** → **Blueprint**
2. ریپازیتوری را انتخاب کنید (فایل `render.yaml` خودکار شناسایی می‌شود)
3. Render این‌ها را می‌سازد:
   - وب‌سرویس Docker به نام `telegramdrive`
   - دیتابیس Postgres به نام `telegramdrive-db`
   - `TGDRIVE_DATABASE_URL` به‌طور خودکار به وب‌سرویس تزریق می‌شود
4. برای متغیرهایی که `sync: false` هستند (`TGDRIVE_ADMIN_PASSWORD`, `TGDRIVE_API_ID`, `TGDRIVE_API_HASH`) مقدار وارد کنید یا خالی بگذارید
5. **Apply** → دیپلوی شروع می‌شود (~۳-۵ دقیقه build)

### استقرار دستی (بدون Blueprint)
- **New + → PostgreSQL** → بعد **New + → Web Service** → ریپو → Runtime: **Docker** → Dockerfile Path: `./Dockerfile`
- در تب **Environment**، متغیر `TGDRIVE_DATABASE_URL` را با **Internal Database URL** دیتابیس پر کنید (بقیه را جدول پایین ببینید)

---

## ۳) متغیرهای محیطی

### ضروری
| متغیر | مقدار | توضیح |
|-------|-------|-------|
| `TGDRIVE_DATABASE_URL` | Internal Database URL | از دیتابیس Render کپی کنید (با `?sslmode=require`) |
| `TGDRIVE_SECRET` | رشته تصادفی | secret مشترک JWT/لینک‌ها — روی همه نودها **یکسان** |
| `TGDRIVE_FERNET_KEY` | رشته تصادفی | رمزنگاری سشن‌های تلگرام در DB — روی همه نودها **یکسان** |
| `TGDRIVE_ADMIN_PASSWORD` | رمز دلخواه | خالی = تولید خودکار (پیدا کردنش: بند ۵) |

### تلگرام (برای آپلود واقعی)
| متغیر | مقدار |
|-------|-------|
| `TGDRIVE_API_ID` | از my.telegram.org |
| `TGDRIVE_API_HASH` | از my.telegram.org |

### پیشنهادی
| متغیر | پیش‌فرض | توضیح |
|-------|---------|-------|
| `TGDRIVE_FAKE_TG` | `0` | `1` = بک‌اند جعلی برای تست بدون تلگرام |
| `TGDRIVE_NODE_ID` | hostname | در دیپلوی چندنودی منحصربه‌فرد کنید |
| `TGDRIVE_DEFAULT_BACKEND` | `telegram` | یا `eitaa` |
| `TGDRIVE_LOG_LEVEL` | `INFO` | `DEBUG` برای عیب‌یابی |
| `TGDRIVE_REDIS_URL` + `TGDRIVE_REDIS_ENABLED` | خالی/`false` | بعد از اضافه‌کردن Redis Render، صف را سریع‌تر می‌کند (اختیاری) |

> بقیه محدودیت‌ها (`max_upload_size`، ورکرها، TTLها و…) لازم نیست env بزنید — از **تب تنظیمات پنل** بعداً به‌صورت runtime تنظیم می‌شوند (اولویت DB > env).

---

## ۴) اولین اجرا و ویزارد استارتر

1. بعد از دیپلوی موفق، URL سرویس را باز کنید (`https://telegramdrive-xxxx.onrender.com`)
2. با `admin` + رمز (env یا تولیدشده) وارد شوید
3. **ویزارد راه‌اندازی اولیه خودکار باز می‌شود**:
   - مرحله ۱: تغییر رمز ادمین (پیشنهاد: حتماً)
   - مرحله ۲: افزودن اکانت تلگرام/بات/ایتایار از تب‌های مربوطه
   - مرحله ۳: انتخاب بک‌اند پیش‌فرض → **تکمیل راه‌اندازی**
4. تب **تنظیمات**: محدودیت‌ها را runtime تنظیم کنید؛ در جدول «نودهای متصل» باید `render-node-1` با ضربان تازه ببینید

---

## ۵) پیدا کردن رمز ادمین تولیدشده

اگر `TGDRIVE_ADMIN_PASSWORD` را خالی گذاشتید:
- **Logs** سرویس را ببینید: `generated admin password stored at ...`
- یا **Shell** سرویس: `cat /srv/data/initial_admin_password.txt`

---

## ۶) مقیاس چندنودی روی Render

برای نود دوم، یک وب‌سرویس جدید از همین ریپو بسازید و **فقط این‌ها** را متفاوت بگذارید:
- `TGDRIVE_NODE_ID=render-node-2`
- همان `TGDRIVE_DATABASE_URL`، `TGDRIVE_SECRET` و `TGDRIVE_FERNET_KEY` (مشترک — الزامی)

هر دو نود در تب تنظیمات → «نودهای متصل» دیده می‌شوند؛ صف جاب‌ها با claim اتمیک + lease بین نودها تقسیم می‌شود.

> ⚠️ **محدودیت دیسک:** جاب‌های آپلود به نود مبدأ چسبیده‌اند (فایل موقت روی دیسک همان نود می‌ماند). دیسک Render ephemeral است؛ برای فایل‌های بزرگ یا ماندگاری tmp، دیسک Render (Disk) به سرویس اضافه کنید و `TGDRIVE_DATA_DIR` را به مسیر mount بدهید.

---

## ۷) هزینه و پلن‌ها (نمونه)

| مورد | پلن | حدود هزینه ماهانه |
|------|-----|------------------|
| وب‌سرویس | starter (512MB) | ~$7 |
| PostgreSQL | basic-256mb | ~$6 |
| (اختیاری) Redis | key-value 25MB | ~$5 |

برای تست اولیه می‌توانید فقط وب‌سرویس + free Postgres (انقضا ۳۰ روزه) را بردارید.

---

## ۸) عیب‌یابی

| علامت | علت/راه‌حل |
|-------|-----------|
| Build fail روی `asyncpg` | مطمئن شوید `requirements.txt` همین نسخه را دارد؛ Render از Dockerfile build می‌کند نه pip مستقیم |
| کرش در استارت با `invalid connection URI` | URL دیتابیس باید با `postgres://` یا `postgresql://` شروع شود؛ `sslmode` خودکار هندل می‌شود |
| `health check failed` | مسیر health: `/api/v1/admin/healthz` — اگر ۵۰۳ دیدید، لاگ‌ها را ببینید (معمولاً DB در دسترس نیست) |
| لاگین نمی‌شود | رمز env با چیزی که وارد می‌کنید فرق دارد؛ طبق بند ۵ رمز تولیدشده را پیدا کنید یا env را عوض و redeploy کنید |
| آپلود می‌گیرد ولی جاب fail می‌شود | بدون اکانت تلگرام طبیعی است؛ `TGDRIVE_FAKE_TG=1` برای تست، یا اکانت اضافه کنید |
| «session decrypt failed» بعد از تغییر secret | `TGDRIVE_SECRET`/`TGDRIVE_FERNET_KEY` را عوض نکنید بعد از افزودن اکانت — سشن‌ها رمزنگاری‌شده با همان کلید ذخیره شده‌اند |

---

## ۹) چک‌لیست سریع تایید استقرار

```bash
# سلامت
curl https://<your-app>.onrender.com/api/v1/admin/healthz   # {"ok":true}

# لاگین + خواندن تنظیمات
TOKEN=$(curl -s -X POST https://<your-app>.onrender.com/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"<pass>"}' | python -c "import sys,json;print(json.load(sys.stdin)['access_token'])")
curl -s https://<your-app>.onrender.com/api/v1/admin/settings -H "Authorization: Bearer $TOKEN" | head -c 300

# آپلود آزمایشی (با FAKE_TG یا اکانت واقعی)
curl -s -X POST https://<your-app>.onrender.com/api/v1/keys \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"name":"test"}'
```

سپس از پنل: تب تنظیمات → ذخیره یک مقدار → نشان «ذخیره‌شده» = چرخه DB→پنل→DB سالم است. ✅
