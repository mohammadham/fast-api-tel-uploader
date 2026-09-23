# AGENTS.md — learnings

## محیط و ابزارها
- Windows + Git Bash؛ venv در `.venv/Scripts/python.exe`؛ تست: `timeout 300 .venv/Scripts/python.exe -m pytest -q`
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
