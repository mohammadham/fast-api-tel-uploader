# 🚀 ROADMAP — امکانات رقابتی TelegramDrive

> تحقیق‌شده از رفتار سرویس‌های معتبر: **GoFile، Pixeldrain، Catbox/Litterbox، Streamtape، Streamable، storage.to** + الگوهای رایج قالب‌های file-hosting.

---

## صفر — چیزی که الان داریم (مزیت پایه)
- آپلود ≤2.1GB + پارت‌بندی خودکار >1.9GB + آپلود resumable (chunked)
- استریم با Range/206/HEAD/416 + ETag + merge چندپارته
- لینک Presigned با HMAC + انقضا + one-time
- کلید API با scopes/RPM/quota روزانه + webhook آپلود
- صف اولویت‌دار دانلود-محور + چند اکانت + circuit breaker
- بات چندباتی + پنل ادمین + لاگ JSON + audit + metrics
- Docker + ۴۲ تست + FakeTelegram

---

## P0 — بردارهای سریع (هر کدام ۰.۵–۲ روز، بیشترین اثر رقابتی)

| # | امکان | مرجع رقیب | نکته پیاده‌سازی در معماری فعلی |
|---|---|---|---|
| 1 | **Slug سفارشی/کوتاه لینک** (`/d/my-file` به‌جای hex) | GoFile | ستون `slug` + unique constraint؛ redirect یا سرو مستقیم |
| 2 | **رمز عبور روی لینک** | GoFile, MEGA | `pwd_hash` در لینک؛ فرم HTML کوچک قبل از `/d/` |
| 3 | **سقف دانلود لینک** (max N بار) | همه | شمارنده per-link (جدول `links` از presign به DB) |
| 4 | **Trash / حذف نرم + بازیابی** | همه | `deleted_at` به‌جای DELETE؛ janitor پاک نهایی بعد ۷ روز |
| 5 | **صفحه‌ی عمومی فایل** (پلیر + دکمه دانلود + آمار) | Pixeldrain | صفحه static با `<video controls>` روی `/d/` (Range از قبل هست) |
| 6 | **تامب‌نیل** | همه | تلگرام برای عکس/ویدیو thumbnail می‌دهد؛ fallback ffmpeg |
| 7 | **جستجو/فیلتر/تگ + صفحه‌بندی** در پنل | همه | `LIMIT/OFFSET` + `tags` ساده در جدول files |
| 8 | **Remote upload** (URL → سرور → تلگرام) | GoFile, Streamtape | job جدید `fetch` در صف؛ httpx stream → همان pipeline آپلود |
| 9 | **Zip چند فایل** (دانلود گروهی) | GoFile | `zipfile` streaming از پارت‌ها به Response |
| 10 | **QR code لینک** | رایج | `qrcode` lib → PNG یک‌خطی |
| 11 | **نوتیف تلگرام به ادمین** (آپلود/خطا/سلود) | — | BotService با `sendMessage` به `TGDRIVE_BOT_ADMIN_IDS` |
| 12 | **دانلود از چت تلگرام** (فوروارد به بات → ذخیره) | **انحصاری — هیچ رقیبی ندارد** | بات media فورواردشده را ingest کند (message.document/audio/video) |

## P1 — هسته‌ی رقابتی (۱–۳ هفته)

| # | امکان | مرجع | نکته |
|---|---|---|---|
| 13 | **فولدرها/درخت فایل** (tree + آلبوم + پیجینیت) | GoFile, Pixeldrain | `parent_id` در files؛ پنل tree؛ آلبوم = فولدر با view گالری |
| 14 | **WebDAV endpoint** (رایگاه/ویندوز network drive) | Box, Nextcloud | کیلر فیچر: rclone/WebDAV clientها بدون کد جدید وصل می‌شوند |
| 15 | **Guest upload page** (آپلود مهمان + کپچا + سهمیه IP) | همه | صفحه عمومی با سهمیه per-IP از limiter موجود |
| 16 | **آمار لینک** (per-link: بازدید، IPها، نمودار) | Pixeldrain | جدول `link_hits`؛ نمودار SVG سبک در پنل |
| 17 | **مستندات API + Playground** (OpenAPI polish) | همه | FastAPI از قبل `/docs` دارد؛ examples + توضیح فارسی |
| 18 | **CLI رسمی** (`tgdrive-cli`) | rclone-style | پکیج کوچک python: upload/download/share |
| 19 | **چندکاربره + نقش‌ها** (فایل‌های شخصی، invite) | همه | جدول users گسترش؛ `owner_id` در files؛ رول admin/user |
| 20 | **کش دیسکی/CDN جلوی تلگرام** (فایل پرمصرف) | همه | LRU دیسک + `Cache-Control`؛ عملاً Cloudflare front رایگان |
| 21 | **پلیر چندفرمته + زیرنویس** (mp4/webm/mkv + .srt) | Streamtape | HTML5 `<track>`؛ mkv همان Range است |
| 22 | **جستجوی عمومی فایل‌های public + گالری** | Pixeldrain | فقط فایل‌های `is_public=1` |
| 23 | **Antivirus scan** (VirusTotal روی آپلود) | برخی | صف جدید `scan`؛ فایل مشکوک → quarantine |
| 24 | **DMCA/Abuse report** (لینک گزارش + پنل تیک‌داون) | Streamtape و… | فرم عمومی + جدول reports + اکشن ادمین |

