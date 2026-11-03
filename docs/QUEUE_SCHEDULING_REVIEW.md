# بررسی صف، زمان‌بندی و مدیریت بار — TelegramDrive

**تاریخ:** ۲۰۲۶-۱۱-۰۳
**دامنه بررسی:** کد مسیر آپلود/دانلود/صف/استخر تلگرام + بررسی read-only استقرار زنده روی `tdrive.sanduqche.top`
**نوع سند:** بررسی مهندسی، نه مستند محصول. همه‌ی ادعاهای کد با ارجاع به فایل و تابع، و ادعاهای اجرایی با داده‌ی واقعی یا محاسبه‌ی بازتولیدپذیر آمده‌اند.

---

## ۰) خلاصه اجرایی

سیستم یک **صف پایدار با اولویت نوع‌کار** دارد، اما آن چیزی که درخواست کرده‌اید — صف آگاه از اندازه/قدمت، انتخاب اکانت بر اساس ظرفیت، و کنترل نرخ هر اکانت/ربات — در وضعیت فعلی **وجود ندارد**.

| نیاز | وضعیت فعلی | حکم |
|---|---|---|
| ۱. صف با اولویت فایل‌های سبک‌تر و قدیمی‌تر، بدون گرسنگی | صف heap با کلید `(-priority, seq)`؛ اولویت فقط بر اساس **نوع** کار (دانلود ۱۰۰، آپلود ۴۰، حذف ۱۰)؛ `seq` میلی‌ثانیه‌ای؛ **اندازه فایل در jobs نیست**؛ **aging ندارد** | ❌ ندارد |
| ۲. انتخاب اکانت/ربات متناسب با ظرفیت | round-robin ساده + سقف همزمانی؛ بدون وزن، بدون premium، بدون throughput اندازه‌گیری‌شده، بدون صف per-account | ⚠️ جزئی و دارای باگ |
| ۳. صف/نرخ مستقل برای هر اکانت و ربات | فقط FloodWait واکنشی + circuit breaker؛ bot delivery بدون سقف، بدون بررسی پاسخ و بدون احترام به 429 | ❌ ندارد |

نکته‌ی مهم: صف فعلی در برابر «چند درخواست هم‌زمان» **backpressure ندارد**. هر آپلود کامل روی دیسک نوشته می‌شود و بعد enqueue می‌شود؛ سقف تعداد جاب، سقف بایت در جریان، watermark دیسک و سقف جاب هم‌زمان per-key وجود ندارد. یعنی سیستم به‌جای «مدیریت» بار، بار را **می‌پذیرد و انبار می‌کند** و بعد با دو worker و یک اکانت آن را تخلیه می‌کند.

---

## ۱) روش بررسی

1. خواندن کامل مسیرهای کد: `app/api/files.py`، `app/queue/queue_manager.py`، `app/tg/manager.py`، `app/services/streaming.py`، `app/tg/bot_service.py`، `app/tg/telethon_backend.py`، `app/tg/fast_transfer.py`، `app/services/janitor.py`، `app/core/rate_limit.py`، `app/api/deps.py`.
2. بررسی read-only استقرار زنده: لاگین ادمین، `queue/stats`، `queue/pressure`، `queue/jobs`، `admin/overview`، `admin/settings`، `admin/health-report`، `admin/metrics`. هیچ تغییری در داده یا تنظیمات تولید انجام نشد.
3. اجرای تست‌های مرتبط: `tests/test_queue.py`، `tests/test_pressure_alerts.py`، `tests/test_bots_ops.py`، `tests/test_concurrency.py` → **۱۲ تست پاس**.
4. شبیه‌سازی گسسته-رویداد برای مقایسه‌ی سیاست‌های زمان‌بندی (FIFO فعلی، SJF، دو-لاین) با فرض ۲ worker و ۲MB/s برای هر worker. این شبیه‌سازی benchmark تلگرام نیست؛ برای نشان‌دادن رفتار صف است.

**محدودیت‌ها:** هیچ تست بار یا تغییر داده روی استقرار تولید انجام نشد (فقط GET و لاگین). به 1Panel و منابع CPU/RAM/دیسک کانتینر لاگین نشدم؛ یافته‌های اجرایی از APIهای خود اپ (queue/health/metrics) هستند. FloodWait واقعی تلگرام بازتولید نشد؛ محاسبه‌ی آن از کد و پارامترهای تنظیمات است.

### ۱.۱ وضعیت زنده (۲۰۲۶-۱۱-۰۳، read-only)

| مورد | مقدار مشاهده‌شده |
|---|---|
| اکانت | ۱ عدد (`acc:2`)، ready، غیر-premium، `sem_free=2` |
| ربات | ۱ عدد، ready |
| فایل | ۱ فایل ready، ۳۵۷,۳۰۶,۵۷۵ بایت |
| ترافیک | ۵ دانلود، ۱,۰۷۲,۰۲۰,۷۲۶ بایت سروشده |
| صف | pending/running/retry/failed/done همه ۰؛ `heap_size=19` |
| worker | download=۴، upload=۲؛ upload gate=۲ |
| تنظیمات | `max_upload_size=10GB` (db)، `split_threshold=1.9GB`، `max_concurrent_downloads=4`، `max_concurrent_uploads=2` |
| زیرساخت | MySQL، Redis روشن (۱Panel)، `nodes=3`، uptime ~۳۶.۵ ساعت |
| متریک | `queue_done_upload=1`، `queue_done_delete=36`، `backends_success=25`، بدون FloodWait ثبت‌شده |

نکته: چون janitor جاب‌های تمام‌شده‌ی قدیمی‌تر از ۲۴ ساعت را پاک می‌کند، تاریخچه‌ی wait/زمان‌بندی در دسترس نبود؛ به همین دلیل رفتار با شبیه‌سازی و کد سنجیده شد، نه با percentileهای تاریخی.

