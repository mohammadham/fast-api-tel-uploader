# 📦 TelegramDrive

سرویس حرفه‌ای آپلود/دانلود فایل با بک‌اند تلگرام — معادل self-hosted سرویس‌های آپلود تجاری، با:

- **اکانت‌های تلگرام (MTProto/Telethon)** برای فایل‌های تا ۲GB (پارت‌بندی خودکار بالاتر)
- **ربات تلگرام** برای آپلود/دانلود فایل‌های ≤۵۰MB و مدیریت
- **صف اولویت‌دار پایدار** — دانلود همیشه مقدم بر آپلود
- **دانلود استریم با Range/206** (پخش ویدیو، resume، پارشئال)
- **لینک‌های امضاشده** با انقضا و گزینه‌ی یک‌بارمصرف
- **پنل وب کامل**: اکانت‌ها، بات‌ها، کلیدها، فایل‌ها، صف، لاگ
- **API Key با سهمیه و نرخ**، JWT برای پنل، رمزنگاری سشن‌ها (Fernet)
- **Circuit breaker + FloodWait-aware** + retry نمایی
- **Prometheus metrics + audit log**
- **Docker + تست کامل**

## راه‌اندازی سریع (Docker)

```bash
cp .env.example .env
# ویرایش: TGDRIVE_SECRET را حتماً عوض کنید
docker compose up -d --build
# رمز ادمین تولیدشده:
cat data/initial_admin_password.txt
# پنل: http://localhost:8000
```

## راه‌اندازی بدون داکر

```bash
python -m venv .venv && source .venv/bin/activate  # ویندوز: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env
uvicorn app.main:app --port 8000
```

## حالت تست بدون تلگرام

```
TGDRIVE_FAKE_TG=1
```
در این حالت یک بک‌اند تقلبی درون‌حافظه‌ای جای تلگرام می‌نشیند؛ تمام قابلیت‌ها (صف، استریم، Range) بدون اکانت واقعی قابل تست است.

## اتصال اکانت تلگرام

1. `TGDRIVE_TG_API_ID` و `TGDRIVE_TG_API_HASH` را از <https://my.telegram.org> بگیرید.
2. پنل → اکانت‌ها → افزودن اکانت → شماره تلفن → کد → (رمز 2FA در صورت وجود).
3. سشن به‌صورت StringSession رمزشده ذخیره می‌شود.

> ⚠️ نکته‌ی مهم تلگرام: آپلود فقط به «ذخیره‌سازی خود اکانت» یا کانال خصوصی که اکانت شما ادمینش است انجام می‌شود تا PeerFloodError نبینید.

## بک‌اند ایتا (ایتایار)

در کنار تلگرام، می‌توانید فایل‌ها را در **ایتا** ذخیره کنید:

1. از <https://eitaayar.ir> توکن API بگیرید و در پنل (تب «ایتا») اضافه کنید.
2. شناسه‌ی **کانال عمومی ایتا** را به‌عنوان مقصد بدهید (@sender را مدیر کانال کنید).
3.  → پیش‌فرض سیستم ایتا می‌شود؛ یا
4. در ساخت کلید API فیلد «محل ذخیره‌سازی» را روی ایتا بگذارید → آپلودهای آن کلید در ایتا ذخیره می‌شوند (کلید بدون override = پیش‌فرض سیستم).

**محدودیت‌های ایتایار (مستند ۲۰۲۶):** API فقط ارسال است — `sendFile` و `sendMessage`.
- **دانلود**: از صفحه‌ی عمومی `eitaa.com/{کانال}/{id}` اسکرپ می‌شود → مقصد باید کانال عمومی باشد.
- **حذف**: API حذف ندارد؛ purge فقط رکورد دیتابیس را پاک می‌کند (پیام در ایتا می‌ماند).
- **حجم**: محدودیت رسمی منتشر نشده؛ برای امنیت پارت‌بندی تلگرامی روی ایتا اعمال نمی‌شود ولی سقف `TGDRIVE_MAX_UPLOAD_SIZE` برقرار است.

## اتصال ربات

1. از @BotFather توکن بگیرید.
2. پنل → بات‌ها → افزودن بات → توکن.
3. فرمان‌های بات: `/help`, `/upload` (روی فایل ریپلای کنید)، `/download file_id`, `/status`, `/files`
4. اختیاری: `TGDRIVE_BOT_ADMIN_IDS` برای محدودکردن کنترل بات به کاربران خاص.
5. اختیاری: سرور محلی Bot API برای بات تا ۲GB: `docker compose --profile localbot up -d` و `TGDRIVE_BOT_API_BASE=http://botapi:8081`

