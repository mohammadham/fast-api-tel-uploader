# 🧠 DECISIONS.md — حافظه تصمیمات (Remember File)

> هر جا برای ادامه‌ی کار به زمینه نیاز بود، این فایل مرجع تصمیم‌های گرفته‌شده است.

## D1 — انتخاب کتابخانه MTProto
**تصمیم:** Telethon + StringSession + FastTelethon (embed شده در repo)
**چرا:** پایدارترین کتابخانه MTProto پایتون، FastTelethon انتقال موازی chunk می‌دهد، StringSession نیازی به فایل سشن ندارد (استخر در حافظه ممکن می‌شود). Pyrogram نگهداری‌اش متوقف/ناپایدار شده، tdlib سنگین است.

## D2 — بات
**تصمیم:** aiogram 3 فقط برای فایل ≤50MB و رابط مدیریتی.
**چرا:** Bot API محدودیت 20MB دانلود و 50MB آپلود دارد؛ سرور محلی bot-api (تا 2GB) به‌صورت compose profile اختیاری است، پیش‌فرض خاموش.

## D3 — صف
**تصمیم:** صف داخلی asyncio.PriorityQueue + پایدارسازی روی SQLite (WAL) + بازیابی در startup.
**چرا:** بدون وابستگی به Redis؛ برای مقیاس هدف (~500 job/min) کافی است. Redis فقط در roadmap v2 برای scale-out چندپروسه‌ای.
**اولویت‌ها:** DOWNLOAD=100 > STREAM=60 > UPLOAD=40 > MIGRATE=20 > HOUSEKEEPING=10. در اولویت برابر FIFO (sub-order = seq).

## D4 — ذخیره‌سازی در تلگرام
**تصمیم:** هر اکانت به کانال ذخیره‌سازی خصوصی خودش آپلود می‌کند (peer=خود اکانت / کانال private که اکانت admin است) تا PeerFlock رخ ندهد.
**پارت‌بندی:** فایل >1.9GB → تقسیم به پارت‌های ~1.9GB؛ رکورد `file_parts` ترتیب و مرزها را نگه می‌دارد؛ دانلود merge استریمی می‌کند.

## D5 — امنیت سشن
**تصمیم:** StringSession با Fernet (کلید از `TGDRIVE_SECRET`) رمز و در SQLite ذخیره می‌شود. فایل دیتابیس باید chmod 600 باشد (در Docker enforced).

## D6 — احراز هویت
**تصمیم:** پنل = JWT access (15min) + refresh؛ API کلاینت‌ها = API Key با پیشوند `td_` و ذخیره sha256. Scopes: read/write/admin. Quota روزانه + rate limit per key (token bucket).

## D7 — استریم
**تصمیم:** دانلود مستقیم TG→client بدون کش دیسک؛ پشتیبانی Range/206/HEAD/ETag. Presigned link = HMAC(file_id, expiry, one_time?).

## D8 — اولویت دانلود
**تصمیم:** درخواست‌های دانلود همیشه جلوتر از آپلود سرو می‌شوند؛ workerهای دانلود می‌توانند آپلود را موقتاً pause کنند (`queue.pause(kind="upload")`). دانلود استریم فعال (رنج‌های بعدی همان فایل) sub-prio بالاتر می‌گیرد تا استریم قطع نشود.

## D9 — مقاوم‌سازی
**تصمیم:** FloodWait → isolate اکانت تا پایان مدت؛ Job → اکانت دیگر. ۵ خطای متوالی اکانت → circuit breaker ۵ دقیقه. Retry = 5 با backoff نمایی (2s→60s cap). پس از اتمام retry → status=failed + پیام خطا.

## D10 — حالت dev/تست
**تصمیم:** `TGDRIVE_FAKE_TG=1` → FakeTelegram جای Telethon/aiogram (بدون شبکه). تمام تست‌ها با fake کار می‌کنند؛ فقط smoke تست اختیاری با اکانت واقعی.

## D11 — وب
**تصمیم:** FastAPI + Uvicorn (Gunicorn فقط در Docker)، پنل = SPA vanilla JS بدون build step در `static/`، served by FastAPI.

## D12 — پایگاه داده
**تصمیم:** SQLite با WAL + `check_same_thread=False` + قفل نوشتن در سطح app. جدول‌ها: users, api_keys, tg_accounts, bot_tokens, files, file_parts, jobs, audit_log, upload_sessions.

