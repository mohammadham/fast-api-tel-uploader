# 📦 TelegramDrive — سرویس حرفه‌ای آپلود/دانلود روی تلگرام

> پلن معماری نسخه ۲ (نهایی پس از ۵ دور بازبینی)
> هدف: رقابت با سرویس‌های آپلود حرفه‌ای (Bayfiles/Openload/Streamtape-style) با بک‌اند نامحدود تلگرام.

---

## ۱. یافته‌های تحقیق (محدودیت‌های API تلگرام)

### ۱.۱ محدودیت‌های MTProto (اکانت کاربر — Telethon)
| محدودیت | مقدار | راهکار سیستم ما |
|---|---|---|
| حداکثر حجم فایل | 2 GB (اکانت معمولی) / 4 GB (پرمیوم) | فایل‌های بزرگ → تقسیم به پارت‌های ≤1.9GB (auto-splitter) |
| FloodWaitError | انتظار اجباری ۳۰s تا چند ساعت | توقف هوشمند worker + شمارش backoff نمایی |
| PeerFloodError | اسپم به چت‌های ناشناس | آپلود فقط به «کانال ذخیره‌سازی خودِ اکانت» (Saved Messages / کانال خصوصی) |
| سرعت انتقال | تک‌کاناله و آهسته | FastTelethon (موازی‌سازی chunk روی DCهای متعدد) |
| تعداد لاگین | محدودیت ثبت نام/دستگاه | سشن‌ها یک‌بار ساخته می‌شوند و StringSession پایدار در دیتابیس رمز می‌شود |
| Session file | قفل شدن فایل SQLite | StringSession + استخر اتصال در حافظه |

### ۱.۲ محدودیت‌های Bot API
| محدودیت | مقدار | راهکار |
|---|---|---|
| دانلود getFile | ≤ 20 MB | بات فقط برای فایل‌های ≤ 50MB استفاده می‌شود |
| آپلود sendDocument | ≤ 50 MB | همان بالا |
| نرخ پیام | 30 msg/s (گروه‌ها ~20/min) | صف خروجی بات + token bucket |
| Local Bot API Server | تا 2GB | گزینه‌ی docker-compose برای فعال‌سازی (اختیاری) |

### ۱.۳ کتابخانه‌های آماده بررسی‌شده
| کتابخانه | نقش | وضعیت انتخاب |
|---|---|---|
| Telethon | MTProto کامل + FastTelethon | ✅ انتخاب اصلی |
| Pyrogram/Kurigram | MTProto جایگزین | ❌ نگهداری ناپایدار |
| aiogram 3 | بات async | ✅ برای بات |
| tdlib | سنگین، C++ | ❌ |
| python-telegram-bot | sync قدیمی | ❌ |
| FastAPI + Uvicorn/Gunicorn | وب | ✅ |
| ARQ / Celery / RQ | صف | ❌ صف داخلی اختصاصی با SQLite (بدون Redis اجباری) |
| FastTelethon (gist painor) | انتقال موازی | ✅ embed شده |

### ۱.۴ روش‌های مدیریت صف بررسی‌شده
1. Celery+Redis — سنگین، وابستگی بیرونی ❌
2. ARQ (asyncio+redis) — خوب ولی Redis می‌خواهد ⚠️
3. **صف داخلی asyncio.PriorityQueue + WAL SQLite برای persistence** ✅ (انتخاب شد: کم‌وابستگی، پایدار، قابل کنترل دقیق برای FloodWait)
4. Kafka/Rabbit — over-engineering برای این مقیاس ❌

---

## ۲. معماری نهایی (پس از ۵ دور بازبینی)

```
┌─────────────────────────────────────────────────────────────────┐
│                        FastAPI App (:8000)                       │
│  ┌───────────┐ ┌────────────┐ ┌──────────┐ ┌─────────────────┐  │
│  │ Web Panel │ │ REST API   │ │ Auth     │ │ Streaming        │  │
│  │ (SPA)     │ │ +API Keys  │ │ JWT/2FA  │ │ Range/presigned  │  │
│  └───────────┘ └────────────┘ └──────────┘ └─────────────────┘  │
├─────────────────────────────────────────────────────────────────┤
│  Rate Limiter (token bucket)  │  Audit Log  │  Metrics Prom     │
├─────────────────────────────────────────────────────────────────┤
│                     Priority Queue Manager                       │
│   PriorityQueue:  DOWNLOAD(10) > UPLOAD(5) > MIGRATE/DELETE(1)  │
│   * پایدار روی SQLite WAL  * FloodWait-aware  * retry/exp backoff│
│   * DL Workers: N×Telethon  * UL Worker: M×Telethon  * Bot worker│
├─────────────────────────────────────────────────────────────────┤
│  Session Manager (Telethon Pool)  │  Bot Service (aiogram)       │
│  * استخر StringSession اکانت‌ها    │  * آپلود/دانلود کوچک ≤50MB    │
│  * round-robin + health-check     │  * رابط مدیریت/通知/صف        │
│  * circuit-breaker هر اکانت        │  * inline commands           │
├─────────────────────────────────────────────────────────────────┤
│  Storage Layer: Telegram Channels (auto-split >1.9GB → parts)    │
│  Metadata DB: SQLite WAL (files, parts, keys, users, audit)      │
└─────────────────────────────────────────────────────────────────┘
```