## استخر پراکسی تلگرام (فعال‌سازی/مانیتور/Fallback)

برای عبور از محدودیت شبکه هنگام اتصال به تلگرام، می‌توانید پراکسی تعریف کنید (MTProto با secret، SOCKS5 و HTTP با احراز هویت اختیاری):

1. **افزودن پراکسی**: پنل → تب «پراکسی‌ها» → افزودن با لینک اشتراک (`tg://proxy?...`، `t.me/proxy`، `socks5://user:pass@host:port` یا `host:port[:user:pass]`) یا ورود دستی. رمزها رمزنگاری‌شده ذخیره می‌شوند.
2. **فعال‌سازی**: پنل → تب تنظیمات → گروه «پراکسی تلگرام» → `proxy_enabled=1` (بدون ری‌استارت؛ اتصال‌های زنده تلگرام خودکار reconnect می‌شوند). وقتی خاموش است همه اتصال‌ها مستقیم‌اند.
3. **تست سرعت**: دکمه «تست همه» (یا `POST /api/v1/admin/proxies/test`) — تاخیر TCP هر پراکسی ذخیره و لیست بر اساس سرعت مرتب می‌شود.
4. **انتخاب برای اتصال‌های جدید**: استراتژی `speed` (تندترین تست‌شده) یا `rr` (چرخشی).
5. **مانیتور دوره‌ای**: `proxy_monitor_interval` دقیقه (۰=خاموش، پیش‌فرض؛ ۲ تا ۱۴۴۰). هر پاس: وضعیت/تاخیر همه پراکسی‌ها ذخیره می‌شود و **فقط وقتی وضعیت یک پراکسی flip کند** (سالم→قطع، قطع→سالم، ضعیف↔سالم) به ادمین‌های `TGDRIVE_BOT_ADMIN_IDS` پیام تلگرامی «🛰 گزارش سلامت پراکسی‌ها» می‌رود — ری‌استارت هرگز alert-storm نمی‌سازد (پاس اول فقط seed است).
6. **Fallback خودکار**: اگر ترانسفری از مسیر یک پراکسی در سطح transport شکست بخورد (FloodWait شمرده نمی‌شود)، آن پراکسی برای ۵ دقیقه «مرده» علامت می‌خورد و اتصال‌های جدید **خودکار به سریع‌ترین پراکسی سالم بعدی** می‌روند؛ اگر هیچ پراکسی سالمی نماند، مستقیم. پراکسی پس از اولین ترانسفر موفق یا اولین تست سالمِ مانیتور بلافاصله به استخر برمی‌گردد (بدون انتظار برای انقضای ۵ دقیقه).

جزئیات کامل endpointها: `API_DOCUMENTATION.md` ← بخش "Admin: Telegram Proxy Pool".

## استفاده از API

```bash
# ساخت کلید از پنل (کلیدها → ساخت)، سپس:

# آپلود
curl -H "Authorization: Bearer td_YOUR_KEY" -F "file=@video.mp4" \
  http://localhost:8000/api/v1/files/upload

# وضعیت
curl -H "Authorization: Bearer td_YOUR_KEY" \
  http://localhost:8000/api/v1/files/f_xxxx

# لینک امضاشده ۱ ساعته
curl -X POST -H "Authorization: Bearer td_YOUR_KEY" \
  -H "Content-Type: application/json" -d '{"ttl":3600,"one_time":false}' \
  http://localhost:8000/api/v1/files/f_xxxx/link

# دانلود استریم با Range
curl -H "Range: bytes=0-1023" -L "LINK" -o part.bin

# آپلود resumable (چانک ۸MB)
curl -X POST -H "Authorization: Bearer td_YOUR_KEY" -H "Content-Type: application/json" \
  -d '{"name":"big.bin","size":25000000}' http://localhost:8000/api/v1/files/upload/session
curl -X PATCH --data-binary @chunk1.bin -H "X-Offset: 0" \
  -H "Authorization: Bearer td_YOUR_KEY" \
  http://localhost:8000/api/v1/files/upload/session/us_xxx
```

## اولویت صف

| اولویت | نوع | توضیح |
|---|---|---|
| 100 | download | همیشه اول (شامل استریم‌ها) |
| 40 | upload | آپلود — وقتی دانلود در انتظار است، pause می‌شود |
| 10 | delete | پاک‌سازی |