---

## ۲) معماری فعلی در یک نگاه

```
HTTP /upload ──► نوشتن کامل فایل روی دیسک ──► jobs(kind=upload, priority=40, seq=ms)
                                                     │
Bot /download ──────────────────────────────► jobs(kind=download, priority=100)
                                                     │
HTTP /content, /d/... ──► streaming.py ──► مستقیم از manager.acquire() (بدون صف)
                                                     │
                              ┌──────────────────────┴──────────────────────┐
                              │  QueueManager: 4 dl worker + 2 ul worker     │
                              │  MySQL claim (FOR UPDATE SKIP LOCKED)        │
                              └──────────────────────┬──────────────────────┘
                                                     │
                              TGManager: sem per backend + round-robin + flood/circuit
                                                     │
                                  acc:*  /  bot:*  /  eit:*
```

دو نکته‌ی ساختاری:

- **دانلود HTTP اصلاً از صف رد نمی‌شود.** `file_response` در `app/services/streaming.py:157` قبل از برگرداندن پاسخ، بک‌اند را قرض می‌گیرد و اسلات را تا پایان ارسال به کلاینت نگه می‌دارد. پس «اولویت دانلود ۱۰۰» فقط برای دانلودهای ربات است، نه دانلود کاربران.
- **ربات‌ها در استخر اکانت نیستند.** `_candidates("acc")` در حالت واقعی فقط `acc:*` برمی‌گرداند؛ ربات‌ها فقط برای polling و delivery استفاده می‌شوند و `deliver_download` هیچ semaphore/queue‌ای ندارد.

---

## ۳) نیاز ۱: صف آگاه از اندازه و قدمت

### ۳.۱ چیزی که کد واقعاً انجام می‌دهد

`QueueManager.enqueue` (`app/queue/queue_manager.py:357`) اولویت را از caller می‌گیرد و `seq = int(time.time() * 1000)` می‌سازد. سپس:

```python
heapq.heappush(self._heap, (-priority, seq, job.id))
```

و claim در SQLite/MySQL با `ORDER BY priority DESC, seq` است. یعنی:

- **اولویت = نوع کار، ثابت.** `PRIO_DOWNLOAD=100`، `PRIO_STREAM=60` (استفاده نمی‌شود)، `PRIO_UPLOAD=40`، `PRIO_DELETE=10` (`app/core/models.py:35`). آپلود ۱MB و آپلود ۱۰GB هر دو ۴۰ هستند.
- **FIFO فقط در همان اولویت.** فایل قدیمی‌تر جلوتر است، ولی یک دانلود جدید همیشه از هر آپلود قدیمی جلو می‌زند.
- **aging وجود ندارد.** یک آپلود می‌تواند تا بی‌نهایت پشت جریان مداوم دانلود بماند؛ هیچ‌جا `created_at`/`next_run_at` وارد کلید ترتیب نمی‌شود.
- **اندازه در job نیست.** جدول `jobs` ستون size ندارد؛ payload بعضی jobها `size` دارد ولی scheduler آن را نمی‌خواند.
- **`seq` میلی‌ثانیه‌ای است.** دو enqueue در یک میلی‌ثانیه (کاملاً ممکن در burst) `seq` یکسان می‌گیرند؛ در heap، tie با `job.id` (رشته‌ی هگز تصادفی) شکسته می‌شود و در MySQL ترتیب tie تضمین‌شده نیست. پس «قدیمی‌تر اول» در burst واقعی می‌شکند.
- **در MySQL دو مسیر متفاوت داریم.** `_next_job_mysql` برای worker آپلود `kind IN ('upload','transfer')` می‌گذارد، ولی برای worker دانلود هیچ فیلتری ندارد؛ یعنی worker دانلود می‌تواند delete/transfer/upload بردارد. در SQLite منطق متفاوت است (`_downloads_waiting` و deferred). رفتار سیستم به موتور DB وابسته می‌شود.

### ۳.۲ اثر واقعی: یک فایل کوچک پشت یک فایل بزرگ

با فرض ۲MB/s برای هر worker و دو worker آپلود:

| فایل بزرگ جلوی صف | زمان اشغال یک worker | انتظار یک فایل ۱MB که بعداً می‌رسد |
|---|---:|---:|
| ۱۰۰MB | ۵۰ ثانیه | تا ۵۰ ثانیه |
| ۱GB | ۸.۵ دقیقه | تا ۸.۵ دقیقه |
| ۱۰GB (سقف فعلی تنظیمات) | ۸۵ دقیقه | تا ۸۵ دقیقه |

اگر هر دو worker مشغول دو فایل ۱۰GB باشند، یک فایل ۱MB تا **۸۵ دقیقه** منتظر می‌ماند، درحالی‌که خودش ۰.۵ ثانیه کار دارد. این دقیقاً همان head-of-line blocking است که در نیاز ۱ به آن اشاره کرده‌اید.

### ۳.۳ شبیه‌سازی: FIFO فعلی در برابر سیاست‌های اندازه‌آگاه

فرض: ۱۵۰ آپلود، مجموع ۱۸.۱GB، ورود یکنواخت در ۶۰۰ ثانیه، ۲ worker، هر کدام ۲MB/s (ظرفیت تجمعی ۴MB/s؛ پایین‌ترین زمان تخلیه ممکن ۷۷ دقیقه). اعداد انتظار بر حسب ثانیه:

