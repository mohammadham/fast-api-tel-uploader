# AGENTS.md — learnings

## محیط و ابزارها
- Windows + Git Bash؛ venv در `.venv/Scripts/python.exe`؛ تست: `timeout 300 .venv/Scripts/python.exe -m pytest -q`
- **pytest فقط با `.venv/Scripts/python.exe`** — interpreter مستقل uv (`AppData\Roaming\uv\python\...`) telethon/python_socks ندارد؛ `build_telethon_proxy` بدون آن‌ها None برمی‌گرداند. دو تست `test_build_telethon_*` در test_proxies.py `pytest.importorskip` دارند و در interpreter ناقص SKIP می‌شوند (نه fail) — پس skip در این دو تست یعنی venv اشتباه است. با venv درست: همه‌ی ۱۱۰ تست pass بدون skip
- e2e پنل: `npx playwright test` (براوزرها در LOCALAPPDATA/ms-playwright)؛ config خودش uvicorn fake-TG را روی 8712 بالا می‌آورد — `python -m playwright test` کار نمی‌کند
- CI: `.github/workflows/playwright.yml` در هر push دو job — pytest با `.venv/bin/python` (بعد از `pip install -r requirements.txt` + preflight import-check) و e2e با build تازه‌ی پنل؛ مسیر webServer در playwright.config.mjs پلتفرم‌محور است (`.venv/bin/uvicorn` در Linux)؛ `python-socks` در requirements.txt و pyproject باید بماند (preflight CI آن را verify می‌کند و دو تست پراکسی importorskip هم به‌عنوان safety-net دارند)
- `str_replace` و `read_files` گاهی dotfileها (`.env.example`) را «not found» می‌کنند → با python ویرایش کن
- ripgrep گاهی ENOENT می‌دهد → جایگزین: `grep -n` در shell
- `run_terminal_command` BACKGROUND پیاده نشده → سرور detached با PowerShell Start-Process (run-doc در `.freebuff/run.md`)
- سرور/سشن‌ها با ری‌استارت Freebuff می‌میرند؛ `.env` باید secret/FAKE_TG ثابت نگه دارد (وگرنه decrypt سشن می‌شکند)

## معماری پروژه (TelegramDrive)
- همه‌ی انتقال‌ها از استخر `TGManager` می‌گذرند (`acc:`/`bot:`/`eit:`) — مستقیم به Telethon/API نزن
- مسیریابی بک‌اند: `files.backend` از `api_keys.backend` یا `TGDRIVE_DEFAULT_BACKEND` هنگام enqueue اسنپ‌شات می‌شود؛ دانلود/حذف/استریم از رکورد فایل
- تست‌ها با `TGDRIVE_FAKE_TG=1`؛ `FakeBackend.STORE` مشترک است و باید بین تست‌ها پاک شود (conftest این کار را می‌کند)
- جاب‌های صف شکست را با backoff retry می‌کنند (max_retries پیش‌فرض ۵) — تست شکست باید تا status=failed منتظر بماند نه یک poll کوتاه
- حذف نرم (trash) پیش‌فرض DELETE است؛ `?purge=true` برای حذف تلگرامی
- مهاجرت‌های سبک ستونی در `db.py` (لیست ALTER با try/except) — جدول جدید را به SCHEMA اضافه کن، ستون جدید را به لیست مهاجرت

## ایتا (ایتایار)
- API فقط ارسال: `POST /api/{token}/sendFile` multipart → `{"status":"success","message_id":...}`؛ دانلود فقط اسکرپ صفحه‌ی عمومی کانال؛ حذف API ندارد
- مقصد ایتا باید کانال عمومی باشد و @sender مدیرش شود
- **درس regex (باگ c21c61c):** در `_DOWNLOAD` (eitaa_backend.py) علامت `?` لیترالِ URL باید `\?` باشد. الگوی `...{8,64}?token=` کامپایل می‌شود و SyntaxError نمی‌دهد، ولی `?` را lazy-quantifier می‌گیرد → الگو دنبال `token=` بلافاصله بعد از hex می‌گردد و هرگز با URL واقعی (`hex?token=`) match نمی‌شود. علامت: تست‌های اسکرپر همیشه None برمی‌گردانند و به‌اشتباه شبیه «تغییر markup ایتا» به‌نظر می‌رسند. قبل از مقصر دانستن markup سایت، regex را با URL نمونه‌ی خود تست جدا تست کن (`re.search(pattern, sample_url)`).
