# ✅ TODO — TelegramDrive

## فاز ۰ — زیرساخت ✅
- [x] تحقیق محدودیت‌های تلگرام + کتابخانه‌ها
- [x] پلن ۵ دور بازبینی‌شده (PLAN.md)
- [x] DECISIONS.md و GRAPH.md
- [x] pyproject.toml + requirements.txt + .env.example + .gitignore + .dockerignore

## فاز ۱ — امنیت و مدل‌ها ✅
- [x] app/core/config.py (pydantic-settings)
- [x] app/core/db.py (SQLite WAL + schema کامل: users, api_keys, tg_accounts, bot_tokens, files, file_parts, jobs, audit_log, upload_sessions)
- [x] app/core/security.py (scrypt, JWT, API Key td_*, Fernet, HMAC presign)
- [x] app/core/models.py + repos کامل
- [x] app/core/rate_limit.py (token bucket + daily quota)
- [x] app/core/metrics.py (Prometheus بدون وابستگی)
- [x] app/core/state.py

## فاز ۲ — تلگرام ✅
- [x] app/tg/base.py (BackendClient + FloodWait/SendFailure/TransferError)
- [x] app/tg/fast_transfer.py (دانلود سگمنتی موازی + stream_range با align 4096)
- [x] app/tg/telethon_backend.py (MTProto + BotBackend HTTP)
- [x] app/tg/manager.py (استخر + سقف همزمانی + flood isolation + circuit breaker)
- [x] app/tg/bot_service.py (polling getUpdates + فرمان‌ها + delivery)
- [x] app/tg/fake.py (FakeTelegram برای تست/dev)

## فاز ۳ — صف اولویت‌دار ✅
- [x] app/queue/queue_manager.py (heap −prio/seq + پایداری SQLite + recovery)
- [x] دانلود(100) > آپلود(40) > حذف(10) + جلوگیری از گرسنگی آپلود در DI worker
- [x] FloodWait handling + retry نمایی (2s→60s) + max retries
- [x] پارت‌بندی خودکار فایل >1.9GB (splitter داخلی handler آپلود)
- [x] app/services/streaming.py (Range/206/HEAD/416 + merge چندپارته + ETag)
- [x] app/services/presign.py (HMAC + انقضا + one-time jti)
- [x] app/services/janitor.py (tmp، سشن منقضی، jobهای قدیمی)

## فاز ۴ — API ✅
- [x] app/api/auth.py (login/refresh/me + ضد brute-force)
- [x] app/api/accounts.py (ورود چندمرحله‌ای phone→code→2FA + CRUD + test)
- [x] app/api/bots.py (افزودن با getMe + toggle/delete)
- [x] app/api/keys.py (ساخت یک‌بارنمایش + revoke + patch سهمیه)
- [x] app/api/files.py (upload + resumable session + info + delete + link + content + /d/ عمومی)
- [x] app/api/queue.py (stats/jobs/pause/resume/retry/purge)
- [x] app/api/admin.py (overview/audit/metrics/healthz/readyz)
- [x] app/main.py (lifespan کامل + graceful shutdown + bootstrap ادمین)

## فاز ۵ — پنل وب ✅
- [x] static/index.html + style.css + app.js (RTL، دارک)
- [x] تب‌ها: داشبورد، اکانت‌ها، بات‌ها، کلیدها، فایل‌ها، صف، لاگ
- [x] مودال افزودن اکانت (phone→code→2FA) و بات و کلید
- [x] آپلود از پنل + کپی لینک + pause/resume صف

## فاز ۶ — Docker + تست ✅
- [x] Dockerfile (slim + healthcheck + non-root)
- [x] docker-compose.yml (+ profile localbot برای بات ۲GB)
- [x] tests/conftest.py (FakeTG + JWT + کلید)
- [x] tests/test_security.py (۹ تست)
- [x] tests/test_queue.py (اولویت DL>UL)
- [x] tests/test_api.py (۶ چرخه کامل API)
- [x] tests/test_streaming.py (parse_range)
- [x] tests/test_persistence.py (بازیابی پس از ری‌استارت)
- [x] tests/test_concurrency.py (آپلود موازی + rate limit)
- [x] README.md کامل (fa)
- [x] اجرای تست‌ها — ۲۴/۲۴ پاس ✅
- [x] smoke-test با uvicorn واقعی ✅