## D13 — نکات اجرایی تلگرام (از تحقیق)
- سقف فایل MTProto: 2GB (4GB پرمیوم) — بات: 50MB آپلود / 20MB دانلود (local server: 2GB)
- FloodWaitError را باید **منتظر ماند** (wait واقعی) وگرنه بن طولانی‌تر می‌شود
- get_file/send به چت ناشناس → PeerFlood → بن؛ فقط به خود/کانال خود اکانت
- سرعت تک‌کانال پایین است → موازی‌سازی chunk (FastTelethon) ضروری
- تعداد پیام بات: 30/s کل، 1/s per chat، گروه ~20/min
- از Feb 2025 تلگرام سرعت آپلود MTProto را کند کرده → چند اکانت + موازی‌سازی بیشتر اهمیت دارد

## D14 — مسیر ادامه کار
اگر این پروژه را بعداً باز کردی: اول TODO.md، بعد PLAN.md بخش ۲ (معماری)، سپس DECISIONS همین فایل. ورودی‌ها فقط از env (`.env.example`) می‌آیند. اجرا: `docker compose up -d` یا `uvicorn app.main:app --port 8000`.

## D15 — دور سخت‌سازی (پس از بازبینی کد ۲۰۲۶-۰۹)
**تصمیم‌های امنیتی:**
- logout واقعی: جدول `revoked_tokens` (jti + انقضا) + `blacklist_refresh`/`refresh_blacklisted` در security.py؛ refresh پس از logout رد می‌شود (تست دارد)
- Security headers با middleware: X-Content-Type-Options/X-Frame-Options/Referrer-Policy/Permissions-Policy؛ پاسخ‌های `/api/*` همیشه `Cache-Control: no-store`
- Audit کامل: `auth.login.ok/fail` (fail فقط از exception-handler برای 401)، `apikey.create/revoke`، `file.upload/delete`، `link.create`، `account.delete/reset`، `bot.add/delete` — ثابت‌ها در models.py (AUDIT_*)
- آپلود resumable سخت‌گیر: chunk بیشتر از size اعلامی → 413؛ `X-Offset` غیرعددی → 400 (قبلاً 500 می‌داد)

**تصمیم‌های استخر و پایداری:**
- `acquire` حالا **round-robin** است (نوبت‌گردی با `_rr`) — توزیع بار واقعی بین اکانت‌ها (تست توزیع ۳ اکانته)
- `acquire_key(key)` جدید برای عملیات روی بک‌اند مشخص (تست سلامت اکانت دقیق) — **همیشه با await صدا زده می‌شود**
- خطاهای دائمی (`AuthKeyUnregistered/Duplicated/Invalid`, `UserDeactivatedBanError`) → status=unauthorized + enabled=0 + **حذف فوری از استخر زنده** + log.error
- FloodWait چندباره (≥۳) → ۵ دقیقه cooldown (علاوه بر صبر اصلی) — شکست‌های per-account در `_note_account_failure` ثبت می‌شوند
- `mark_success` حالا status را هم `ready` برمی‌گرداند (اکانت transient-error سالم، خودش را ترمیم می‌کند)
- `note_bytes`: بایت سروشده‌ی واقعی (از `_ReleaseAndCount.bytes_sent`) به `tg_accounts.bytes_down` — نه حجم مفروض
- endpointهای جدید: `POST /accounts/{id}/test` (دقیق همان اکانت) و `POST /accounts/{id}/reset` (پاک‌سازی flood/circuit + reconnect)

**تصمیم‌های کارایی:**
- GC در RateLimiter (هر 600s، باکت idle >1800s حذف) — جلوگیری از رشد نامحدود per-IP
- ایندکس‌های جدید: `file_parts(file_id, idx)` و `audit_log(ts)` — ⚠️ ایندکس باید **بعد از** CREATE TABLE جدولش در schema بیاید (باگ بود؛ sqlite `no such table` می‌داد)

**تست:** ۳۵/۳۵ (۲۴ قبلی + ۱۱ جدید در `tests/test_hardening.py`)

