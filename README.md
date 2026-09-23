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

## محدودیت‌های سرویس حرفه‌ای اعمال‌شده

- نرخ درخواست per-key (token bucket)
- سهمیه روزانه per-key
- محدودیت پسوند خطرناک (.exe و…)
- Rate limit روی login (ضد brute-force)
- Audit log کامل
- Circuit breaker اکانت + isolation هنگام FloodWait
- Presigned HMAC + one-time
- جداسازی tmp + janitor پاک‌سازی