## فاز ۷ — سخت‌سازی/ارتقای استاندارد (۲۰۲۶-۰۹) ✅
- [x] logout واقعی + blacklist refresh token (جدول revoked_tokens)
- [x] Security headers middleware + no-store روی API
- [x] Audit کامل: لاگین موفق/ناموفق + همه‌ی اکشن‌های مدیریتی (constants AUDIT_*)
- [x] سخت‌سازی آپلود resumable (413 oversize، 400 X-Offset بد)
- [x] باگ استریم: release با خطای واقعی + شمارش بایت سروشده (bytes_sent)
- [x] round-robin واقعی در استخر اکانت‌ها + acquire_key برای تست اکانت مشخص
- [x] disable خودکار اکانت با سشن باطل (AuthKeyUnregistered و …) + حذف از استخر
- [x] cooldown اضافه برای flood چندباره + ترمیم status در mark_success
- [x] endpoint ریست اکانت (پاک‌سازی flood/circuit + reconnect) + دکمه در پنل
- [x] هندل PhoneCodeExpired/PhoneCodeInvalid/PasswordHashInvalid/PhoneNumberInvalid/Banned/FloodWait در لاگین
- [x] GC باکت‌های rate-limiter (ضد رشد نامحدود per-IP)
- [x] ایندکس‌های file_parts و audit_log (⚠️ بعد از CREATE TABLE)
- [x] تست‌های جدید: tests/test_hardening.py (۱۱ تست) → کل ۳۵/۳۵ پاس ✅

## فاز ۸ — Observability (لاگ ساختاریافته + Correlation) ✅
- [x] app/core/obs.py: JsonFormatter + ContextFilter + contextvars (request/correlation/job id)
- [x] ObservabilityMiddleware: ساخت/echo شناسه‌ها + access log JSON با duration
- [x] هدرهای X-Request-ID / X-Correlation-ID در پاسخ‌ها
- [x] ستون correlation_id در jobs + propagation از HTTP به صف و workerها
- [x] لاگ‌های رویدادی صف: enqueued/done/crashed/upload complete/bot download/file deleted
- [x] TGDRIVE_LOG_LEVEL + README (بخش لاگ ساختاریافته) + .env.example
- [x] tests/test_observability.py (۷ تست) → کل ۴۲/۴۲ پاس ✅

## فاز ۹ — بازطراحی پنل با دیزاین‌سیستم (ui-ux-pro-max) ✅
- [x] design-system/telegramdrive/MASTER.md (پالت OLED + تایپ + چک‌لیست)
- [x] style.css بازنویسی با توکن‌های دیزاین‌سیستم + focus-visible + reduced-motion + ریسپانسیو
- [x] index.html: لیبل visible، SVG logo، role=alert/aria-live، autocomplete درست
- [x] app.js: toast با نوع error + disabled حین آپلود
- [x] باگ‌های کشف‌شده: تب‌های همزمان نمایش داده می‌شدند (نبود display:none)، مودال‌های بسته نمایان بودند (dialog:flex override)
- [x] تأیید بصری با preview زنده: لاگین→داشبورد→مودال کلید→تب‌ها، کنسول پاک ✅

## فاز ۱۰ — دسته P0 رقابتی ✅
- [x] ROADMAP.md (۴۰ فیچر از تحقیق GoFile/Pixeldrain/Streamtape و…)
- [x] جدول links (slug/pwd_hash/max_downloads/hits) + LinkRepo
- [x] POST /files/{id}/share + صفحه عمومی /{slug} + /d/{slug}/dl با رمز و سقف
- [x] صفحه‌ی پلیر ویدیو/صدا + تامب‌نیل تلگرام (/{slug}/thumb)
- [x] Trash (حذف نرم) + restore + purge صریح + janitor ۷ روزه
- [x] notify_admins برای خطاهای صف (تلگرام)
- [x] پنل: لینک عمومی با اسلاگ/رمز + بازیابی زباله‌دان
- [x] tests/test_p0_features.py (۸ تست) → کل ۵۰/۵۰ پاس ✅

## فاز ۱۱ — QR Code لینک عمومی ✅
- [x] وابستگی qrcode (SVG بدون PIL) + GET /{slug}/qr (کش ۲۴h)
- [x] QR در صفحه عمومی (details تاشو) + مودال QR در پنل بعد از ساخت لینک
- [x] +۲ تست → کل ۵۲/۵۲ پاس ✅ + تأیید زنده با curl و preview

## ایده‌های نسخه بعد (v2)
- [ ] Redis backend برای صف (scale-out چندپروسه‌ای)
- [ ] لایه کش دیسکی/CDN جلوی تلگرام برای فایل‌های پرمصرف
- [ ] WebAuthn/OTP برای پنل
- [ ] تامب‌نیل ویدیویی (ffmpeg)
- [ ] کلاینت CLI رسمی (tgdrive-cli)
- [ ] چند-نودی با exclusive lock

## فاز ۱۲ — بک‌اند ایتا (D20) ✅
- [x] تحقیق API ایتایار (فقط sendFile/sendMessage — دانلود=اسکرپ، حذف=ندارد)
- [x] EitaaBackend + استخر eit: در manager + پین‌کردن acquire با backend
- [x] schema: eitaa_accounts + files.backend + api_keys.backend (مهاجرت خودکار)
- [x] مسیریابی صف/استریم/حذف از رکورد فایل؛ اسنپ‌شات در enqueue
- [x] TGDRIVE_DEFAULT_BACKEND + override کلیدی
- [x] پنل: تب ایتا + انتخاب ذخیره‌سازی در کلید + ستون ذخیره در فایل‌ها
- [x] ۸ تست جدید → ۶۰/۶۰