| سیاست | میانگین انتظار | p95 | بیشترین انتظار | میانگین فایل‌های ≤۵MB | بیشترین انتظار ≤۵MB |
|---|---:|---:|---:|---:|---:|
| FIFO فعلی | ۲۲۹۸ | ۳۹۴۶ | ۴۰۲۶ | ۲۳۹۳ | ۳۹۴۸ |
| SJF خالص | ۵۵۸ | ۲۴۶۷ | ۳۸۵۷ | **۵** | **۸** |
| دو-لاین ۲۰MB | ۱۷۹۷ | ۳۹۴۵ | ۴۰۲۹ | ۱۲۹ | ۳۰۶ |
| دو-لاین ۱۰۰MB | ۱۲۲۳ | ۳۷۱۴ | ۴۰۱۰ | ۵۹۳ | ۱۱۹۰ |

تفسیر:

- SJF خالص میانگین را عالی می‌کند ولی **فایل بزرگ را گرسنه می‌کند** (بیشترین انتظار ۶۴ دقیقه، عملاً تا آخر صف).
- «aging خطی» ساده‌ای که آزمایش کردم (`max(0, size - rate*age)`) به‌سرعت به FIFO تبدیل می‌شود، چون با rate=10MB/s هر job بعد از ~۱۰۰ ثانیه وزن صفر می‌گیرد. این نتیجه‌ی مهمی است: **aging و SJF در بار اشباع با هم جمع نمی‌شوند**؛ هر ضمانت `T_max` سخت، در saturation به FIFO نزدیک می‌شود (این همان trade-off شناخته‌شده‌ی `T_max` است).
- دو-لاین (یک worker برای فایل‌های کوچک، یک worker برای bulk، با کمک متقابل وقتی lane خالی است) بدون پیچیدگی SJF، انتظار فایل‌های کوچک را از ~۴۰ دقیقه به ~۲ دقیقه می‌آورد و گرسنگی کامل bulk را هم ندارد. این کم‌ریسک‌ترین قدم اول است.

### ۳.۴ یافته‌ی عملیاتی مرتبط: janitor فایل tmp صف‌شده را بعد از ۱ ساعت پاک می‌کند

`Janitor._sweep` (`app/services/janitor.py`) هر ۱۰ دقیقه هر فایل داخل `tmp` با `mtime` قدیمی‌تر از ۳۶۰۰ ثانیه را حذف می‌کند — **بدون نگاه به jobs فعال**. یعنی:

- یک آپلود ۱۰GB که ~۸۵ دقیقه طول می‌کشد، در دقیقه ۶۰ ممکن است فایل منبعش حذف شود.
- هر jobی که بیش از ۱ ساعت در صف منتظر بماند، قبل از شروع، فایلش را از دست می‌دهد و با `tmp file missing` شکست می‌خورد.
- با سقف ۱۰GB و دو worker، backlog بیش از ۱ ساعت کاملاً واقع‌بینانه است.

این یک باگ عملیاتی است، نه فقط ضعف زمان‌بندی: صف با انتظار طولانی، خودش ورودی را نابود می‌کند.

---

## ۴) نیاز ۲: انتخاب اکانت/ربات بر اساس ظرفیت

### ۴.۱ فرمول سقف همزمانی و باگ آن

`TGManager._sem` (`app/tg/manager.py:136`):

```python
cap_dl = runtime max_concurrent_downloads
if key.startswith("acc:"):
    cap = max(1, cap_dl // max(1, len(self._backends) or 1))
else:
    cap = 2
```

مشکلات:

1. **ربات‌ها در مخرج هستند.** `len(self._backends)` شامل `bot:*` و `eit:*` هم می‌شود، درحالی‌که ربات‌ها در حالت واقعی اصلاً کاندید دانلود نیستند. با ۱ اکانت + ۱ ربات و `max_concurrent_downloads=4`، سقف اکانت **۲** می‌شود، نه ۴. این دقیقاً در استقرار زنده دیده می‌شود: `sem_free: 2` برای تنها اکانت، درحالی‌که تنظیمات ۴ است.
2. **floor=1 باعث oversubscription می‌شود.** با ۵ اکانت و تنظیم ۴، هر اکانت ۱ اسلات می‌گیرد → مجموع ۵ > ۴. با ۱۰ اکانت → ۱۰ > ۴. یعنی تنظیم «سقف» عملاً سقف کل نیست.
3. **sem یک‌بار ساخته می‌شود و resize نمی‌شود.** اگر بعداً اکانت اضافه/حذف شود، سقف‌های قبلی دست‌نخورده می‌مانند؛ توزیع ظرفیت بین اکانت‌ها ناهمگون می‌شود. `reload_all` آن را پاک می‌کند، ولی افزودن یک اکانت معمولی این کار را نمی‌کند.
4. **همان sem برای آپلود و دانلود.** سقف دانلود (که با مخرج اشتباه تقسیم شده) به آپلود هم اعمال می‌شود؛ عملاً ظرفیت آپلود هر اکانت به تعداد بک‌اندها گره خورده است.
5. **بدون وزن و بدون ظرفیت واقعی.** `is_premium` در جدول اکانت هست ولی در انتخاب استفاده نمی‌شود؛ throughput اندازه‌گیری‌شده، نرخ خطا، latency و quota باقی‌مانده هم دخالتی ندارند.

جدول محاسبه‌شده از همین فرمول (تنظیم ۴):

| اکانت | ربات | سقف هر اکانت | مجموع واقعی |
|---:|---:|---:|---:|
| ۱ | ۰ | ۴ | ۴ |
| ۱ | ۱ | ۲ | ۲ |
| ۲ | ۱ | ۱ | ۲ |
| ۳ | ۱ | ۱ | ۳ |
| ۵ | ۱ | ۱ | ۵ |
| ۱۰ | ۱ | ۱ | ۱۰ |

### ۴.۲ الگوریتم انتخاب