## D16 — لاگ ساختاریافته + Request/Correlation ID
**تصمیم:** ماژول `app/core/obs.py`:
- همه‌ی لاگ‌ها JSON تک‌خطی (`JsonFormatter`) — آماده‌ی Loki/ELK
- contextvars: `request_id`، `correlation_id`، `job_id` — با `ContextFilter` روی هر رکورد لاگ می‌نشینند
- `ObservabilityMiddleware` در main.py: ساخت/echo شناسه‌ها (`X-Request-ID`/`X-Correlation-ID`)، هدرهای پاسخ برمی‌گردند، access log با method/path/status/duration_ms/ip
- مسیرهای پرتردد (استریم `/d/` و `/api/v1/files/{id}/content`، فایل‌های static) لاگ access نمی‌گیرند
- **propagation به صف:** ستون `correlation_id` در جدول jobs؛ `enqueue` مقدار contextvar را ذخیره می‌کند؛ `_process` در worker آن را به contextvars ست می‌کند → لاگ worker به درخواست HTTP اولیه گره می‌خورد
- لاگ‌های رویدادی صف: `job enqueued/done/crashed`، `upload complete` (با account و duration)، `bot download complete`، `file deleted`
- `slog(name)` سینتکس `info(msg, **fields)` — فیلدها کلید JSON می‌شوند
- مهاجرت: `ALTER TABLE jobs ADD COLUMN correlation_id` (best-effort در connect)
- `TGDRIVE_LOG_LEVEL` جدید؛ سطح access log برای 5xx خودکار WARNING است
- ⚠️ کوچک: `_log(level, msg, **fields)` از `extra=fields` استفاده می‌کند — کلیدهای fields نباید با attrs رکورد تداخل کنند
**تست:** ۷ تست جدید در `tests/test_observability.py` → کل ۴۲/۴۲

## D17 — بازطراحی پنل با دیزاین‌سیستم (ui-ux-pro-max)
**تصمیم:** پنل `static/` با دیزاین‌سیستم تولیدشده توسط اسکیل ui-ux-pro-max بازطراحی شد:
- **پالت OLED Dark** (مناسب dashboard توسعه‌دهنده‌ای): bg `#0F172A`، panel `#1B2336`، اکسنت سبز `#22C55E`، ring `#4ADE80`، destructive `#EF4444` — همه به‌صورت توکن CSS در `:root`
- **تایپوگرافی:** Vazirmatn (فارسی) + IBM Plex Sans fallback + JetBrains Mono برای شناسه‌ها/کد (`td_…`، `f_…`)
- **a11y:** لیبل visible برای همه‌ی فیلدها (فرم placeholder-only بود)، `:focus-visible` سبز، `role=alert` روی خطاها، `aria-live=polite` روی toast، `aria-labelledby` روی مودال‌ها، aria-label روی nav/sectionها
- **لمس:** هدف ۴۴px (`--tap`) برای input/button؛ تب‌ها ۳۸px (استثنای دسکتاپ)
- **انیمیشن:** transition 160ms روی hover/focus/active؛ `prefers-reduced-motion` رعایت
- **آیکون:** SVG inline (Lucide box) جای emoji در لوگو؛ فقط متن‌های وضعیت emoji نگه داشته شد
- **ریسپانسیو:** 375/480/768 — تب‌ها اسکرول افقی در موبایل، toolbar تمام‌عرض

**باگ‌های واقعی که بازطراحی کشف و رفع کرد:**
1. قانون `.tab { display:none }` **از اول در CSS وجود نداشت** → همه‌ی تب‌ها همزمان نمایش داده می‌شدند (باگ از v1)
2. `dialog { display:flex }` استایل `:not([open]) { display:none }` مرورگر را override می‌کرد → مودال‌های بسته همیشه نمایان بودند؛ با `dialog:not([open]) { display:none }` رفع شد
3. toast قبلاً timer double-set داشت؛ با clearTimeout اصلاح + نوع error متمایز
4. دکمه‌ی آپلود حین آپلود disabled نمی‌شد (double-submit ممکن بود)

**متد:** design-system با `--design-system --persist` در `design-system/telegramdrive/MASTER.md` ذخیره شد؛ تأیید بصری با preview زنده (لاگین → داشبورد → مودال → تب‌ها) و snapshot بدون خطای کنسول. تست‌های pytest به UI static دست نمی‌زنند؛ رگرسیون بصری نیازمند چشم/اسکرین‌شات است.