## P2 — پرمیوم و درآمد (۲–۴ هفته)

| # | امکان | مرجع | نکته |
|---|---|---|---|
| 25 | **پلن‌ها/اشتراک** (Free/Pro/Business: سقف حجم، سرعت، TTL) | Pixeldrain Pro | مدل Key-based فعلی → plan attribute؛ سقف per-plan در limiter |
| 26 | **درگاه پرداخت** (NowPayments/Stripe/زرین‌پال) | همه | جدول invoices؛ فعال‌سازی خودکار plan |
| 27 | **Pay-per-download برای آپلودکننده** (تقسیم درآمد) | Pixeldrain | شمارش per-key → کیف پول ساده |
| 28 | **Transcoding ویدیو** (480/720/1080 + HLS) | Streamable, api.video | سنگین: worker ffmpeg در صف؛ **ceiling: CPU سرور** — فقط پلن Pro |
| 29 | **Watermark تصویر/ویدیو** | Streamtape | ffmpeg/overlay؛ فقط ویدیو Pro |
| 30 | **Torrent → تلگرام** (با aria2) | Deepfile-style | job جدید؛ نکته‌ی حقوقی/ابزوی |
| 31 | **Multi-node / scale-out** (Redis queue) | همه | طبق PLAN دور ۲ — اینترفیز QueueManager از قبل آماده |
| 32 | **SSO ادمین** (OIDC/OAuth) | — | حذف رمز محلی؛ پنل |
| 33 | **دموی زنده + صفحه‌ی لندینگ محصول** (hero + pricing + FAQ) | قالب‌های hosting | استاتیک؛ از دیزاین‌سیستم فعلی |

## P3 — اکوسیستم (بلندمدت)

| # | امکان | مرجع |
|---|---|---|
| 34 | اپ موبایل (API آماده است) | همه |
| 35 | Sync client دسکتاپ (مثل Dropbox، با delta) | MEGA |
| 36 | افزونه مرورگر (right-click → آپلود) | Catbox |
| 37 | پشتیبانی rclone backend رسمی (WebDAV = #14 رایگانش می‌دهد) | همه |
| 38 | کولابوریشن فولدر مشترک چندکاربره | Drive |
| 39 | SDK رسمی Python/JS | api.video |
| 40 | واترمارک هوشمند + تامب‌نیل اسپرایت + چانک‌های adaptive | سایت‌های video |

---

## 🎯 پیشنهاد ترتیب اجرا
1. **Slug + رمز لینک + سقف دانلود + Trash** (#1-4) — یک روز کار، جدول `links` واحد همه را می‌گیرد
2. **صفحه‌ی عمومی فایل + تامب‌نیل** (#5-6) — چهره‌ی محصول برای رقابت
3. **Remote upload + دانلود از چت تلگرام** (#8, #12) — **مزیت انحصاری** که هیچ رقیبی ندارد
4. **WebDAV** (#14) — بزرگ‌ترین jump رقابتی با کمترین کد
5. **فولدرها + guest upload + آمار** (#13, 15, 16) — رفتار full-product
6. بعد مسیر P2 درآمدی

## ⚠️ نکات ساختاری تلگرام (چرا بعضی فیچرها متفاوت‌اند)
- **حذف فیزیکی**: فایل واقعاً از تلگرام پاک می‌شود → «حذف موقت» فقط جلوی لینک را می‌گیرد (پیام تا janitor پاکش نکند هست)
- **Transcoding**: مصرف CPU خود سرور است (تلگرام فقط storage) → گران‌ترین فیچر
- **سرعت**: سقف ذاتی MTProto → کش/CDN (#20) مهم‌ترین ابزار تجربه‌ی کاربری است
- **مزیت**: بک‌اند نامحدود و ارزان + ingestion از خود تلگرام = چیزی که Catbox/GoFile ندارند