`TGManager.acquire` (`app/tg/manager.py:322`) یک `_rr` جهانی را increment می‌کند، لیست را از آن نقطه می‌چرخاند و **اولین بک‌اندی که sem آزاد دارد** را برمی‌دارد. این round-robin در حالت برابر تقریباً منصفانه است، اما:

- **least-loaded نیست.** اگر اکانت A یک فایل ۱۰GB در دست داشته باشد و اکانت B صد فایل کوچک، هر دو «یک اسلات مشغول» شمرده می‌شوند؛ انتخاب بعدی به‌طور کور به A هم می‌رسد. شمارش درخواست، جای شمارش بایت/زمان را گرفته است.
- **ظرفیت ناهمگون را نمی‌فهمد.** اکانت premium و غیر-premium، یا اکانت با پروکسی کند و سریع، وزن یکسان دارند.
- **`_rr` جهانی است** و با health probe و acquireهای دیگر شلوغ می‌شود؛ عدالت per-key/per-kind تضمین‌شده نیست.
- **زمان انتظار `acquire` فقط ۳۰ ثانیه است.** اگر همه بک‌اندها مشغول/فلود باشند، `NoBackendAvailable` می‌دهد و job با backoff retry می‌شود؛ یعنی به‌جای صف منصفانه، شکست موقت و مصرف attempt داریم.

### ۴.۳ صف per-account وجود ندارد

هیچ ساختاری مثل «صف مخصوص acc:2» یا «وزن acc:2» نیست. یک job آپلود، اکانت را در لحظه‌ی اجرا انتخاب می‌کند و در payload فقط `backend: telegram` ثبت می‌شود، نه `account_key`. اگر `send_document` موفق شود ولی نوشتن DB شکست بخورد، retry می‌تواند همان فایل را روی **اکانت دیگری** دوباره بفرستد → پیام تکراری و مصرف دوباره‌ی پهنای باند. هیچ idempotency key یا بررسی partهای موجود قبل از retry وجود ندارد.

---

## ۵) نیاز ۳: نرخ ارجاع و محدودیت هر اکانت/ربات

### ۵.۱ اکانت‌ها: فقط FloodWait واکنشی

هیچ token bucket یا نرخ درخواست per-account وجود ندارد؛ تنها کنترل، sem همزمانی است. Telegram هم عدد ثابتی اعلام نمی‌کند: FloodWait به **متد + پارامترها** بسته است، نه یک نرخ جهانی. (docs.telethon.dev، core.telegram.org/api/errors). بنابراین «۲MB/s» یک فرض عملیاتی است، نه محدودیت مستند؛ مکانیزم واقعی `FLOOD_WAIT_X` و `FLOOD_PREMIUM_WAIT_X` است. منابع معتبر می‌گویند سرعت تک‌جریان در عمل ~۰.۵–۱.۵MB/s است و دانلود موازی فقط تا وقتی کمک می‌کند که بودجه‌ی FloodWait جا داشته باشد.

دو باگ مهم در مسیر FloodWait:

1. **سقف ۶۰ ثانیه‌ای روی retry** (`app/queue/queue_manager.py:750`):
   `delay=min(exc.seconds, RETRY_MAX_DELAY)` با `RETRY_MAX_DELAY=60`. اگر FloodWait=۳۰۰ ثانیه باشد، job بعد از ۶۰ ثانیه دوباره تلاش می‌کند، در حالی‌که اکانت تا ۳۰۰ ثانیه در `_flood_until` است. `acquire` هم ۳۰ ثانیه صبر می‌کند و بعد `NoBackendAvailable` می‌دهد؛ این چرخه attempts را می‌سوزاند.
2. **attempt مصرف می‌شود.** با `max_retries=5` و یک اکانت، یک FloodWait طولانی می‌تواند job را **قبل از آزادشدن اکانت** برای همیشه failed کند. محاسبه‌ی تقریبی با کد فعلی:

| FloodWait | نتیجه |
|---:|---|
| ۱۲۰s | موفق |
| ۳۰۰s | failed در ~۱۸۰s |
| ۶۰۰s | failed در ~۱۸۰s |
| ۱۸۰۰s | failed در ~۱۸۰s |

یعنی فایل ۳۵۷MB یا ۱۰GB بعد از یک فلود ۵ دقیقه‌ای، «شکست خورده» می‌شود و tmp آن تا پاک‌سازی janitor می‌ماند.

### ۵.۲ circuit breaker نیمه‌باز ندارد

`release` بعد از ۵ خطای پیاپی، بک‌اند را ۳۰۰ ثانیه از چرخش خارج می‌کند (`CIRCUIT_THRESHOLD=5`, `CIRCUIT_COOLDOWN=300`). بعد از cooldown، بک‌اند **با ظرفیت کامل** برمی‌گردد؛ هیچ probe تکی، half-open، یا افزایش تدریجی وجود ندارد. اگر مشکل هنوز برطرف نشده باشد، دوباره flood/خطا و چرخه‌ی بعدی. best practice: half-open با ۱–۱۰ probe، و cooldown پله‌ای.

### ۵.۳ ربات‌ها: بدون سقف و بدون بررسی خطا

`BotService.deliver_download` (`app/tg/bot_service.py:222`):

```python
with open(local_path, "rb") as fh:
    files = {"document": (name, fh, "application/octet-stream")}
    await self._http.post(f"{base}/bot{tok}/sendDocument", ...)
```

