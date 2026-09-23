# TelegramDrive — run doc

## Reproduce artifacts
- venv: `.venv/Scripts/python.exe -m venv .venv` then `.venv/Scripts/python.exe -m pip install -r requirements.txt pytest pytest-asyncio asgi-lifespan` (or `uv pip install -p .venv -r requirements.txt`)
- No `.env` needed for dev preview: run with `TGDRIVE_FAKE_TG=1` (in-memory fake telegram) and `TGDRIVE_DATA_DIR=data` (auto-created). Admin password auto-generated at `data/initial_admin_password.txt` on first start.

## Run server (port 8000, uvicorn from venv, detached)
```powershell
powershell -NoProfile -Command "(Start-Process -FilePath '.venv\Scripts\uvicorn.exe' -ArgumentList 'app.main:app','--host','127.0.0.1','--port','8000' -RedirectStandardOutput '.freebuff\preview.log' -RedirectStandardError '.freebuff\preview.log.err' -WindowStyle Hidden -PassThru).Id"
```
Env for the process (set before Start-Process in the same shell): `TGDRIVE_FAKE_TG=1`, `TGDRIVE_SECRET=dev-secret-change-me-please-32-chars!`
Health check: `curl http://127.0.0.1:8000/api/v1/admin/healthz` → `{"ok":true}`
Panel: `http://127.0.0.1:8000/` (login: admin / password from data/initial_admin_password.txt)