### اجزای اصلی
| ماژول | فایل | وظیفه |
|---|---|---|
| Config | `app/core/config.py` | pydantic-settings، همه envها |
| DB | `app/core/db.py` | SQLite WAL + Repos |
| Models | `app/core/models.py` | User, ApiKey, TgAccount, BotToken, FileRecord, FilePart, QueueJob, AuditLog |
| Security | `app/core/security.py` | bcrypt، JWT، ApiKey hash، Fernet session crypto |
| RateLimit | `app/core/rate_limit.py` | token bucket per-key/per-IP |
| TG Manager | `app/tg/manager.py` | استخر Telethon + round-robin + circuit breaker |
| Bot Service | `app/tg/bot.py` | aiogram + صف داخلی |
| Fast DL | `app/tg/fast_download.py` | FastTelethon embed |
| Fast UL | `app/tg/fast_upload.py` | FastTelethon embed |
| Queue | `app/queue/queue_manager.py` | صف اولویت‌دار + workers + persistence |
| Splitter | `app/services/splitter.py` | تقسیم فایل >1.9GB و merge در دانلود |
| Router | `app/api/*.py` | auth, accounts, bots, keys, files, queue, admin, metrics |
| Panel | `static/` | HTML/JS/CSS بدون build step |

### جدول اولویت صف
| اولویت | نوع | توضیح |
|---|---|---|
| 100 (بالاترین) | DOWNLOAD | درخواست دانلود/استریم همیشه اول |
| 60 | STREAM_RANGE | ادامه‌ی استریم کاربر فعال |
| 40 | UPLOAD | آپلود فایل |
| 20 | MIGRATE | انتقال بین اکانت‌ها |
| 10 | DELETE/ADMIN | پاک‌سازی |

### جریان آپلود
1. `POST /api/v1/files/upload` → multipart stream → فایل موقت
2. Registration در DB (status=queued) → enqueue (UL, prio=40)
3. Worker: انتخاب اکانت آزاد → (اگر >1.9GB → splitter) → FastTelethon upload به کانال ذخیره‌سازی
4. ذخیره message_id/parts → status=ready → بازگرداندن file_id + لینک
5. فایل موقت حذف (یا نگهداری کوتاه برای retry)

### جریان دانلود/استریم
1. `GET /d/{file_id}` یا `GET /api/v1/files/{id}/content` (با Range)
2. enqueue (DL, prio=100, sub-prio بر اساس برآورد میزان انتظار)
3. Worker: FastTelethon parallel-download → chunkها مستقیم به Response Streaming
4. پشتیبانی Range/206 + Content-Length + Content-Disposition + ETag
5. Presigned URL با انقضا و محدودیت دانلود

### مدیریت اکانت‌ها (پنل)
- افزودن اکانت: phone → code (optional 2FA password) → StringSession
- نمایش: وضعیت، flood-wait تا کی، تعداد آپلود/دانلود، حجم مصرفی، سلامت
- اکشن: فعال/غیرفعال، حذف، تست اتصال، reset circuit breaker

### مدیریت توکن‌ها (پنل)
- ساخت API Key با: name، scopes، سهمیه روزانه (GB)، سهمیه req/min، انقضا
- نمایش hash-prefix، revoke، آمار مصرف

### مدیریت بات‌ها (پنل)
- افزودن توکن بات → راه‌اندازی aiogram instance
- فرمان‌ها: `/upload` (پاسخ به فایل)، `/download {id}`، `/queue`، `/status`
- بات به صف اصلی وصل است (اولویت پایین‌تر از DL کاربران)

---

## ۳. امنیت
| لایه | پیاده‌سازی |
|---|---|
| احراز هویت پنل | JWT کوتاه‌عمر + refresh + bcrypt |
| کلیدهای API | `td_` + 43 char base58، فقط sha256 در DB، scopes |
| Session تلگرام | Fernet (کلید از env `TGDRIVE_SECRET`) |
| Rate limit | token bucket روی /upload /download /auth + global |
| Upload validation | پسوندهای مسدود، حد حجم، اسم‌سازی (sanitized name) |
| Path traversal | نام فایل تصادفی، عدم استفاده از user path |
| Audit log | همه اکشن‌های مدیریتی + دانلودها (IP, key, ts) |
| CORS | سفیدلیست |
| Abuse | حداکثر دانلود همزمان per-key، quota روزانه، بن دستی |
| Secrets | فقط env، .env در gitignore |