- **پاسخ بررسی نمی‌شود.** نه `resp.json()["ok"]`، نه `raise_for_status`، نه `Retry-After`. اگر Telegram ۴۲۹/۵۰۰ بدهد، job با موفقیت تمام می‌شود، فایل tmp حذف می‌شود و کاربر هیچ فایلی نمی‌گیرد (silent data loss).
- **هیچ semaphoreای ندارد.** تا ۴ worker دانلود می‌توانند همزمان برای همان ربات `sendDocument` بزنند؛ Bot API برای هر چت ~۱ پیام/ثانیه و به‌طور کلی ~۳۰ پیام/ثانیه توصیه می‌کند و در ۴۲۹ `retry_after` می‌دهد. اینجا نه محدودیت، نه احترام به retry_after.
- **polling هر ربات یک task مستقل است** ولی delivery مشترک با workerهای دانلود است؛ صف per-bot وجود ندارد.
- **`sendDocument` از فایل کامل روی دیسک استفاده می‌کند** و timeout کلاینت ۶۵ ثانیه است؛ برای فایل ۵۰MB روی uplink کند، timeout → retry کامل → دانلود دوباره از تلگرام. 

---

## ۶) مسیرهایی که در سؤال نیستند ولی رفتار بار را تعیین می‌کنند

### ۶.۱ دانلود HTTP خارج از صف و نگه‌داشتن اسلات

`streaming.file_response` اسلات را **قبل از شروع پاسخ** می‌گیرد و تا پایان ارسال (یا قطع کلاینت) نگه می‌دارد. نتیجه:

- دو کلاینت کند/ویدیوی paused می‌توانند هر دو اسلات اکانت را بگیرند؛ دانلودهای ربات و استریم‌های دیگر ۵۰۳ می‌گیرند (بعد از ۳۰ ثانیه acquire).
- هر seek ویدیو یک درخواست Range جدید و یک اسلات جدید است؛ سقف per-IP/per-key برای استریم هم‌زمان وجود ندارد.
- هیچ سقف egress یا fair-share بین دانلودها نیست؛ فقط sem.
- `Cache-Control: public, max-age=3600` روی محتوای احراز‌شده هم گذاشته می‌شود (خارج از موضوع بار، ولی ریسک کش/CDN اشتباه).

### ۶.۲ عدم admission control در ورودی

`POST /files/upload` کل فایل را روی دیسک می‌نویسد (`while chunk := await file.read(...)`) و بعد enqueue می‌کند. هیچ‌کدام از این‌ها وجود ندارد:

- سقف تعداد jobهای pending یا سقف بایت در جریان،
- watermark فضای دیسک،
- سقف آپلود هم‌زمان per API key (فقط RPM؛ با rpm=120 می‌توان ۱۲۰ آپلود ۱۰GB را در یک ثانیه پذیرفت)،
- سقف job هم‌زمان per key.

`max_upload_size` در استقرار زنده **۱۰GB** است (env=2.1GB، db=10GB)، درحالی‌که `split_threshold=1.9GB`. دو آپلود هم‌زمان ۱۰GB = ۲۰GB tmp، به‌علاوه‌ی partهای split (تا ۱۰GB دیگر برای هر job) → فشار دیسک بدون هیچ توقف.

### ۶.۳ سهمیه‌ی روزانه در حافظه و ناقص

`quota` در `app/core/rate_limit.py` یک dict در حافظه‌ی پروسس است:

- با restart صفر می‌شود؛ در multi-node هر node سهمیه‌ی جدا دارد.
- در `/upload` **قبل از** افزودن بایت چک می‌شود، پس یک فایل می‌تواند از سقف عبور کند؛ بعد از درخواست `quota.add` می‌شود.
- در `/content` کل `size` فایل اضافه می‌شود، حتی برای Range/HEAD یا دانلود نیمه‌کاره؛ یک پلیر ویدیو با ۲۰ درخواست Range، ۲۰ برابر حجم فایل را به سهمیه می‌زند.

### ۶.۴ بازیابی و multi-node

`QueueManager._recover` هر job با status `running`/`lease` را **بدون توجه به `lease_owner`** به `pending` برمی‌گرداند:

```sql
SELECT * FROM jobs WHERE status IN ('pending','retry','running','lease')
...
if row["status"] in ("running", "lease"): status = "pending"
```

در multi-node، ری‌استارت یک node باعث می‌شود jobهای در حال اجرای nodeهای دیگر دوباره اجرا شوند (آپلود تکراری، حذف تکراری). در استقرار زنده `nodes: 3` دیده می‌شود؛ اگر واقعاً چند replica فعال است، این یک ریسک جدی است.

همچنین در استقرار زنده:

- `redis_enabled=True` است، اما مسیر claim روی MySQL از Redis استفاده نمی‌کند؛ Redis فقط یک نسخه‌ی دوم برای enqueue/recover است. `heap_size=19` با `pending=0` نشان می‌دهد heap بازیابی‌شده در مسیر MySQL هرگز تخلیه نمی‌شود (نشتی حافظه‌ی کوچک و گمراه‌کننده).
- `docker-compose.redis.yml` روی SQLite کار می‌کند؛ در آن مسیر، بازیابی هم از Redis و هم SQLite انجام می‌شود و ورودی‌های تکراری heap می‌سازد. اگر دو worker هم‌زمان یک job را بردارند، بازنده `rows_affected=0` می‌گیرد، `_next_job` مقدار `None` برمی‌گرداند و `_worker_loop` **return** می‌کند؛ یعنی worker بی‌صدا می‌میرد. (در MySQL این مسیر استفاده نمی‌شود.)

### ۶.۵ resume آپلود واقعاً resume نیست

در `_handle_upload` اگر `resumed_offset` داشته باشیم، `_send_streaming` صدا زده می‌شود. ولی داخل آن:

```python
fh.seek(from_offset)
...
chunk = fh.read(1024 * 1024)
result = await be.send_document(storage_chat, tmp_path, name, mime, ...)
```