صف روی SQLite (WAL) پایدار است؛ با ری‌استارت، jobها ادامه پیدا می‌کنند.

## لینک عمومی پیشرفته (slug/رمز/سقف)

```bash
# ساخت لینک عمومی با اسلاگ دلخواه + رمز + سقف دانلود
curl -X POST http://localhost:8000/api/v1/files/{file_id}/share \
  -H "Authorization: Bearer td_..." -H "Content-Type: application/json" \
  -d '{"slug": "my-file", "password": "opensesame", "max_downloads": 100}'
# → {"url": ".../my-file", "download_url": ".../d/my-file/dl"}
```

- `/{slug}` — صفحه‌ی عمومی با پلیر ویدیو/صدا (Range) + دکمه‌ی دانلود + تامب‌نیل + **QR Code**
- `/{slug}/qr` — QR Code (SVG) لینک عمومی، کش ۲۴ ساعته؛ در پنل هم بعد از ساخت لینک نمایش داده می‌شود
- `/d/{slug}/dl` — دانلود مستقیم از طریق لینک (رمز و سقف را اعمال می‌کند)
- رمز: با `?pw=...` یا هدر `X-Link-Password`
- حذف پیش‌فرض = زباله‌دان (`?purge=true` = حذف نهایی از تلگرام)؛ بازیابی: `GET /files/{id}/restore`
- زباله‌دان‌های قدیمی‌تر از ۷ روز توسط janitor پاک نهایی می‌شوند
- اعلان‌های مدیریتی: خطای جاب‌ها به `TGDRIVE_BOT_ADMIN_IDS` پیام تلگرامی می‌رود

## یکپارچگی دانلود، zip و دسترسی اکانت‌ها

- **گارد قطعات**: هر فایل `ready` باید قطعات پیوستهٔ `0..n-1` با مجموع اندازهٔ اصلی برابر `files.size` داشته باشد. اگر رکورد ناقص/ناسازگار باشد، دانلود با `409 file_incomplete` رد می​شود و هیچ پاسخ ۲۰۰ ناقصی ساخته نمی​شود. رکوردهای قدیمی تک​پیامی همچنان با fallback سازگار خوانده می​شوند.
- **zip چندکاناله**: هر entry از کانال و بک​اند خودش خوانده می​شود (نه کانال فایل اول)؛ فایل موقت هر entry یکتاست و بلافاصله پاک می​شود. اگر یکی از فایل​ها ناقص باشد، کل آرشیو قبل از ارسال بایت اول با `409` رد می​شود.
- **کش دسترسی**: جدول `chat_access` برای هر جفت (بک​اند، اکانت، کانال) وضعیت خواندن/نوشتن را نگه می​دارد. کش مثبت پیش​فرض ۶ ساعت و کش منفی ۱۰ دقیقه است. آپلود موفق همان حساب را مثبت ثبت می​کند؛ خطای دسترسی در ارسال/دانلود رکورد را invalidate می​کند. فقط cache missها probe می​شوند (`access_probe_limit`).
- **انتخاب اکانت**: دانلود و zip اول اکانت آپلودکنندهٔ قطعه را ترجیح می​دهند، بعد اکانت مجاز کم​بار. اگر هیچ اکانتی مجاز نباشد، پاسخ صریح `503 storage_unavailable` است، نه فایل ناقص.
- **اسپلیت خودکار و gzip**: `auto_split_enabled` و `compress_uploads` پیش​فرض خاموش​اند. اسپلیت فقط برای تلگرام، با حداقل اکانت مجازِ دارای دسترسی نوشتن و یک کانال مشترک غیر از `me` انجام می​شود. gzip فقط وقتی اعمال می​شود که صرفه​جویی از `compress_min_savings_pct` بیشتر باشد؛ اندازهٔ اصلی در `file_parts.size` و اندازهٔ فیزیکی در `stored_size`/`encoding` می​ماند و دانلود شفاف decompress می​کند.
- **تنظیمات مرتبط**: گروه «دسترسی اکانت​ها به کانال (کش)» و «اسپلیت خودکار چنداکانتی» در پنل؛ `POST /api/v1/keys/preflight` برای بررسی دسترسی یک کانال پیش از ساخت کلید؛ `GET /api/v1/admin/access-health` برای خلاصهٔ کش، قطعات gzip و قطعات orphan.
- **rollback**: برای برگشت سریع بدون تغییر داده، `auto_split_enabled=0` و `compress_uploads=0` و در صورت نیاز `access_cache_enabled=0` را از پنل تنظیم کنید؛ تصویر قبلی را با تگ rollback نگه دارید. حذف فایل​های ذخیره​شده انجام نمی​شود.

