# 🗺 GRAPH.md — گراف معماری و وابستگی تصمیمات

## ۱. گراف کامپوننت‌ها (DB → Core → TG → Queue → API → Panel)

```
                       ┌────────────────────────────┐
                       │        static/ (SPA)       │
                       └─────────────┬──────────────┘
                                     │ fetch (JWT)
┌────────────────────────────────────┼─────────────────────────────────────┐
│ FastAPI app.main                   │                                     │
│  ┌──────────┐  ┌──────────┐  ┌─────┴─────┐  ┌────────┐  ┌──────────┐   │
│  │ auth.py  │  │accounts  │  │ files.py  │  │ queue  │  │ admin    │   │
│  │ (JWT)    │  │ bots.py  │  │ (+ /d/)   │  │ .py    │  │ metrics  │   │
│  └────┬─────┘  └────┬─────┘  └─────┬─────┘  └───┬────┘  └────┬─────┘   │
│       │             │              │            │            │         │
│  ┌────┴─────────────┴──────────────┴────────────┴────────────┴─────┐   │
│  │           core: security / rate_limit / deps / config           │   │
│  └────┬───────────────┬───────────────────┬───────────────┬────────┘   │
│       │               │                   │               │            │
│  ┌────┴────┐   ┌──────┴──────┐   ┌────────┴──────┐  ┌─────┴──────┐     │
│  │ SQLite  │   │ QueueManager│──▶│ TGManager     │  │ BotService │     │
│  │ (WAL)   │◀──│ persistence │   │ (Telethon池)  │◀─│ (aiogram)  │     │
│  └─────────┘   └─────────────┘   └───────┬───────┘  └────────────┘     │
│                                          │                             │
│                            ┌─────────────┴─────────────┐               │
│                            │ fast_upload / fast_download│               │
│                            │ (FastTelethon) + splitter │               │
│                            └───────────────────────────┘               │
└────────────────────────────────────────────────────────────────────────┘
                                     │ MTProto
                              ┌──────┴──────┐
                              │  Telegram   │
                              └─────────────┘
```

## ۲. گراف جریان داده‌ها

### آپلود
```
Client ──multipart──▶ files.py ──tmp──▶ jobs(UL,40) ──▶ Worker(UL)
  Worker ──▶ TGManager.acquire() ──▶ splitter?(>1.9GB) ──▶ fast_upload
  ──▶ files+file_parts rows (ready) ──▶ tmp حذف ──▶ webhook (اختیاری)
```

### دانلود/استریم (اولویت 100)
```
Client ──GET /d/{id} (Range?)──▶ files.py ──▶ jobs(DL,100)
  ──▶ Worker(DL) ──▶ TGManager.acquire() ──▶ fast_download
  chunk──▶ StreamingResponse ──▶ merge پارت‌ها (اگر multi-part)
```

### بات
```
Telegram ──update──▶ BotService ──┬─▶ /upload (≤50MB) ──▶ jobs(UL,40)
                                  ├─▶ /download ──▶ jobs(DL,100) ──▶ sendDocument
                                  └─▶ /queue /status ──▶ QueueManager.stats
```

## ۳. گراف وابستگی تصمیم‌ها (DECISIONS.md)

```
D13(محدودیت‌های TG)
 ├─▶ D1 (Telethon+FastTelethon)  ─▶ D5 (سشن رمز Fernet)
 ├─▶ D2 (aiogram فقط ≤50MB)
 ├─▶ D3 (صف داخلی + اولویت DL) ─▶ D8 (اولویت دانلود)
 └─▶ D4 (کانال خصوصی + پارت‌بندی 1.9GB)

D3 ─▶ D9 (FloodWait/backoff/circuit-breaker)
D5+D6 ─▶ امنیت پنل/API
D10 (FakeTG) ─▶ همه تست‌ها
D12 (SQLite WAL) ─▶ D3 (persistence)
```

## ۴. گراف جداول دیتابیس

```
users ──< api_keys
users ──< audit_log
tg_accounts ──< jobs (account_id)
bot_tokens
files ──< file_parts
files ──< jobs (file_id)
upload_sessions ──▶ files (resumable)
```

## ۵. نقاط اتصال آینده (scale-out)
- QueueManager پشت اینترفیس است → در v2 با Redis backend جایگزین می‌شود بدون تغییر APIها
- Storage نیز اینترفیس دارد → افزودن S3-cache به‌صورت لایه‌ی جلوی Telegram ممکن است
- BotService مستقل است → چند بات همزمان پشتیبانی می‌شود