`send_document` **کل مسیر فایل** را می‌فرستد، نه chunk را. یعنی آپلود از offset ادامه نمی‌دهد؛ دوباره از صفر ارسال می‌شود. همچنین در `finally`:

```python
await UploadSessionRepo(self.db).advance(file_id, size)
```

به‌جای `session_id`، `file_id` به `advance` داده می‌شود و عملاً هیچ session‌ای به‌روز نمی‌شود. نتیجه: هر retry یک آپلود کامل جدید است، با همان مصرف پهنای باند و همان ریسک FloodWait. برای فایل ۱۰GB این تفاوت بین «ادامه» و «۱۸۰ دقیقه دوباره» است.

---

## ۷) مقایسه با بهترین روش‌ها (بر پایه‌ی منابع)

| موضوع | وضعیت فعلی | روش پیشنهادی | چرا / منبع |
|---|---|---|---|
| ترتیب jobها | اولویت نوع + FIFO ms | SJF اندازه‌آگاه + محافظ گرسنگی (oldest override یا دو-لاین) | SJF میانگین انتظار را کم می‌کند؛ aging خطی در saturation به FIFO برمی‌گردد. مرز گرسنگی باید زمانی باشد، نه امتیازی. (third-bit.com priority starvation، arpalert aging) |
| عدالت بین اکانت‌ها | round-robin + sem برابر | DRR با quantum متناسب ظرفیت (bytes) یا P2C وزن‌دار | DRR هزینه‌ی متغیر (بایت) را native می‌فهمد و bound دارد؛ P2C از herd شدن جلوگیری می‌کند. (NVIDIA Dynamo DRR، Envoy load balancing، F5 P2C) |
| ظرفیت ناهمگون | همه یک وزن | وزن = ظرفیت اعلامی/اندازه‌گیری‌شده؛ premium ضریب؛ کاهش وزن با صف/خطا | Envoy: `weight = w / (active+1)^bias`؛ slow-start برای بازگشت. |
| محدودیت نرخ | فقط sem | token bucket per (account, method) + AIMD concurrency | 429/FloodWait را باید به کاهش کنترل‌شده تبدیل کرد، نه retry کور. (Vector ARC، Netflix concurrency-limits، Stripe rate limits) |
| FloodWait | retry با سقف ۶۰s | احترام کامل به `seconds`، پارک اکانت، عدم مصرف attempt در انتظار | `FLOOD_WAIT_X` دقیقاً همان مدت را می‌خواهد؛ Telegram می‌گوید نادیده‌گرفتنش cooldown را طولانی‌تر می‌کند. (core.telegram.org/api/errors، grammy flood) |
| Circuit breaker | ۵ خطا / ۳۰۰s، بازگشت کامل | half-open با probe محدود، cooldown پله‌ای | Azure/Resilience4j؛ بازگشت ناگهانی باعث flood دوباره می‌شود. |
| ربات‌ها | بدون سقف/بدون چک پاسخ | sem per bot + بررسی `ok` + `retry_after` + صف delivery | Bot API 429 و محدودیت ~۱ پیام/ثانیه در چت. |
| Backpressure | هیچ | سقف صف/بایت/دیسک + 429/503 با `Retry-After` | صف بی‌نهایت = انتقال overload به دیسک و حافظه. (queue design under load) |
| Observability | شمارنده‌ها، بدون wait/age | histogram انتظار به تفکیک kind/lane، oldest age، drain time، utilization per backend | depth تنها شاخص lagging است؛ age و tail زودتر هشدار می‌دهند. |

---

## ۸) طرح پیشنهادی، مرحله‌به‌مرحله

### فاز ۰ — اصلاحات فوری (بدون تغییر معماری، ۱–۲ روز)

1. **سقف sem را درست کن:** `max_concurrent_downloads` را به‌عنوان سقف **هر اکانت** بگیر (نه تقسیم بر تعداد بک‌اند). ربات‌ها/eitaa را از مخرج حذف کن. سقف آپلود را جدا کن. sem را هنگام افزودن/حذف بک‌اند بازسازی کن.
2. **FloodWait را کامل احترام بگذار:** `delay = max(0.5, exc.seconds)` بدون سقف ۶۰s؛ اگر مدت انتظار از یک آستانه بیشتر است، job را `retry` کن ولی **attempt را مصرف نکن** و `next_run_at` را دقیقاً روی `flood_until` بگذار. `acquire` هم تا `flood_until` صبر کند یا فوراً `retry` با زمان دقیق بدهد.
3. **پاسخ ربات را چک کن:** `resp.raise_for_status()`، `data["ok"]`، و `retry_after`؛ در صورت خطا job را retry کن (و temp را نگه دار)، نه done.
4. **janitor را امن کن:** فقط فایل‌های tmp را حذف کن که در `jobs` با status pending/running/retry ارجاع نشده‌اند؛ TTL را از «۱ ساعت» به «بیشتر از حداکثر زمان مجاز یک job» ببر (مثلاً ۲۴ ساعت) و mtime را در طول آپلود تازه کن.
5. **admission control حداقلی:** سقف job هم‌زمان per key، سقف bytes in flight، watermark دیسک (مثلاً ۸۰٪) و پاسخ 429/503 با `Retry-After`.
6. **multi-node recovery را امن کن:** فقط leaseهای منقضی (`lease_until < now`) یا متعلق به همین node را reset کن.
7. **متریک‌های انتظار:** `queue_wait_seconds{kind,lane}`، `queue_oldest_age`، `backend_inflight`، `backend_flood_total`، `bot_429_total`، `tmp_bytes`.

### فاز ۱ — صف اندازه‌آگاه (۳–۵ روز)