## D18 — دسته P0 رقابتی (slug/رمز/سقف/تامب‌نیل/ترش/نوتیف)
**تصمیم:** بردارهای سریع رقابتی (تحقیق GoFile/Pixeldrain — جزئیات در ROADMAP.md):
- جدول `links`: file_id، slug (UNIQUE)، pwd_hash (sha256)، max_downloads، hits، disabled — همه‌ی فیچرهای لینک در یک جدول واحد
- `POST /files/{id}/share` → لینک `/{slug}` (صفحه) + `/d/{slug}/dl` (دانلود). اسلاگ خودکار ۱۲ کاراکتری اگر خالی؛ 409 اگر تکراری؛ regex `[a-zA-Z0-9_-]{3,64}`
- رمز لینک: sha256، ورودی با `?pw=` یا هدر `X-Link-Password`؛ فرم HTML خودکار روی 401
- سقف دانلود: hits >= max → 403 با پیام فارسی. حذف لینک → `DELETE /files/{id}/links/{link_id}`
- صفحه‌ی عمومی `/{slug}`: HTML ریاضی‌شده، `<video controls src=/d/{slug}>` برای ویدیو، `<audio>` برای صدا، تامب‌نیل img — همه از استریم Range موجود استفاده می‌کنند (بدون کش جدید)
- تامب‌نیل: هنگام آپلود برای image/jpeg|png|webp و ویدیو و pdf، خود فایل به‌عنوان پیام جدا در تلگرام ذخیره و `thumb_message_id` ست می‌شود (best-effort؛ شکستش جاب را نمی‌شکند)؛ سرو `/{slug}/thumb` با Cache-Control 24h
- **Trash:** `DELETE /files/{id}` پیش‌فرض حذف نرم (`deleted_at`) — لینک‌ها 404 می‌دهند؛ `?purge=true` = حذف فیزیکی تلگرام؛ `GET /files/{id}/restore` بازیابی؛ janitor زباله‌دان >۷ روز را purge می‌کند
- **نوتیف ادمین:** `app/services/notify.py` — خطای نهایی جاب‌ها به `TGDRIVE_BOT_ADMIN_IDS` با اولین بات ready؛ best-effort (شکستش لاگ می‌شود)
- تست‌های قدیمی delete: به `?purge=true` مهاجرت یافتند؛ audit اکشن `file.delete` → `file.trash`
- ⚠️ پونتیل: کلاس تکراری `LinkIn` shadow می‌کرد → شد `ShareIn`. # ponytail در notify: sequential + first-bot-only
**تست:** ۸ تست جدید (`tests/test_p0_features.py`) → کل ۵۰/۵۰

## D19 — QR Code لینک عمومی
**تصمیم:** `GET /{slug}/qr` — SVG بدون Pillow (`qrcode.image.svg.SvgPathImage`)، کش ۲۴ ساعته، pass-through رمز/سقف/ترش همان `_check_slug_access`. QR به صفحه‌ی `/{slug}` اشاره می‌کند (نه مستقیم دانلود) تا رمز/سقف اعمال شود.
- صفحه‌ی عمومی: بخش `<details>` تاشو «QR Code» با پس‌زمینه سفید
- پنل: بعد از ساخت لینک، مودال QR باز می‌شود (`showQR`)
- `qrcode` به requirements اضافه شد (بدون وابستگی PIL)
- ⚠️ `to_string()` بسته به factory ممکن است bytes برگرداند → decode مشروط
**تست:** +۲ (QR SVG + 404 اسلاگ ناشناس) → کل ۵۲/۵۲؛ تأیید زنده: curl SVG واقعی + img.naturalWidth>0 در preview
**نکته دیباگ:** بدون `.env`، secret سرور با secret پروسه‌ی دستی decrypt نمی‌خواند («Not a valid string») — `.env` ساخته شد تا secret/FAKE_TG ثابت بماند

## D20 — بک‌اند دوم ذخیره‌سازی: ایتا (ایتایار)

**تحقیق API ایتایار:** فقط ارسال — `POST /api/{token}/sendFile` (multipart) و `sendMessage`. دانلود/حذف API ندارند.
- **معماری:** `EitaaBackend` (BackendClient جدید) + استخر `eit:{id}` در TGManager؛ `acquire(..., backend="eitaa")` استخر را پین می‌کند
- **مسیریابی:** `files.backend` (از `api_keys.backend` یا `TGDRIVE_DEFAULT_BACKEND` هنگام enqueue اسنپ‌شات می‌شود)؛ download/stream/delete از خود رکورد فایل می‌خوانند
- **پیش‌فرض سیستم:** `TGDRIVE_DEFAULT_BACKEND` (telegram|eitaa)؛ کلید بدون override = پیش‌فرض
- **دانلود ایتا:** اسکرپ صفحه‌ی عمومی `eitaa.com/{chat}/{id}` → مقصد باید کانال عمومی باشد؛ حذف = no-op (API ندارد)
- **Fake mode:** `FakeBackend.kind` از پیشوند cid (`eit:` → eitaa) — بدون کلاس دوم؛ `storage_chat` روی backend ست می‌شود
- **پنل:** تب «ایتا» (CRUD + تست ارسال واقعی)؛ انتخاب ذخیره‌سازی در مودال کلید؛ ستون «ذخیره» در جدول فایل‌ها
**تست:** +۸ (`tests/test_eitaa.py`: مسیریابی پیش‌فرض/override، roundtrip ایتا، شکست بلند بدون بک‌اند، CRUD، عدم cross-routing) → کل ۶۰/۶۰