## تست‌ها

```bash
pip install -r requirements.txt pytest pytest-asyncio asgi-lifespan
pytest -v
```

## معماری

نمودار کامل در `GRAPH.md`، تصمیم‌های طراحی در `DECISIONS.md`، پلن در `PLAN.md` و چک‌لیست در `TODO.md`.

## لاگ ساختاریافته (JSON)

همه‌ی لاگ‌ها JSON تک‌خطی هستند و آماده‌ی Loki/ELK/CloudWatch:

```json
{"ts": 1758500000.1, "level": "INFO", "logger": "tgdrive.access", "msg": "request", "method": "POST", "path": "/api/v1/files/upload", "status": 200, "duration_ms": 41.7, "ip": "1.2.3.4", "request_id": "req_9f2c...", "correlation_id": "req_9f2c..."}
{"ts": 1758500002.3, "level": "INFO", "logger": "tgdrive.queue", "msg": "upload complete", "file_id": "f_ab12...", "size": 1048576, "parts": 1, "account": "acc:1", "duration_ms": 1830.2, "correlation_id": "req_9f2c..."}
```

- **request_id**: شناسه‌ی هر درخواست HTTP؛ کلاینت می‌تواند با هدر `X-Request-ID` خودش را تحمیل کند و همان را در پاسخ می‌گیرد
- **correlation_id**: در کل زنجیره‌ی upstream ثابت است (`X-Correlation-ID`) و **داخل جاب‌های صف هم ذخیره می‌شود** — لاگ worker مستقیماً به درخواست HTTP اولیه گره می‌خورد
- **job_id**: حین پردازش هر جاب در تمام لاگ‌ها حاضر است
- دانلودهای استریمی و فایل‌های static لاگ access نمی‌گیرند (بی‌سروصدا برای پرفورمنس)
- سطح لاگ: `TGDRIVE_LOG_LEVEL` (DEBUG/INFO/WARNING/ERROR)

برای پیگیری یک مشکل در تولید: هدر `X-Correlation-ID` را از پاسخ خطا بردارید و با `grep` در لاگ‌ها، کل مسیر request → job → account را ببینید.

## متغیرهای محیطی مهم

| متغیر | پیش‌فرض | توضیح |
|---|---|---|
| `TGDRIVE_SECRET` | — | کلید امضا/رمزنگاری (الزامی تغییر) |
| `TGDRIVE_FAKE_TG` | 0 | بک‌اند تقلبی برای تست |
| `TGDRIVE_TG_API_ID/HASH` | — | اعتبارنامه my.telegram.org |
| `TGDRIVE_DOWNLOAD_WORKERS` | 4 | workerهای دانلود |
| `TGDRIVE_UPLOAD_WORKERS` | 2 | workerهای آپلود |
| `TGDRIVE_MAX_UPLOAD_SIZE` | 2.1GB | سقف آپلود |
| `TGDRIVE_SPLIT_THRESHOLD` | 1.9GB | آستانه پارت‌بندی |
| `TGDRIVE_PRESIGNED_TTL` | 3600 | عمر پیش‌فرض لینک |
| `TGDRIVE_DATABASE_URL` | — | URL دیتابیس: `postgres://…` یا `mysql://…` / `mariadb://…` (خالی → SQLite محلی؛ قطع بودن سرور → fallback خودکار به SQLite) |
| `TGDRIVE_DB_ENGINE` | — | تحمیل موتور دیتابیس (`sqlite` / `postgres` / `mysql`) — انتخاب صریح sqlite حتی با وجود URL سرور |

## محدودیت‌های سرویس حرفه‌ای اعمال‌شده

- نرخ درخواست per-key (token bucket)
- سهمیه روزانه per-key
- محدودیت پسوند خطرناک (.exe و…)
- Rate limit روی login (ضد brute-force)
- Audit log کامل
- Circuit breaker اکانت + isolation هنگام FloodWait
- Presigned HMAC + one-time
- جداسازی tmp + janitor پاک‌سازی
- Fallback پراکسی + مانیتور سلامت با notify ادمین