1. ستون‌های `size_bytes`، `lane`، `deadline_at` را به `jobs` اضافه کن (مهاجرت سبک در `db.py`).
2. دو lane: `small` (مثلاً ≤۶۴MB) و `bulk`. هر lane حداقل یک worker رزرو داشته باشد؛ worker بیکار به lane دیگر کمک کند (work-conserving). این با ۲ worker آپلود هم قابل پیاده‌سازی است (یکی small، یکی bulk، با کمک متقابل).
3. داخل هر lane: SJF با `size_bytes`، و **oldest override**: اگر `age > T_max` (مثلاً ۱۵ دقیقه) یا هر K-امین dispatch، قدیمی‌ترین job انتخاب شود. `T_max` را قابل تنظیم کن.
4. اولویت نوع را به سهم رزرو تبدیل کن، نه برتری مطلق: مثلاً ۱ worker همیشه برای آپلود رزرو باشد تا دانلود مداوم آپلود را کامل گرسنه نکند. (در بار реальн، اگر دانلودها مداوم برسند، آپلود با اولویت ۴۰ هرگز اجرا نمی‌شود.)
5. تست‌های scheduler با fake clock: bound انتظار، عدم گرسنگی، tie در `seq`. `seq` را به یک شمارنده‌ی یکنوا (DB sequence/Redis INCR) تغییر بده، نه ms.

### فاز ۲ — توزیع منصفانه و کنترل نرخ (۱–۲ هفته)

1. **DRR per account:** quantum متناسب وزن اکانت (premium/غیر premium/throughput اندازه‌گیری‌شده)؛ cost = bytes. اگر quantum < max job size، از bulk credit (چند round پرش) استفاده کن تا اسکن حلقه‌ای هزینه‌ی فایل ۱۰GB نشود.
2. **P2C وزن‌دار** برای انتخاب اکانت: دو کاندید تصادفی، انتخاب کم‌بارترین با وزن `w/(active+1)`؛ وزن از `is_premium`، نرخ موفقیت، و صف فعلی.
3. **token bucket per (account, method):** `get_file`، `send_file`، `delete` جدا؛ نرخ اولیه محافظه‌کارانه و تنظیم با AIMD: افزایش خطی در موفقیت، کاهش ضربی در FloodWait/429. سقف همزمانی هم جداگانه AIMD شود.
4. **half-open breaker:** بعد از cooldown فقط ۱–۳ probe؛ در صورت موفقیت بازگشت تدریجی (slow start).
5. **صف per bot + سقف delivery:** هر ربات یک sem (مثلاً ۱–۲) و احترام به `retry_after`؛ دانلود هم‌زمان زیادی برای یک ربات نداشته باش.

### فاز ۳ — استریم منصفانه (۱ هفته)

1. دانلود HTTP را یا از یک **صف دانلود** با workerهای اختصاصی رد کن، یا حداقل یک sem fair-share با سقف per-IP/per-key و timeout کوتاه‌تر بگذار.
2. اسلات را بعد از اولین بایت یا با buffer محدود آزاد کن؛ نگه‌داشتن اسلات تا پایان ارسال به کلاینت کند، ریشه‌ی ۵۰۳های غیرمنصفانه است.
3. سقف egress اختیاری (bytes/sec) برای هر اکانت؛ اگر تلگرام محدودیت واقعی دارد، همان را به‌عنوان budget بگذار، نه عدد هاردکد ۲MB/s.
4. `HEAD`/`Range` را از شمارش سهمیه/دانلود کامل جدا کن.

### اسکیمای پیشنهادی (نمونه)

```sql
ALTER TABLE jobs ADD COLUMN size_bytes BIGINT NOT NULL DEFAULT 0;
ALTER TABLE jobs ADD COLUMN lane VARCHAR(16) NOT NULL DEFAULT 'bulk';
ALTER TABLE jobs ADD COLUMN deadline_at DOUBLE NOT NULL DEFAULT 0;
ALTER TABLE jobs ADD COLUMN account_key VARCHAR(32) NOT NULL DEFAULT '';
CREATE INDEX idx_jobs_pick ON jobs(status, lane, priority, size_bytes, seq);
```

```python
# انتخاب داخل lane (small) — قابل تنظیم
def pick(cands, now):
    overdue = [j for j in cands if now - j.created_at > T_MAX]
    if overdue:
        return min(overdue, key=lambda j: j.created_at)      # oldest first
    return min(cands, key=lambda j: (j.size_bytes, j.created_at))  # SJF
```

```python
# وزن اکانت برای DRR/P2C
weight = base_weight(account) * (2.0 if account.is_premium else 1.0)
weight *= max(0.1, success_rate_ewma)
effective = weight / (inflight + 1) ** bias   # bias ~1
```

---

## ۹) اندازه‌گیری و معیار پذیرش

پیشنهاد تست‌ها (بدون نیاز به Telegram واقعی؛ FakeBackend با `fake_send_delay_s`):

1. **صف:** ۲۰۰ job با اندازه‌های lognormal و ورود burst؛ assert:
   - انتظار فایل ≤۵MB از `T_max` بیشتر نشود،
   - هیچ jobی از `2*T_max + max_service` بیشتر منتظر نماند،
   - ترتیب tie با `seq` یکنوا قطعی باشد.
2. **اکانت:** ۳ اکانت با وزن ۲:۱:۱ و ۳۰۰ job؛ سهم بایت هر اکانت باید در بازه‌ی ±۵٪ وزن باشد (شبیه تست ۳:۲:۱ در ADR).
3. **FloodWait:** FloodWait=۶۰۰s روی تنها اکانت؛ job باید بعد از آزادشدن موفق شود، نه failed.
4. **ربات:** ۴۲۹ با `retry_after`؛ job باید retry شود و no silent done.
5. **بار:** ۵۰ آپلود هم‌زمان ۱GB؛ دیسک از watermark عبور نکند و ورودی جدید 429 بگیرد؛ هیچ worker نمیرد (`worker_alive` metric).
6. **رگرسیون:** تست‌های فعلی queue/concurrency/pressure باید پاس بمانند.