## ۴. بهینگی و پایداری
- **FastTelethon**: موازی chunk (کلیه DCها) → چند برابر سریع‌تر از download_media استاندارد
- **Persisted queue**: با ری‌استارت، jobها از SQLite بازیابی می‌شوند
- **FloodWait-aware**: هنگام FloodWait اکانت به مدت لازم isolate می‌شود و job به اکانت دیگر صدرصد می‌رود
- **Circuit breaker**: ۵ خطای متوالی اکانت → ۵ دقیقه isolate
- **Streaming بدون دیسک**: دانلود مستقیم از TG به client (chunk → stream)
- **Graceful shutdown**: drain صف، توقف امن workers
- **Health endpoints**: /healthz /readyz /metrics (Prometheus)

## ۵. Docker
- `Dockerfile` (python:3.12-slim) + `docker-compose.yml`:
  - `app` (FastAPI + workers + bot)
  - `botapi` (اختیاری: telegram-bot-api local برای 2GB بات)
  - volume: `./data` برای DB و سشن‌ها
- Gunicorn+UvicornWorker، restart policy، healthcheck

## ۶. تست
- **Unit**: security، splitter، rate limiter، queue ordering
- **Integration با FakeTelegram** (بدون شبکه): آپلود/دانلود/استریم/Range
- **API tests**: httpx AsyncClient روی کل اپ
- **Concurrency test**: ۳۰ دانلود همزمان + ۵ آپلود → ترتیب اولویت حفظ شود
- **Persistence test**: ری‌استارت صف → jobها بازیابی شوند

---

# 🔄 ۵ دور بازبینی و بهبود پلن

## دور ۱ — شکاف‌ها و امنیت
- ❌ کشف شد: «حذف فیزیکی» فایل روی تلگرام لازم است → اضافه شد (message delete)
- ❌ Presigned URL قابل حدس بود → HMAC + expiry + یک‌بارمصرف اختیاری
- ❌ Upload های نیمه‌تمام (crash) فایل موقت جا می‌گذارند → janitor task هر ۱۰ دقیقه
- + اضافه شد: ETag/Last-Modified برای cache کلاینت‌ها

## دور ۲ — مقیاس و کارایی
- ❌ تک‌پروسه بودن → Gunicorn multi-worker → صف باید بین پروسه‌ها shared باشد
  → **راه‌حل**: حالت single-worker پیش‌فرض + Optional Redis backend برای scale-out (آینده)؛ فعلاً multi-worker فقط برای API، صف در یک پروسه‌ی dedicated (`QUEUE_SEPARATE_PROCESS=true`)
- ❌ Large multipart upload → پشتیبانی **chunked resumable upload** (session upload با offset)
- + اضافه شد: connection pool هر اکانت + keepalive ping

## دور ۳ — قابلیت‌های سرویس حرفه‌ای
- + Gallery/Direct link generator
- + Folder/collection (گروه‌بندی فایل‌ها، به‌صورت album در تلگرام)
- + Thumbnail از فایل‌های ویدیویی/تصویری (هدر Content-Type درست)
- + Webhook/callback بعد از تکمیل آپلود
- + CLI client ساده (`curl` examples در README)

## دور ۴ — مانیتورینگ و دیباگ
- + `/metrics` Prometheus (queue depth، سرعت DL/UL، flood count، اکانت سلامت)
- + Audit log API + خروجی CSV
- + ساختار لاگ JSON + request-id propagation

## دور ۵ — نهایی‌سازی سخت
- ✅ بررسی first-principles: آیا Redis لازم است؟ → خیر برای v1 (SQLite WAL کافی است تا ~500 job/min)
- ✅ آیا چند اکانت برای دانلود لازم است؟ → بله، round-robin باعث توزیع بار flood می‌شود
- ✅ امنیت StringSession در DB → Fernet + محدودیت دسترسی فایل دیتابیس
- + اضافه شد: `--dev` mode با fake telegram برای تست بدون اکانت واقعی

## 📋 چیزهایی که بعداً (v2 roadmap)
- Redis/RabbitMQ برای scale-out کامل
- S3-compatible local cache layer (CDN front)
- Multi-node با exclusive lock
- Admin SSO (OIDC)
- کلاینت‌های desktop/mobile

---

## ✅ جمع‌بندی نهایی
پلن v2 آماده پیاده‌سازی است: FastAPI + Telethon (FastTelethon) + aiogram + صف اولویت‌دار پایدار داخلی + پنل وب + امنیت چندلایه + Docker + تست کامل. منطق «اولویت دانلود» در هسته‌ی صف جاری است.