معیارهای پنل: `queue_oldest_age`، `queue_drain_seconds` (depth / نرخ تخلیه)، `backend_inflight`، `backend_flood_rate`، `bot_429`، `tmp_bytes`، `worker_alive`.

---

## ۱۰) ریسک‌های فوری استقرار فعلی

1. **رمز پنل ضعیف و عمومی است.** باید فوراً چرخش کند؛ همچنین دسترسی پنل به IP محدود یا 2FA بگیرد.
2. **تک‌اکانت = SPOF.** با یک اکانت و یک FloodWait طولانی، کل صف آپلود می‌خوابد؛ حداقل ۲ اکانت و circuit/half-open لازم است.
3. **max_upload_size=10GB با ۲ worker و ~۲MB/s** یعنی هر فایل تا ۸۵ دقیقه یک worker را می‌گیرد؛ با janitor ۱ ساعته، احتمال از دست رفتن tmp بالاست.
4. **Redis روشن است ولی claim از MySQL می‌آید**؛ مقدار `heap_size=19` با صف خالی نشانه‌ی عدم تطابق recovery و claim است. یا Redis را از مسیر صف حذف کنید یا claim را واقعاً Redis-based کنید.
5. **recovery در multi-node** هر job در حال اجرا را pending می‌کند؛ اگر `nodes: 3` واقعی است، آپلود/حذف تکراری رخ می‌دهد.
6. **resume آپلود کار نمی‌کند**؛ هر خطا = ارسال کامل دوباره. برای فایل‌های بزرگ این بزرگ‌ترین مصرف‌کننده‌ی پهنای باند و FloodWait است.

---

## ۱۱) جمع‌بندی

- صف پایدار و atomic claim و retry/backoff پایه‌ی خوبی است؛ مشکل در **سیاست** است، نه در وجود صف.
- برای رسیدن به سه نیاز شما، حداقل سه تغییر بنیادی لازم است: (۱) اندازه و قدمت وارد کلید انتخاب شود، (۲) توزیع بین اکانت‌ها بر اساس ظرفیت/وزن و با صف per-account باشد، (۳) نرخ هر اکانت/ربات با token bucket + AIMD و احترام کامل به FloodWait/429 کنترل شود.
- قبل از هر بازطراحی، چهار باگ عملیاتی باید فوراً بسته شوند: سقف sem، retry فلود، janitor روی tmp صف‌شده، و بررسی پاسخ ربات.
- اعداد شبیه‌سازی نشان می‌دهد حتی یک تغییر ساده (دو-لاین small/bulk) انتظار فایل‌های کوچک را از ~۴۰ دقیقه به ~۲ دقیقه کاهش می‌دهد، بدون گرسنه‌کردن فایل‌های بزرگ. SJF خالص بهتر است ولی بدون محافظ، فایل بزرگ را گرسنه می‌کند.

---

## ۱۲) منابع

- Telethon errors: https://docs.telethon.dev/en/stable/concepts/errors.html
- Telethon client (flood_sleep_threshold / iter_download): https://docs.telethon.dev/en/stable/modules/client.html
- Telegram files API (part limits, parallel ops, premium throttle): https://core.telegram.org/api/files
- Telegram errors (FLOOD_WAIT, AUTH_KEY_DUPLICATED): https://core.telegram.org/api/errors
- Telegram config (queue limits, premium speedup): https://core.telegram.org/api/config
- grammY flood guide: https://grammy.dev/advanced/flood
- Priority starvation / aging trade-off: https://third-bit.com/2026/06/05/priority-starvation/
- Aging formula و rarity bonus: https://www.arpalert.org/go_sched_en.html
- NVIDIA Dynamo DRR: https://docs.nvidia.com/dynamo/knowledge-base/modular-components/router/deficit-round-robin
- Fair queueing ADR (DRR bounds, hasCapacity/canServe): https://github.com/jamarius-fortson/agent-gateway/blob/main/docs/adr/0002-fair-queueing-is-weighted-deficit-round-robin.md
- DRR pseudocode: https://www.cs.emory.edu/~cheung/Courses/558/Syllabus/11-Fairness/DRR.html
- WFQ/SFQ: https://intronetworks.cs.luc.edu/current/uhtml/fairqueuing.html
- LAS/MLFQ (skip-join): https://arxiv.org/html/2305.05920v3
- Bounded queue/backpressure: https://www.myrobertson.com/blog/queue-design-under-load
- Netflix concurrency limits: https://github.com/Netflix/concurrency-limits
- Vector adaptive concurrency: https://vector.dev/blog/adaptive-request-concurrency/
- Stripe rate limits: https://docs.stripe.com/rate-limits
- Temporal rate limiting downstream: https://docs.temporal.io/guides/rate-limit-downstream-apis
- Azure circuit breaker: https://learn.microsoft.com/en-us/azure/architecture/patterns/circuit-breaker
- Resilience4j circuit breaker: https://resilience4j.readme.io/docs/circuitbreaker
- Envoy load balancing (weighted least request): https://www.envoyproxy.io/docs/envoy/latest/intro/arch_overview/upstream/load_balancing/load_balancers
- NGINX P2C: https://www.f5.com/company/blog/nginx/nginx-power-of-two-choices-load-balancing-algorithm
- Retry-After: https://developer.mozilla.org/en-US/docs/Web/HTTP/Reference/Headers/Retry-After
