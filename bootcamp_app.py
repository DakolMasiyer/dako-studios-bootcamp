#!/usr/bin/env python3
"""
Dako Studios Bootcamp — FastAPI + SQLite teaching platform
100 concurrent students | 20-day digital skills curriculum
Freemium: Days 1-3 free, Days 4-20 require payment (Flutterwave)
"""
import json
import os
import base64
import hmac
import secrets
import hashlib
import sqlite3
import uuid
from datetime import datetime, timedelta
from pathlib import Path

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, Request, Form, File, UploadFile, HTTPException, Response
from fastapi.responses import HTMLResponse, RedirectResponse, FileResponse, StreamingResponse
from translations import get_t, SUPPORTED_LANGS

try:
    from vercel.blob import AsyncBlobClient
except Exception:  # pragma: no cover - optional production dependency
    AsyncBlobClient = None

load_dotenv()

# ─── Config ───────────────────────────────────────────────────────────────────
def _is_vercel_runtime() -> bool:
    return os.getenv("VERCEL") == "1" or bool(os.getenv("VERCEL_ENV"))


def _resolve_storage_path(raw_value: str, fallback_name: str) -> Path:
    path = Path(raw_value)
    if path.is_absolute():
        return path
    if _is_vercel_runtime():
        return Path("/tmp") / fallback_name
    return path


DB_PATH  = _resolve_storage_path(os.getenv("SQLITE_PATH", "data/bootcamp.db"), "bootcamp.db")
UPLOADS  = _resolve_storage_path(os.getenv("UPLOADS_DIR", "uploads/screenshots"), "uploads/screenshots")
DB_PATH.parent.mkdir(parents=True, exist_ok=True)
UPLOADS.mkdir(parents=True, exist_ok=True)

FLW_SECRET  = os.getenv("FLUTTERWAVE_SECRET_KEY") or os.getenv("FLW_CLIENT_SECRET", "")
FLW_PUBLIC  = os.getenv("FLUTTERWAVE_PUBLIC_KEY") or os.getenv("FLW_CLIENT_ID", "")
FLW_SECRET_HASH = os.getenv("FLW_SECRET_HASH") or os.getenv("FLUTTERWAVE_WEBHOOK_SECRET", "")
FLW_CLIENT_ID = os.getenv("FLW_CLIENT_ID") or FLW_PUBLIC
FLW_CLIENT_SECRET = os.getenv("FLW_CLIENT_SECRET") or FLW_SECRET
PRICE_USD = float(os.getenv("BOOTCAMP_PRICE_USD", "49"))
PRICE_NGN = float(os.getenv("BOOTCAMP_PRICE_NGN", "75000"))


def _student_currency_price(student: dict) -> tuple[str, float]:
    country = (student.get("country") or "").lower()
    if "nigeria" in country:
        return "NGN", PRICE_NGN
    return "USD", PRICE_USD


def _currency_symbol(currency: str) -> str:
    return "₦" if currency == "NGN" else "$"
BASE_URL    = os.getenv("BASE_URL", "http://localhost:8000")
FREE_DAYS   = 3   # Days 1–FREE_DAYS are always free
ALLOW_PAYMENT_DEV_BYPASS = os.getenv("ALLOW_PAYMENT_DEV_BYPASS", "false").lower() in ("1", "true", "yes")

from payment_logger import log_payment_event

app = FastAPI(docs_url=None, redoc_url=None)

from fastapi.middleware.cors import CORSMiddleware

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ─── DB ───────────────────────────────────────────────────────────────────────
import queue

from db_adapter import db, DB_POOL, query, one, run
query = db.query
one = db.one
run = db.run
DB_POOL = db

if _is_vercel_runtime():
    try:
        from dako_bootcamp_init_db import init_db as _bootstrap_bootcamp_db

        def _has_bootcamp_schema(db_path: Path) -> bool:
            if not db_path.exists() or db_path.stat().st_size == 0:
                return False
            try:
                conn = sqlite3.connect(str(db_path), timeout=5)
                try:
                    row = conn.execute(
                        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='students'"
                    ).fetchone()
                    return row is not None
                finally:
                    conn.close()
            except Exception:
                return False

        if not _has_bootcamp_schema(DB_PATH):
            _bootstrap_bootcamp_db()
    except Exception:
        # Keep startup resilient; first request may still initialize the DB path.
        pass

# ─── Auth ─────────────────────────────────────────────────────────────────────

def _hash(pwd):  return hashlib.sha256(pwd.encode()).hexdigest()
def _token():    return secrets.token_urlsafe(32)

def _session_cutoff() -> str:
    return (datetime.utcnow() - timedelta(days=7)).isoformat()[:19]

def _get_student(request: Request):
    tok = request.cookies.get("s_token")
    if not tok: return None
    return one(
        "SELECT s.* FROM students s JOIN sessions se ON s.id=se.student_id"
        " WHERE se.token=? AND se.created_at > ?",
        (tok, _session_cutoff()),
    )

def _get_coach(request: Request):
    tok = request.cookies.get("c_token")
    if not tok: return None
    return one(
        "SELECT c.* FROM coaches c JOIN coach_sessions cs ON c.id=cs.coach_id"
        " WHERE cs.token=? AND cs.created_at > ?",
        (tok, _session_cutoff()),
    )

def _requires_payment(day_num: int, student: dict) -> bool:
    return day_num > FREE_DAYS and not student["paid_access"]

# ─── Language + i18n ──────────────────────────────────────────────────────────

VALID_LANGS = {"en", "pcm", "yo", "ha", "ig"}

def _get_lang(request: Request) -> str:
    lang = request.cookies.get("lang_pref", "en")
    return lang if lang in VALID_LANGS else "en"

def _lang_name(code: str) -> str:
    return get_t(code).get("lang_name", code.upper())

def _lang_switcher_flat(current_lang: str) -> str:
    return "".join(
        f'<form method="POST" action="/set-language">'
        f'<input type="hidden" name="lang" value="{code}">'
        f'<button type="submit" class="lang-option{" lang-active" if code == current_lang else ""}" style="width:100%;border-radius:6px">'
        f'<span class="lang-check">✓</span>{_lang_name(code)}'
        f'</button>'
        f'</form>'
        for code in SUPPORTED_LANGS
    )

def _lang_switcher(current_lang: str) -> str:
    options = "".join(
        f'<form method="POST" action="/set-language">'
        f'<input type="hidden" name="lang" value="{code}">'
        f'<button type="submit" class="lang-option{" lang-active" if code == current_lang else ""}">'
        f'<span class="lang-check">✓</span>{_lang_name(code)}'
        f'</button>'
        f'</form>'
        for code in SUPPORTED_LANGS
    )
    chevron = ('<svg class="lang-chevron" viewBox="0 0 24 24" fill="none" '
               'stroke="currentColor" stroke-width="2.4" stroke-linecap="round" '
               'stroke-linejoin="round"><polyline points="6 9 12 15 18 9"/></svg>')
    return (f'<details class="lang-dropdown">'
            f'<summary class="lang-current">'
            f'<span>{_lang_name(current_lang)}</span>{chevron}'
            f'</summary>'
            f'<div class="lang-menu">{options}</div>'
            f'</details>')

def _is_local_dev() -> bool:
    return BASE_URL.startswith("http://localhost") or BASE_URL.startswith("http://127.0.0.1")

@app.on_event("startup")
async def _run_schema_patches():
    try:
        run("ALTER TABLE creative_tech_applications ADD COLUMN status TEXT NOT NULL DEFAULT 'pending'")
    except Exception:
        pass  # column already exists

@app.on_event("startup")
async def _init_contact_messages_table():
    try:
        if db.backend_name == "postgresql":
            run("""
                CREATE TABLE IF NOT EXISTS contact_messages (
                    id SERIAL PRIMARY KEY,
                    name TEXT NOT NULL,
                    contact_info TEXT NOT NULL,
                    service TEXT NOT NULL,
                    message TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
        else:
            run("""
                CREATE TABLE IF NOT EXISTS contact_messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    contact_info TEXT NOT NULL,
                    service TEXT NOT NULL,
                    message TEXT NOT NULL,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            """)
    except Exception as exc:
        import logging
        logging.error("Failed to initialize contact_messages table: %s", exc)

@app.on_event("startup")
async def _guard_default_credentials():
    if not _is_vercel_runtime():
        return
    import logging
    default_hash = _hash("coach2024")
    row = one("SELECT id FROM coaches WHERE username='admin' AND password_hash=?", (default_hash,))
    if row:
        # Log at CRITICAL so the alert surfaces in Vercel function logs.
        # We do NOT crash here — crashing would prevent the coach from logging
        # in to rotate the credential. Change the password via /coach/account
        # then this warning will stop appearing.
        logging.critical(
            "SECURITY WARNING: Default credentials (admin/coach2024) are active in "
            "production. Log in to /coach/account and change the password immediately."
        )

def _using_blob_storage() -> bool:
    return bool(os.getenv("BLOB_READ_WRITE_TOKEN")) and AsyncBlobClient is not None

def _flutterwave_auth_headers() -> dict:
    if not FLW_SECRET:
        raise HTTPException(
            500,
            "Flutterwave secret key is missing. Set FLUTTERWAVE_SECRET_KEY or FLW_CLIENT_SECRET.",
        )
    return {"Authorization": f"Bearer {FLW_SECRET}"}


def _verify_flutterwave_webhook(raw_body: bytes, headers) -> bool:
    if not FLW_SECRET_HASH:
        return _is_local_dev()

    signature = headers.get("flutterwave-signature")
    if signature:
        expected = base64.b64encode(
            hmac.new(FLW_SECRET_HASH.encode(), raw_body, hashlib.sha256).digest()
        ).decode()
        return secrets.compare_digest(signature, expected)

    legacy_hash = headers.get("verif-hash")
    if legacy_hash:
        return secrets.compare_digest(legacy_hash, FLW_SECRET_HASH)

    return False

async def _verify_flutterwave_transaction(
    tx_ref: str,
    expected_amount: float,
    expected_currency: str,
    transaction_id: str = "",
):
    if not FLW_SECRET:
        return _is_local_dev(), None

    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.get(
            "https://api.flutterwave.com/v3/transactions/verify_by_reference",
            params={"tx_ref": tx_ref},
            headers=_flutterwave_auth_headers()
        )
        resp.raise_for_status()
        data = resp.json()
        payload = data.get("data", {}) if isinstance(data, dict) else {}

        def _payload_is_valid(candidate: dict) -> tuple[bool, str]:
            status_ok = candidate.get("status") == "successful"
            tx_ref_ok = candidate.get("tx_ref") == tx_ref
            currency_ok = candidate.get("currency") == expected_currency
            try:
                amount_ok = float(candidate.get("amount", 0)) >= float(expected_amount)
            except Exception:
                amount_ok = False

            if status_ok and tx_ref_ok and currency_ok and amount_ok:
                return True, ""
            return False, f"status={candidate.get('status')} tx_ref={candidate.get('tx_ref')} currency={candidate.get('currency')} amount={candidate.get('amount')}"

        valid, error = _payload_is_valid(payload)
        if valid:
            return True, payload

        if transaction_id and str(transaction_id).isdigit():
            resp = await client.get(
                f"https://api.flutterwave.com/v3/transactions/{transaction_id}/verify",
                headers=_flutterwave_auth_headers()
            )
            resp.raise_for_status()
            data = resp.json()
            payload = data.get("data", {}) if isinstance(data, dict) else {}
            valid, error = _payload_is_valid(payload)
            if valid:
                return True, payload

        return False, error or "Provider verification failed"

# ─── CSS + JS ─────────────────────────────────────────────────────────────────

CSS = """
:root {
  --background: #FAF8F4;
  --foreground: #161618;
  --card: #FFFFFF;
  --card-border: #E0E0E4;
  --muted: #8E8E92;
  --muted-bg: #F0EEEC;
  --red: #C1272D;
  --red2: #931E22;
  --red-dim: rgba(193, 39, 45, 0.08);
  --white: #FFFFFF;
  --nav-bg: rgba(250, 248, 244, 0.85);
}
html.dark {
  --background: #161618;
  --foreground: #FAF8F4;
  --card: #1E1E21;
  --card-border: #2C2C30;
  --muted: #8E8E92;
  --muted-bg: #252528;
  --red: #C1272D;
  --red2: #931E22;
  --red-dim: rgba(193, 39, 45, 0.12);
  --white: #FFFFFF;
  --nav-bg: rgba(22, 22, 24, 0.9);
}
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:'Plus Jakarta Sans',-apple-system,BlinkMacSystemFont,sans-serif;background:var(--background);color:var(--foreground);line-height:1.5;min-height:100vh;position:relative}
body::after{content:'';position:fixed;inset:0;background-image:url("data:image/svg+xml,%3Csvg viewBox='0 0 200 200' xmlns='http://www.w3.org/2000/svg'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.85' numOctaves='4' stitchTiles='stitch'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23n)' opacity='0.035'/%3E%3C/svg%3E");pointer-events:none;z-index:9999;opacity:.5}
a{color:inherit;text-decoration:none}
.nav{background:var(--nav-bg);backdrop-filter:blur(16px);color:var(--foreground);padding:0 28px;height:64px;display:flex;align-items:center;justify-content:space-between;position:sticky;top:0;z-index:100;border-bottom:1px solid var(--card-border)}
.nav-brand{font-family:'Space Grotesk',sans-serif;font-weight:700;font-size:1.05rem;letter-spacing:-.02em}
.nav-brand .r{color:var(--red)}
.nav-right{display:flex;align-items:center;gap:20px;font-size:.85rem;font-weight:500}
.nav-right a{color:var(--muted);text-decoration:none;transition:color .15s}
.nav-right a:hover{color:var(--foreground)}
.nav-user{color:var(--foreground)}
.nav-badge{background:var(--red);color:var(--white);padding:2px 8px;border-radius:4px;font-size:.65rem;font-weight:700;font-family:'JetBrains Mono',monospace;letter-spacing:0.05em}
.container{max-width:1080px;margin:0 auto;padding:28px 20px;position:relative;z-index:10}
.card{background:var(--card);border-radius:4px;padding:28px;border:1px solid var(--card-border);box-shadow:0 4px 20px rgba(0,0,0,.05);margin-bottom:20px}
html.dark .card{box-shadow:0 4px 20px rgba(0,0,0,.55)}
.card-sm{padding:18px 22px}
.card-title{font-family:'Space Grotesk',sans-serif;font-size:1.2rem;font-weight:700;margin-bottom:16px;color:var(--foreground);letter-spacing:-0.01em}
.btn{display:inline-flex;align-items:center;justify-content:center;gap:6px;padding:10px 22px;border-radius:4px;border:none;cursor:pointer;font-size:.85rem;font-weight:700;text-decoration:none;transition:all .2s ease-in-out;font-family:inherit;white-space:nowrap;letter-spacing:1px;text-transform:uppercase}
.btn:hover{opacity:.9;transform:translateY(-1px)}.btn:active{transform:scale(.98)}
.btn-red{background:var(--red);color:var(--white);border:1px solid transparent}.btn-red:hover{background:var(--red2);box-shadow:0 0 12px rgba(193,39,45,0.35)}
.btn-dark{background:var(--muted-bg);color:var(--foreground);border:1px solid var(--card-border)}.btn-dark:hover{background:var(--background);border-color:var(--foreground)}
.btn-green{background:#22c55e;color:var(--white);border:1px solid transparent}.btn-green:hover{background:#16a34a;box-shadow:0 0 12px rgba(34,197,94,0.35)}
.btn-orange{background:#f97316;color:var(--white);border:1px solid transparent}.btn-orange:hover{background:#ea580c;box-shadow:0 0 12px rgba(249,115,22,0.35)}
.btn-gold{background:#f59e0b;color:var(--white);border:1px solid transparent}.btn-gold:hover{background:#d97706;box-shadow:0 0 12px rgba(245,158,11,0.35)}
.btn-ghost{background:transparent;border:1px solid var(--card-border);color:var(--foreground)}.btn-ghost:hover{border-color:var(--foreground);background:var(--muted-bg)}
.btn-full{width:100%}
.btn-lg{padding:14px 32px;font-size:.9rem;letter-spacing:1.5px}
.form-group{margin-bottom:18px}
.form-label{display:block;font-size:.75rem;font-weight:700;margin-bottom:6px;color:var(--muted);text-transform:uppercase;letter-spacing:1px;font-family:'Space Grotesk',sans-serif}
input[type=text],input[type=email],input[type=password],input[type=number],input[type=date],textarea,select{width:100%;padding:10px 14px;border:1.5px solid var(--card-border);border-radius:4px;font-size:.9rem;font-family:inherit;background:var(--background);color:var(--foreground);transition:border-color .15s,box-shadow .15s}
input:focus,textarea:focus,select:focus{outline:none;border-color:var(--red);box-shadow:0 0 0 2px var(--red-dim)}
textarea{resize:vertical;min-height:110px}
.badge{display:inline-flex;align-items:center;padding:3px 8px;border-radius:4px;font-size:.65rem;font-weight:700;text-transform:uppercase;letter-spacing:.06em;font-family:'JetBrains Mono',monospace;border:1px solid transparent}
.badge-pending{background:rgba(245,158,11,0.08);border-color:rgba(245,158,11,0.25);color:#f59e0b}
.badge-approved{background:rgba(34,197,94,0.08);border-color:rgba(34,197,94,0.25);color:#22c55e}
.badge-revision{background:rgba(239,68,68,0.08);border-color:rgba(239,68,68,0.25);color:#ef4444}
.badge-locked{background:rgba(255,255,255,0.05);border-color:rgba(255,255,255,0.1);color:#8e8e92}
.badge-new{background:rgba(59,130,246,0.08);border-color:rgba(59,130,246,0.25);color:#3b82f6}
.badge-draft{background:rgba(255,255,255,0.05);border-color:rgba(255,255,255,0.1);color:#8e8e92}
.badge-published{background:rgba(34,197,94,0.08);border-color:rgba(34,197,94,0.25);color:#22c55e}
.badge-paid{background:rgba(250,204,21,0.08);border-color:rgba(250,204,21,0.25);color:#eab308}
.grid-2{display:grid;grid-template-columns:2fr 1fr;gap:20px}
.grid-3{display:grid;grid-template-columns:repeat(3,1fr);gap:16px}
.grid-stats{display:grid;grid-template-columns:repeat(4,1fr);gap:16px;margin-bottom:24px}
.grid-days{display:grid;grid-template-columns:repeat(auto-fill,minmax(155px,1fr));gap:12px}
.stat{background:var(--card);border-radius:4px;padding:18px 20px;border:1px solid var(--card-border);box-shadow:0 4px 16px rgba(0,0,0,.05);transition:transform .2s ease-in-out}.stat:hover{transform:translateY(-2px)}
.stat-num{font-family:'Space Grotesk',sans-serif;font-size:2rem;font-weight:700;color:var(--red);line-height:1.1}
.stat-label{font-size:.65rem;color:var(--muted);font-weight:700;text-transform:uppercase;letter-spacing:.08em;margin-top:6px;font-family:'JetBrains Mono',monospace}
.day-card{border-radius:4px;padding:14px 16px;border:2px solid transparent;transition:all .2s ease-in-out;display:block;text-decoration:none;color:inherit}
.day-card.locked{background:var(--muted-bg);border-color:var(--card-border);opacity:.45;pointer-events:none}
.day-card.paywalled{background:rgba(251,191,36,0.02);border-color:rgba(251,191,36,0.15);cursor:pointer}
.day-card.paywalled:hover{transform:translateY(-2px);border-color:#fbbf24;box-shadow:0 4px 16px rgba(251,191,36,.15)}
.day-card.available{background:var(--card);border-color:var(--card-border)}
.day-card.available:hover{transform:translateY(-2px);border-color:var(--red);box-shadow:0 4px 16px var(--red-dim)}
.day-card.pending{background:rgba(59,130,246,0.02);border-color:rgba(59,130,246,0.2)}
.day-card.pending:hover{transform:translateY(-2px);border-color:#3b82f6;box-shadow:0 4px 16px rgba(59,130,246,.15)}
.day-card.graded-pass{background:rgba(34,197,94,0.02);border-color:rgba(34,197,94,0.2)}
.day-card.graded-pass:hover{transform:translateY(-2px);border-color:#22c55e;box-shadow:0 4px 16px rgba(34,197,94,.15)}
.day-card.graded-revision{background:rgba(249,115,22,0.02);border-color:rgba(249,115,22,0.2)}
.day-card.graded-revision:hover{transform:translateY(-2px);border-color:#f97316;box-shadow:0 4px 16px rgba(249,115,22,.15)}
.day-num{font-size:.65rem;font-weight:700;text-transform:uppercase;letter-spacing:.08em;color:var(--muted);margin-bottom:6px;font-family:'JetBrains Mono',monospace}
.day-title{font-size:.82rem;font-weight:600;line-height:1.35}
.day-status{font-size:.7rem;color:var(--muted);margin-top:6px}
.progress-wrap{background:var(--muted-bg);border-radius:4px;height:8px;overflow:hidden}
.progress-fill{height:100%;background:linear-gradient(90deg,var(--red),#ff6b7a);border-radius:4px}
.alert{padding:12px 16px;border-radius:4px;margin-bottom:18px;font-size:.875rem}
.alert-error{background:#3a1818;color:#ff8b8b;border-left:4px solid #ef4444}
.alert-success{background:#0f2c1b;color:#a3f4c1;border-left:4px solid #22c55e}
.alert-info{background:#10223e;color:#a3c7f4;border-left:4px solid #3b82f6}
.alert-warn{background:#2c230e;color:#fcd34d;border-left:4px solid #fbbf24}
.divider{border:none;border-top:1px solid var(--card-border);margin:20px 0}
.text-muted{color:var(--muted)}.text-sm{font-size:.875rem}.text-xs{font-size:.78rem}
.mt-2{margin-top:8px}.mt-3{margin-top:12px}.mt-4{margin-top:16px}.mb-2{margin-bottom:8px}
.flex{display:flex}.flex-1{flex:1}.gap-2{gap:8px}.gap-3{gap:12px}
.items-center{align-items:center}.justify-between{justify-content:space-between}
.img-thumb{width:90px;height:72px;object-fit:cover;border-radius:4px;border:1px solid var(--card-border);cursor:pointer}
.sub-card{border-left:4px solid var(--card-border);padding:16px;border-radius:0 4px 4px 0;background:var(--background);margin-bottom:12px}
.sub-card.approved{border-color:#22c55e;background:#0f2c1b}
.sub-card.needs_revision{border-color:#f97316;background:#23140a}
.sub-card.pending{border-color:#3b82f6;background:#10223e}
.student-row{display:flex;align-items:center;justify-content:space-between;padding:14px 0;border-bottom:1px solid var(--card-border)}
.student-row:last-child{border-bottom:none}
.table{width:100%;border-collapse:collapse;font-size:.875rem}
.table th{text-align:left;padding:10px 12px;background:var(--muted-bg);border-bottom:2px solid var(--card-border);font-size:.7rem;font-weight:700;text-transform:uppercase;letter-spacing:.08em;color:var(--muted);font-family:'JetBrains Mono',monospace}
.table td{padding:10px 12px;border-bottom:1px solid var(--card-border)}
.table tr:last-child td{border-bottom:none}
.login-wrap{min-height:100vh;display:flex;align-items:center;justify-content:center;padding:20px;background:var(--background);position:relative}
.login-wrap::after{content:'';position:fixed;inset:0;background-image:url("data:image/svg+xml,%3Csvg viewBox='0 0 200 200' xmlns='http://www.w3.org/2000/svg'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.85' numOctaves='4' stitchTiles='stitch'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23n)' opacity='0.035'/%3E%3C/svg%3E");pointer-events:none;z-index:9999;opacity:.5}
.login-box{background:var(--card);border:1px solid var(--card-border);border-radius:4px;padding:40px;width:100%;max-width:420px;box-shadow:0 20px 60px rgba(0,0,0,.05);position:relative;z-index:100}
.login-logo{text-align:center;margin-bottom:28px}
.login-logo .logo-text{font-family:'Space Grotesk',sans-serif;font-size:1.5rem;font-weight:800;color:var(--foreground);letter-spacing:-0.02em}
.logo-text .r{color:var(--red)}
.tagline{color:var(--muted);font-size:.78rem;margin-top:6px;font-family:'JetBrains Mono',monospace;text-transform:uppercase;letter-spacing:1px}
.tab-nav{display:flex;border-bottom:2px solid var(--card-border);margin-bottom:24px}
.tab-btn{flex:1;padding:10px;text-align:center;font-size:.85rem;font-weight:700;color:var(--muted);cursor:pointer;border-bottom:2px solid transparent;margin-bottom:-2px;background:none;border-top:none;border-left:none;border-right:none;font-family:'Space Grotesk',sans-serif;transition:color .15s;text-transform:uppercase;letter-spacing:1px}
.tab-btn.active{color:var(--red);border-bottom-color:var(--red)}
.tab-panel{display:none}.tab-panel.active{display:block}
.lesson-content h3{font-family:'Space Grotesk',sans-serif;font-size:1rem;font-weight:700;margin:20px 0 8px;color:var(--foreground)}
.lesson-content h3:first-child{margin-top:0}
.lesson-content p{margin-bottom:12px;line-height:1.75;color:var(--foreground);opacity:0.9}
.lesson-content ul,.lesson-content ol{padding-left:20px;margin-bottom:12px}
.lesson-content li{margin-bottom:6px;line-height:1.65;color:var(--foreground);opacity:0.9}
.lesson-content dl{margin-bottom:12px}
.lesson-content dt{font-weight:700;color:var(--foreground);margin-top:10px}
.lesson-content dd{color:var(--foreground);opacity:0.9;padding-left:16px;margin-top:2px}
.lesson-content blockquote{border-left:4px solid var(--red);padding:10px 16px;background:var(--muted-bg);border-radius:0 4px 4px 0;margin:16px 0;font-style:italic}
.video-wrap{position:relative;padding-bottom:56.25%;height:0;overflow:hidden;border-radius:4px;margin-bottom:20px}
.video-wrap iframe{position:absolute;top:0;left:0;width:100%;height:100%;border:none;border-radius:4px}
.paywall-box{text-align:center;padding:48px 32px}
.paywall-price{font-family:'Space Grotesk',sans-serif;font-size:3rem;font-weight:700;color:var(--red);line-height:1}
.paywall-currency{font-size:1.5rem;font-weight:600;vertical-align:top;margin-top:6px;display:inline-block}
.feature-list{list-style:none;text-align:left;max-width:320px;margin:20px auto}
.feature-list li{padding:8px 0;display:flex;align-items:center;gap:10px;font-size:.95rem}
.feature-list li::before{content:"✓";color:#22c55e;font-weight:900;font-size:1.1rem}
.pricing-hero{background:linear-gradient(135deg,var(--background),var(--muted-bg));color:var(--foreground);padding:80px 40px;text-align:center;border-bottom:1px solid var(--card-border)}
.pricing-hero h1{font-family:'Space Grotesk',sans-serif;font-size:2.5rem;font-weight:700;margin-bottom:16px}
.pricing-hero p{font-size:1.1rem;color:var(--muted);max-width:560px;margin:0 auto 32px}
.pricing-card{max-width:420px;margin:-40px auto 0;background:var(--card);border:1px solid var(--card-border);border-radius:4px;padding:36px;box-shadow:0 20px 60px rgba(0,0,0,.05);position:relative;z-index:10;color:var(--foreground)}
.cohort-row{display:flex;align-items:center;justify-content:space-between;padding:14px 0;border-bottom:1px solid var(--card-border)}
.cohort-row:last-child{border-bottom:none}
@media(max-width:768px){.grid-2{grid-template-columns:1fr}.grid-stats{grid-template-columns:1fr 1fr}.grid-days{grid-template-columns:repeat(auto-fill,minmax(130px,1fr))}.pricing-hero{padding:48px 24px}.pricing-hero h1{font-size:1.75rem}}
.nav-profile{position:relative}
.nav-profile-btn{background:var(--muted-bg);border:none;color:var(--foreground);width:34px;height:34px;border-radius:50%;display:flex;align-items:center;justify-content:center;cursor:pointer;transition:background .15s;flex-shrink:0}
.nav-profile-btn:hover{background:var(--card-border)}
.nav-dropdown{position:absolute;right:0;top:calc(100% + 8px);background:var(--card);border:1px solid var(--card-border);border-radius:4px;box-shadow:0 8px 32px rgba(0,0,0,.15);min-width:188px;z-index:200;display:none;overflow:hidden}
.nav-dropdown.open{display:block}
.nav-dropdown-header{padding:11px 16px;border-bottom:1px solid var(--card-border);font-size:.65rem;font-weight:700;color:var(--muted);text-transform:uppercase;letter-spacing:.08em;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;font-family:'JetBrains Mono',monospace}
.nav-dropdown a{display:block;padding:10px 16px;color:var(--foreground) !important;text-decoration:none;font-size:.875rem;transition:background .1s}
.nav-dropdown a:hover{background:var(--muted-bg);color:var(--foreground) !important}
.nav-dropdown hr{border:none;border-top:1px solid var(--card-border);margin:4px 0}
"""

LANDING_CSS = """
@import url('https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@300;400;500;600;700&family=Space+Grotesk:wght@400;500;600;700&family=JetBrains+Mono:wght@400;700&display=swap');
:root {
  --l-bg: var(--background);
  --l-bg-alt: #F0EEEC;
  --l-card: var(--card);
  --l-border: var(--card-border);
  --l-muted: var(--muted);
  --l-muted-bg: var(--muted-bg);
  --l-red: var(--red);
  --l-red2: var(--red2);
  --l-red-dim: rgba(193, 39, 45, 0.08);
  --l-red-glow: rgba(193, 39, 45, 0.2);
  --light: #636367;
  --white: #161618;
  --l-nav-scrolled: rgba(250, 248, 244, 0.75);
  --l-border-scrolled: rgba(224, 224, 228, 0.6);
  --l-header-bg: rgba(250, 248, 244, 0.8);
  --l-hero-grid-line: rgba(22, 22, 24, 0.035);
}
html.dark {
  --l-bg: var(--background);
  --l-bg-alt: #0E0E0E;
  --l-card: var(--card);
  --l-border: var(--card-border);
  --l-muted: var(--muted);
  --l-muted-bg: var(--muted-bg);
  --l-red: var(--red);
  --l-red2: var(--red2);
  --l-red-dim: rgba(193, 39, 45, 0.12);
  --l-red-glow: rgba(193, 39, 45, 0.25);
  --light: #FAF8F4;
  --white: #FAF8F4;
  --l-nav-scrolled: rgba(22, 22, 24, 0.75);
  --l-border-scrolled: rgba(44, 44, 48, 0.6);
  --l-header-bg: rgba(22, 22, 24, 0.8);
  --l-hero-grid-line: rgba(255, 255, 255, 0.025);
}
.landing-page{background:var(--l-bg);color:var(--white);font-family:'Plus Jakarta Sans',sans-serif;font-size:15px;line-height:1.65;overflow-x:hidden;min-height:100vh}
.landing-page *{box-sizing:border-box}
.landing-page::after{content:'';position:fixed;inset:0;background-image:url("data:image/svg+xml,%3Csvg viewBox='0 0 200 200' xmlns='http://www.w3.org/2000/svg'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.85' numOctaves='4' stitchTiles='stitch'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23n)' opacity='0.035'/%3E%3C/svg%3E");pointer-events:none;z-index:9999;opacity:.5}
/* ── NAV ── */
.l-header{position:fixed;top:0;left:0;right:0;z-index:500;width:100%;transition:all .4s cubic-bezier(0.16,1,0.3,1);background:var(--l-header-bg);backdrop-filter:blur(16px);border-bottom:1px solid var(--l-border-scrolled);padding:0}
.l-header.scrolled{background:transparent;backdrop-filter:none;border-bottom:1px solid transparent;padding-top:12px}
.l-nav{display:flex;align-items:center;justify-content:space-between;width:100%;max-width:1200px;margin:0 auto;padding:0 48px;height:64px;transition:all .4s cubic-bezier(0.16,1,0.3,1);border-radius:0;border:1px solid transparent;background:transparent}
.l-header.scrolled .l-nav{width:calc(100% - 32px);max-width:900px;height:54px;padding:0 24px;background:var(--l-nav-scrolled);backdrop-filter:blur(20px);border:1px solid var(--l-border-scrolled);border-radius:100px;box-shadow:0 16px 40px rgba(0,0,0,.15)}
.l-nav-brand{display:flex;align-items:center;gap:10px;text-decoration:none;color:var(--white)}
.l-brand-text{display:flex;flex-direction:column;justify-content:center;line-height:1}
.l-brand-title{font-family:'Space Grotesk',sans-serif;font-weight:800;font-size:18px;letter-spacing:-0.02em;color:var(--white)}
.l-brand-sub{font-family:'Plus Jakarta Sans',sans-serif;font-size:9px;letter-spacing:0.15em;font-weight:700;color:var(--muted);margin-top:1px;text-transform:uppercase}
.l-nav-right{display:flex;align-items:center;gap:12px;height:100%}
.l-nav-link{color:var(--muted);font-size:12px;font-weight:600;letter-spacing:1.2px;text-transform:uppercase;text-decoration:none;padding:0 14px;height:100%;display:flex;align-items:center;transition:color .2s}
.l-nav-link:hover{color:var(--white)}
.l-nav-cta{background:var(--l-red);color:#FAF8F4!important;padding:0 20px!important;height:36px;display:inline-flex;align-items:center;font-weight:700!important;border-bottom:none!important;border-radius:100px;transition:all .3s cubic-bezier(0.16,1,0.3,1);box-shadow:0 4px 12px var(--l-red-dim)}
.l-nav-cta:hover{background:var(--l-red2)!important;transform:translateY(-1px);box-shadow:0 6px 16px var(--l-red-dim)}

.theme-toggle{background:transparent;border:1px solid var(--l-border);color:var(--white);width:34px;height:34px;border-radius:50%;cursor:pointer;display:inline-flex;align-items:center;justify-content:center;transition:all .2s;padding:0;outline:none;flex-shrink:0}
.theme-toggle:hover{background:var(--l-muted-bg);border-color:var(--white)}
html.dark .theme-toggle .theme-icon-moon{display:none}
html:not(.dark) .theme-toggle .theme-icon-sun{display:none}
/* mobile nav controls */
.l-nav-mobile{display:none;align-items:center;gap:8px;position:relative}
.l-hamburger{background:transparent;border:1px solid var(--l-border);color:var(--white);width:34px;height:34px;border-radius:50%;cursor:pointer;display:inline-flex;align-items:center;justify-content:center;transition:all .2s;padding:0;outline:none;flex-shrink:0}
.l-hamburger:hover{background:var(--l-muted-bg);border-color:var(--white)}
/* mobile dropdown */
.l-mobile-menu{display:none;position:absolute;top:calc(100% + 12px);right:0;min-width:220px;background:var(--l-card);border:1px solid var(--l-border);border-radius:16px;padding:6px;box-shadow:0 16px 48px rgba(0,0,0,.18);z-index:600;flex-direction:column;gap:2px}
.l-mobile-menu.open{display:flex}
.l-mobile-link{color:var(--white);font-size:15px;font-weight:500;text-decoration:none;padding:12px 16px;border-radius:10px;transition:background .15s;display:block}
.l-mobile-link:hover{background:var(--l-muted-bg)}
.l-mobile-sep{height:1px;background:var(--l-border);margin:4px 0}
.l-mobile-lang-label{font-size:11px;font-weight:600;letter-spacing:.8px;text-transform:uppercase;color:var(--l-muted);padding:8px 16px 4px}
.l-mobile-lang-opts .lang-option{border-radius:8px;font-size:14px;width:100%}
.l-mobile-cta-wrap{padding:4px 2px 2px}
.l-mobile-cta{color:#FAF8F4!important;background:var(--l-red);border-radius:10px;padding:12px 16px!important;text-align:center;font-weight:700;text-decoration:none;display:block;transition:background .2s;font-size:14px;letter-spacing:.3px}
.l-mobile-cta:hover{background:var(--l-red2)!important}
/* lang dropdown */
.lang-dropdown{position:relative}
.lang-dropdown summary{list-style:none;display:flex;align-items:center;gap:7px;padding:7px 12px;border:1px solid var(--l-border);border-radius:4px;background:transparent;color:var(--light);font-family:'Plus Jakarta Sans',sans-serif;font-size:12px;font-weight:600;letter-spacing:.3px;cursor:pointer;transition:border-color .15s,color .15s;white-space:nowrap}
.lang-dropdown summary::-webkit-details-marker{display:none}
.lang-dropdown summary:hover{border-color:var(--white);color:var(--white)}
.lang-chevron{width:13px;height:13px;color:var(--muted);transition:transform .2s}
.lang-dropdown[open] summary{border-color:var(--white);color:var(--white)}
.lang-dropdown[open] .lang-chevron{transform:rotate(180deg);color:var(--white)}
.lang-menu{position:absolute;top:calc(100% + 8px);right:0;min-width:170px;background:var(--l-card);border:1px solid var(--l-border);border-radius:4px;padding:6px;box-shadow:0 14px 36px rgba(0,0,0,.15);z-index:600;display:flex;flex-direction:column;gap:2px}
.lang-menu form{margin:0}
.lang-option{display:flex;align-items:center;gap:8px;width:100%;text-align:left;background:transparent;border:none;color:var(--light);font-family:'Plus Jakarta Sans',sans-serif;font-size:14px;font-weight:500;padding:9px 12px;border-radius:4px;cursor:pointer;transition:background .12s,color .12s}
.lang-option:hover{background:var(--l-muted-bg);color:var(--white)}
.lang-check{width:14px;flex-shrink:0;font-size:11px;color:var(--l-red);opacity:0}
.lang-option.lang-active{color:var(--white)}
.lang-option.lang-active .lang-check{opacity:1}
/* ── HERO ── */
.l-hero{position:relative;min-height:calc(100vh - 64px);display:flex;align-items:center;justify-content:center;overflow:hidden;padding:80px 48px;margin-top:64px}
.l-hero-bg{position:absolute;inset:0;background:radial-gradient(ellipse 80% 60% at 50% 100%,var(--l-red-dim) 0%,transparent 70%),linear-gradient(180deg,var(--l-bg) 0%,var(--l-bg-alt) 100%)}
.l-hero-lines{position:absolute;inset:0;background-image:linear-gradient(var(--l-hero-grid-line) 1px,transparent 1px),linear-gradient(90deg,var(--l-hero-grid-line) 1px,transparent 1px);background-size:80px 80px}
.l-hero-content{position:relative;z-index:2;max-width:980px;text-align:center}
.l-eyebrow{display:inline-flex;align-items:center;gap:10px;margin-bottom:32px;animation:fadeUpL .7s ease both}
.l-eyebrow-dot{width:6px;height:6px;background:var(--l-red);border-radius:50%;animation:pulseL 2s ease infinite}
@keyframes pulseL{0%,100%{opacity:1;transform:scale(1)}50%{opacity:.5;transform:scale(1.4)}}
.l-eyebrow-text{font-size:11px;font-weight:700;letter-spacing:3px;text-transform:uppercase;color:var(--muted);font-family:'JetBrains Mono',monospace}
.l-hero h1{font-family:'Space Grotesk',sans-serif;font-size:clamp(64px,11vw,120px);line-height:.88;letter-spacing:-0.03em;margin-bottom:24px;animation:fadeUpL .7s .08s ease both}
.l-hero h1 .l-accent{color:var(--l-red)}
.l-hero-sub{font-size:18px;color:var(--light);max-width:580px;margin:0 auto 48px;animation:fadeUpL .7s .2s ease both;font-weight:300}
@keyframes fadeUpL{from{opacity:0;transform:translateY(20px)}to{opacity:1;transform:translateY(0)}}
/* ── CONTAINER ── */
.l-container{max-width:1080px;margin:0 auto;padding:0 48px}
.l-sec-label{font-size:11px;font-weight:700;letter-spacing:3px;text-transform:uppercase;color:var(--l-red);margin-bottom:12px;font-family:'JetBrains Mono',monospace}
.l-sec-title{font-family:'Space Grotesk',sans-serif;font-size:clamp(36px,5vw,60px);line-height:.95;letter-spacing:-0.02em;margin-bottom:20px}
.l-sec-desc{font-size:16px;color:var(--light);max-width:520px;font-weight:400}
/* ── COURSE CARDS ── */
.l-courses{padding:100px 0;border-top:1px solid var(--l-border)}
.l-course-grid{display:grid;grid-template-columns:1fr 1fr;gap:1px;background:var(--l-border);border-radius:4px;overflow:hidden;margin-top:56px;border:1px solid var(--l-border)}
.l-course-card{background:var(--l-card);padding:48px 40px;position:relative;overflow:hidden;transition:background .2s}
.l-course-card:hover{background:var(--l-muted-bg)}
.l-course-card::before{content:'';position:absolute;top:0;left:0;right:0;height:3px;background:var(--l-red);transform:scaleX(0);transition:transform .3s;transform-origin:left}
.l-course-card:hover::before{transform:scaleX(1)}
.l-course-badge{display:inline-flex;align-items:center;gap:6px;background:var(--l-red-dim);border:1px solid var(--l-red-glow);color:var(--l-red);font-size:10px;font-weight:700;letter-spacing:2px;text-transform:uppercase;padding:5px 12px;border-radius:4px;margin-bottom:20px;font-family:'JetBrains Mono',monospace}
.l-course-title{font-family:'Space Grotesk',sans-serif;font-size:36px;letter-spacing:-0.02em;line-height:1;margin-bottom:16px}
.l-course-desc{font-size:14px;color:var(--light);line-height:1.7;margin-bottom:28px;max-width:400px}
.l-course-pills{display:flex;flex-wrap:wrap;gap:8px;margin-bottom:28px}
.l-pill{display:flex;align-items:center;gap:6px;padding:6px 12px;border:1px solid var(--l-border);border-radius:100px;font-size:12px;font-weight:600;color:var(--light);background:var(--l-bg)}
.l-pill-dot{width:5px;height:5px;background:var(--l-red);border-radius:50%;flex-shrink:0}
.l-price-row{display:flex;align-items:flex-end;gap:8px;margin-bottom:8px}
.l-price{font-family:'Space Grotesk',sans-serif;font-size:52px;line-height:1;color:var(--white)}
.l-price-sub{font-size:12px;color:var(--muted);padding-bottom:8px;font-family:'JetBrains Mono',monospace}
.l-cta{display:inline-flex;align-items:center;gap:8px;padding:14px 28px;background:var(--l-red);color:#FAF8F4;font-family:'Plus Jakarta Sans',sans-serif;font-size:12px;font-weight:700;letter-spacing:1.5px;text-transform:uppercase;border:none;border-radius:4px;cursor:pointer;text-decoration:none;transition:all .2s;margin-top:12px}
.l-cta:hover{background:var(--l-red2);transform:translateY(-2px)}
.l-cta-ghost{background:transparent;border:1px solid var(--l-border);color:var(--white)}
.l-cta-ghost:hover{border-color:var(--white);background:var(--l-muted-bg)}
/* ── SECTION ── */
.l-section{padding:100px 0;border-top:1px solid var(--l-border);background:var(--l-bg)}
.l-section.dark{background:var(--l-bg-alt)}
/* ── WHY CARDS ── */
.l-why-grid{display:grid;grid-template-columns:repeat(4,1fr);gap:1px;background:var(--l-border);border-radius:4px;overflow:hidden;margin-top:56px;border:1px solid var(--l-border)}
.l-why-card{background:var(--l-card);padding:36px 28px;transition:background .2s}
.l-why-card:hover{background:var(--l-muted-bg)}
.l-why-card h4{font-family:'Space Grotesk',sans-serif;font-size:22px;letter-spacing:-0.01em;margin-bottom:12px;color:var(--white)}
.l-why-card p{font-size:13px;color:var(--light);line-height:1.7}
/* ── CURRICULUM ── */
.l-weeks{display:flex;flex-direction:column;gap:12px;margin-top:56px}
.l-week{background:var(--l-card);border:1px solid var(--l-border);border-radius:4px;overflow:hidden}
.l-week summary{display:flex;align-items:center;gap:18px;padding:20px 24px;cursor:pointer;user-select:none;list-style:none;transition:background .2s}
.l-week summary::-webkit-details-marker{display:none}
.l-week summary:hover{background:var(--l-muted-bg)}
.l-week-label{font-size:10px;font-weight:700;letter-spacing:2px;text-transform:uppercase;color:var(--l-red);min-width:60px;font-family:'JetBrains Mono',monospace}
.l-week-name{flex:1;font-weight:600;font-size:14px;color:var(--white)}
.l-week-arr{width:20px;height:20px;border:1px solid var(--l-border);border-radius:50%;display:flex;align-items:center;justify-content:center;font-size:9px;color:var(--muted);transition:transform .3s;flex-shrink:0}
details[open] .l-week-arr{transform:rotate(180deg);border-color:var(--l-red);color:var(--l-red)}
.l-week-days{padding:0 24px 20px;display:flex;flex-direction:column;gap:6px}
.l-day-row{display:flex;align-items:center;gap:16px;padding:12px 16px;background:var(--l-bg-alt);border-radius:4px}
.l-day-num{font-family:'JetBrains Mono',monospace;font-size:13px;color:var(--l-red);min-width:48px}
.l-day-title{flex:1;font-size:13px;font-weight:500;color:var(--white)}
.l-day-badge{font-size:10px;font-weight:700;letter-spacing:1.5px;text-transform:uppercase;padding:3px 8px;border-radius:4px;flex-shrink:0;font-family:'JetBrains Mono',monospace}
.l-day-free{background:var(--l-red-dim);color:var(--l-red);border:1px solid var(--l-red-glow)}
.l-day-locked{background:var(--l-border);color:var(--muted)}
/* ── PAYWALL CALLOUT ── */
.l-paywall{padding:80px 48px;background:linear-gradient(135deg,var(--l-bg-alt),var(--l-bg));border-top:1px solid var(--l-border);border-bottom:1px solid var(--l-border);text-align:center}
.l-paywall h2{font-family:'Space Grotesk',sans-serif;font-size:clamp(36px,6vw,72px);letter-spacing:-0.02em;margin-bottom:12px;color:var(--white)}
.l-paywall p{font-size:15px;color:var(--light);max-width:540px;margin:0 auto 32px}
/* ── TRANSFORM GRID ── */
.l-transform-grid{display:grid;grid-template-columns:1fr 1fr;gap:1px;background:var(--l-border);border-radius:4px;overflow:hidden;margin-top:56px;border:1px solid var(--l-border)}
.l-transform-col{background:var(--l-bg-alt);padding:48px 40px}
.l-transform-col.after-col{background:var(--l-card)}
.l-transform-header{display:flex;align-items:center;gap:12px;margin-bottom:32px}
.l-transform-tag{font-size:10px;font-weight:700;letter-spacing:2px;text-transform:uppercase;padding:5px 12px;border-radius:4px;font-family:'JetBrains Mono',monospace}
.l-tag-before{background:var(--l-border);color:var(--muted)}
.l-tag-after{background:var(--l-red);color:var(--white)}
.l-transform-list{display:flex;flex-direction:column;gap:14px;list-style:none}
.l-transform-list li{display:flex;align-items:flex-start;gap:12px;font-size:15px}
.l-icon{width:22px;height:22px;border-radius:50%;display:flex;align-items:center;justify-content:center;font-size:10px;flex-shrink:0;margin-top:2px}
.l-icon-x{background:var(--l-border);color:var(--muted)}
.l-icon-check{background:var(--l-red-dim);color:var(--l-red);border:1px solid var(--l-red-glow)}
.l-muted-item{color:var(--muted)}
.l-bright-item{color:var(--white);font-weight:500}
/* ── FRAMEWORK ── */
.l-stages{display:grid;grid-template-columns:repeat(7,1fr);gap:1px;background:var(--l-border);border-radius:4px;overflow:hidden;margin-top:56px;margin-bottom:16px;border:1px solid var(--l-border)}
.l-stage{background:var(--l-card);padding:24px 12px;text-align:center;cursor:pointer;transition:background .2s;position:relative;overflow:hidden}
.l-stage:hover,.l-stage.l-stage-active{background:var(--l-muted-bg)}
.l-stage.l-stage-active::after{content:'';position:absolute;bottom:0;left:0;right:0;height:3px;background:var(--l-red)}
.l-stage-num{font-family:'Space Grotesk',sans-serif;font-size:28px;color:var(--l-red);line-height:1;margin-bottom:6px;opacity:.5}
.l-stage.l-stage-active .l-stage-num{opacity:1}
.l-stage-icon{font-size:20px;margin-bottom:8px;display:block}
.l-stage-name{font-size:10px;font-weight:700;letter-spacing:1px;text-transform:uppercase;color:var(--muted);line-height:1.3}
.l-stage.l-stage-active .l-stage-name{color:var(--white)}
.l-stage-detail{background:var(--l-bg-alt);border:1px solid var(--l-border);border-radius:4px;padding:28px;display:none}
.l-stage-detail.l-stage-active{display:block}
.l-stage-detail h4{font-family:'Space Grotesk',sans-serif;font-size:26px;letter-spacing:-0.01em;margin-bottom:8px;color:var(--white)}
.l-stage-detail p{font-size:14px;color:var(--light);line-height:1.7;max-width:680px}
/* ── OUTCOMES ── */
.l-outcomes-grid{display:grid;grid-template-columns:repeat(2,1fr);gap:1px;background:var(--l-border);border-radius:4px;overflow:hidden;margin-top:56px;border:1px solid var(--l-border)}
.l-outcome{background:var(--l-card);padding:28px;display:flex;gap:16px;transition:background .2s}
.l-outcome:hover{background:var(--l-muted-bg)}
.l-outcome-n{font-family:'Space Grotesk',sans-serif;font-size:13px;color:var(--l-red);min-width:28px;padding-top:2px}
.l-outcome strong{display:block;font-size:14px;font-weight:700;margin-bottom:6px;color:var(--white)}
.l-outcome span{font-size:12px;color:var(--light);line-height:1.6}
/* ── TOOLS ── */
.l-tools-grid{display:grid;grid-template-columns:repeat(4,1fr);gap:1px;background:var(--l-border);border-radius:4px;overflow:hidden;margin-top:56px;border:1px solid var(--l-border)}
.l-tool-cat{background:var(--l-card);padding:28px 24px}
.l-tool-cat-label{font-size:10px;font-weight:700;letter-spacing:2.5px;text-transform:uppercase;color:var(--l-red);margin-bottom:16px;padding-bottom:10px;border-bottom:1px solid var(--l-border);font-family:'JetBrains Mono',monospace}
.l-tool-items{display:flex;flex-direction:column;gap:10px}
.l-tool-item{display:flex;align-items:center;gap:10px;font-size:13px;font-weight:500;color:var(--white)}
.l-tool-icon{width:26px;height:26px;background:var(--l-muted-bg);border-radius:4px;display:flex;align-items:center;justify-content:center;font-size:13px;flex-shrink:0}
/* ── ABOUT / CREDS ── */
.l-about-grid{display:grid;grid-template-columns:1fr 1fr;gap:1px;background:var(--l-border);border-radius:4px;overflow:hidden;margin-top:56px;border:1px solid var(--l-border)}
.l-about-col{background:var(--l-card);padding:40px}
.l-about-name{font-family:'Space Grotesk',sans-serif;font-size:38px;letter-spacing:-0.02em;line-height:1;margin-bottom:6px;color:var(--white)}
.l-about-role{font-size:11px;font-weight:700;letter-spacing:2px;text-transform:uppercase;color:var(--l-red);margin-bottom:16px;font-family:'JetBrains Mono',monospace}
.l-about-bio{font-size:14px;color:var(--light);line-height:1.75;margin-bottom:14px}
.l-about-tags{display:flex;flex-wrap:wrap;gap:8px;margin-top:16px}
.l-about-tag{font-size:11px;font-weight:600;padding:4px 12px;border:1px solid var(--l-border);border-radius:100px;color:var(--muted)}
.l-creds{display:flex;flex-direction:column;gap:10px}
.l-cred{display:flex;gap:14px;padding:14px;background:var(--l-bg-alt);border:1px solid var(--l-border);border-left:3px solid var(--l-red);border-radius:0 4px 4px 0}
.l-cred-icon{font-size:16px;flex-shrink:0;margin-top:2px}
.l-cred strong{display:block;font-size:13px;font-weight:700;margin-bottom:2px;color:var(--white)}
.l-cred span{font-size:12px;color:var(--light)}
/* ── FINAL CTA ── */
.l-final-cta{padding:100px 48px;text-align:center;background:linear-gradient(135deg,var(--l-bg-alt),var(--l-bg))}
.l-final-cta h2{font-family:'Space Grotesk',sans-serif;font-size:clamp(40px,7vw,88px);line-height:.9;letter-spacing:-0.03em;margin-bottom:16px;color:var(--white)}
.l-final-cta p{font-size:16px;color:var(--light);max-width:500px;margin:0 auto 36px}
.l-cta-pair{display:flex;gap:14px;justify-content:center;flex-wrap:wrap}
/* ── FOOTER ── */
.l-footer{padding:64px 0 28px;border-top:1px solid var(--l-border);background:var(--l-bg)}
.l-footer-top{display:flex;flex-direction:row;justify-content:space-between;align-items:center;gap:32px;padding-bottom:32px;border-bottom:1px solid var(--l-border)}
.l-footer-brand-col{display:flex;flex-direction:column;align-items:flex-start;text-align:left;max-width:380px}
.l-footer-desc{font-size:14px;color:var(--light);line-height:1.6;margin-top:8px}
.l-footer-links-col{display:flex;flex-direction:column;align-items:flex-end;gap:16px}
.l-footer-links{display:flex;flex-wrap:wrap;gap:12px;font-size:14px;align-items:center}
.l-footer-link{color:var(--light);font-weight:500;transition:color 0.15s}
.l-footer-link:hover{color:var(--l-red)}
.l-footer-dot{color:var(--l-border);opacity:0.6;font-size:14px}
.l-footer-socials{display:flex;gap:16px;font-size:12px;color:var(--light)}
.l-footer-socials a{transition:color 0.15s}
.l-footer-socials a:hover{color:var(--l-red)}
.l-footer-bottom{display:flex;flex-direction:row;justify-content:space-between;align-items:center;gap:16px;padding-top:24px;font-size:12px;color:var(--light)}
.l-footer-meta{display:flex;align-items:center;gap:12px}
.l-footer-meta a{font-weight:500;transition:color 0.15s}
.l-footer-meta a:hover{color:var(--l-red)}
/* ── RESPONSIVE ── */
@media(max-width:1024px){
  .l-nav{padding:0 32px}.l-container{padding:0 32px}
  .l-why-grid,.l-tools-grid{grid-template-columns:repeat(2,1fr)}
  .l-stages{grid-template-columns:repeat(4,1fr)}
}
@media(max-width:768px){
  .l-nav{padding:0 20px}.l-nav-right{display:none}
  .l-nav-mobile{display:flex}
  .l-header.scrolled .l-nav{width:calc(100% - 24px);padding:0 16px;height:52px}
  .l-hero{padding:60px 20px}.l-container{padding:0 20px}
  .l-course-grid{grid-template-columns:1fr}
  .l-why-grid{grid-template-columns:1fr 1fr}
  .l-transform-grid,.l-about-grid{grid-template-columns:1fr}
  .l-stages{grid-template-columns:repeat(4,1fr)}
  .l-outcomes-grid{grid-template-columns:1fr}
  .l-tools-grid{grid-template-columns:1fr 1fr}
  .l-paywall,.l-final-cta{padding:60px 20px}
  .l-footer-top{flex-direction:column;align-items:center;text-align:center;gap:24px}
  .l-footer-brand-col{align-items:center;text-align:center}
  .l-footer-links-col{align-items:center}
  .l-footer-bottom{flex-direction:column;align-items:center;text-align:center;gap:12px}
}
@media(max-width:480px){
  .l-why-grid,.l-tools-grid{grid-template-columns:1fr}
  .l-stages{grid-template-columns:repeat(3,1fr)}
  .l-nav{padding:0 14px}
  .l-header.scrolled .l-nav{width:calc(100% - 16px);padding:0 12px;height:48px}
  .l-brand-title{font-size:15px}
  .l-brand-sub{font-size:8px}
  .l-hero h1{font-size:clamp(38px,11vw,64px);letter-spacing:1px}
  .l-hero-sub{font-size:16px}
  .l-eyebrow-text{font-size:10px;letter-spacing:1.5px}
  .lang-dropdown summary{padding:6px 9px;font-size:11px;gap:5px}
  .lang-menu{min-width:150px}
}
"""

ONBOARDING_CSS = """
.ob-wrap{min-height:100vh;display:flex;align-items:center;justify-content:center;padding:40px 20px;background:var(--background);font-family:'Plus Jakarta Sans',sans-serif;position:relative}
.ob-wrap::after{content:'';position:fixed;inset:0;background-image:url("data:image/svg+xml,%3Csvg viewBox='0 0 200 200' xmlns='http://www.w3.org/2000/svg'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.85' numOctaves='4' stitchTiles='stitch'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23n)' opacity='0.035'/%3E%3C/svg%3E");pointer-events:none;z-index:9999;opacity:.5}
.ob-card{background:var(--card);border:1px solid var(--card-border);border-radius:4px;padding:44px;width:100%;max-width:520px;position:relative;z-index:100}
.ob-card::before{content:'';position:absolute;top:0;left:0;right:0;height:3px;background:#CC0A0A;border-radius:4px 4px 0 0}
.ob-brand{font-family:'Space Grotesk',sans-serif;font-weight:700;font-size:18px;letter-spacing:-0.02em;color:var(--foreground);margin-bottom:32px;display:block;text-decoration:none}
.ob-brand em{color:#CC0A0A;font-style:normal}
.ob-steps{display:flex;align-items:center;gap:6px;margin-bottom:36px}
.ob-step-dot{width:28px;height:28px;border-radius:50%;display:flex;align-items:center;justify-content:center;font-size:11px;font-weight:700;border:2px solid #2C2C30;color:#8E8E92;transition:all .2s;font-family:'JetBrains Mono',monospace}
.ob-step-dot.ob-active{border-color:#CC0A0A;color:#CC0A0A;background:rgba(204,10,10,.1)}
.ob-step-dot.ob-done{border-color:#CC0A0A;background:#CC0A0A;color:var(--foreground)}
.ob-step-line{flex:1;height:1px;background:#2C2C30}
.ob-heading{font-family:'Space Grotesk',sans-serif;font-size:32px;letter-spacing:-0.02em;color:var(--foreground);margin-bottom:8px}
.ob-sub{font-size:14px;color:#8E8E92;margin-bottom:28px}
.ob-options{display:flex;flex-direction:column;gap:10px;margin-bottom:28px}
.ob-option{display:flex;align-items:center;gap:14px;padding:16px 18px;background:var(--background);border:1px solid var(--card-border);border-radius:4px;cursor:pointer;font-size:14px;font-weight:500;color:#bbb;transition:all .15s;position:relative}
.ob-option:hover{border-color:#555;color:var(--foreground);background:#161412}
.ob-option input[type=radio]{position:absolute;opacity:0;width:0;height:0}
.ob-option.ob-selected{border-color:#CC0A0A;color:var(--foreground);background:rgba(204,10,10,.08)}
.ob-option-check{width:18px;height:18px;border-radius:50%;border:2px solid #2C2C30;flex-shrink:0;display:flex;align-items:center;justify-content:center;font-size:9px;color:transparent;transition:all .15s}
.ob-option.ob-selected .ob-option-check{border-color:#CC0A0A;background:#CC0A0A;color:var(--foreground)}
.ob-select{width:100%;background:var(--background);border:1px solid var(--card-border);border-radius:4px;color:#bbb;font-family:'Plus Jakarta Sans',sans-serif;font-size:14px;padding:12px 16px;transition:border-color .15s;outline:none;margin-bottom:28px;cursor:pointer}
.ob-select:focus{border-color:#CC0A0A}
.ob-select option{background:#161412}
.ob-form-group{margin-bottom:18px}
.ob-label{display:block;font-size:11px;font-weight:700;letter-spacing:1.5px;text-transform:uppercase;color:#8E8E92;margin-bottom:8px;font-family:'Space Grotesk',sans-serif}
.ob-input{width:100%;background:var(--background);border:1px solid var(--card-border);border-radius:4px;color:var(--foreground);font-family:'Plus Jakarta Sans',sans-serif;font-size:14px;padding:12px 16px;transition:border-color .15s;outline:none}
.ob-input:focus{border-color:#CC0A0A}
.ob-btn{width:100%;padding:14px;background:#CC0A0A;color:var(--foreground);font-family:'Plus Jakarta Sans',sans-serif;font-weight:800;font-size:12px;letter-spacing:2px;text-transform:uppercase;border:none;border-radius:4px;cursor:pointer;transition:all .2s}
.ob-btn:hover{background:#931E22;transform:translateY(-1px)}
.ob-back{background:none;border:none;color:#8E8E92;font-size:12px;font-weight:600;letter-spacing:1px;text-transform:uppercase;cursor:pointer;padding:0;font-family:'Plus Jakarta Sans',sans-serif;margin-top:14px;display:block}
.ob-back:hover{color:#bbb}
.ob-footer{text-align:center;margin-top:20px;font-size:13px;color:#8E8E92}
.ob-footer a{color:#CC0A0A;text-decoration:none}
.ob-alert{padding:12px 16px;background:rgba(204,10,10,.1);border:1px solid rgba(204,10,10,.3);border-radius:4px;color:#ff6b7a;font-size:13px;margin-bottom:20px}
.ob-fine{text-align:center;font-size:12px;color:#8E8E92;margin-top:12px}
@media(max-width:480px){.ob-card{padding:28px 20px}}
"""

JS = """
function showTab(btn, id) {
    document.querySelectorAll('.tab-panel').forEach(p => p.classList.remove('active'));
    document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
    document.getElementById(id).classList.add('active');
    btn.classList.add('active');
}
function toggleProfileMenu(btn) {
    const menu = btn.nextElementSibling;
    const isOpen = menu.classList.contains('open');
    document.querySelectorAll('.nav-dropdown.open').forEach(m => m.classList.remove('open'));
    if (!isOpen) menu.classList.add('open');
}
function toggleMobileMenu() {
    const menu = document.getElementById('lMobileMenu');
    const btn = document.getElementById('lHamburger');
    if (!menu) return;
    const open = menu.classList.toggle('open');
    btn && btn.setAttribute('aria-expanded', open);
}
function closeMobileMenu() {
    const menu = document.getElementById('lMobileMenu');
    const btn = document.getElementById('lHamburger');
    if (menu) menu.classList.remove('open');
    if (btn) btn.setAttribute('aria-expanded', 'false');
}
document.addEventListener('click', function(e) {
    if (!e.target.closest('.nav-profile')) {
        document.querySelectorAll('.nav-dropdown.open').forEach(m => m.classList.remove('open'));
    }
    if (!e.target.closest('.lang-dropdown')) {
        document.querySelectorAll('.lang-dropdown[open]').forEach(d => d.removeAttribute('open'));
    }
    if (!e.target.closest('.l-nav-mobile')) {
        closeMobileMenu();
    }
});
function toggleTheme() {
    if (document.documentElement.classList.contains('dark')) {
        document.documentElement.classList.remove('dark');
        localStorage.theme = 'light';
    } else {
        document.documentElement.classList.add('dark');
        localStorage.theme = 'dark';
    }
}
function updateHeaderScroll() {
    const header = document.querySelector('.l-header');
    if (header) {
        if (window.scrollY >= 50) {
            header.classList.add('scrolled');
        } else {
            header.classList.remove('scrolled');
        }
    }
}
window.addEventListener('scroll', updateHeaderScroll);
window.addEventListener('DOMContentLoaded', updateHeaderScroll);
setTimeout(updateHeaderScroll, 50);
async function aiDraft(subId, btn) {
    const ta = document.getElementById('fb-' + subId);
    const orig = btn.textContent;
    btn.disabled = true;
    btn.textContent = '⏳ Generating…';
    try {
        const res = await fetch('/coach/grade/' + subId + '/ai-suggest');
        const data = await res.json();
        if (data.feedback) {
            ta.value = data.feedback;
            ta.focus();
        } else {
            alert(data.error || 'AI draft unavailable — write feedback manually.');
        }
    } catch(e) {
        alert('Could not reach AI service. Write feedback manually.');
    } finally {
        btn.disabled = false;
        btn.textContent = orig;
    }
}
"""

def _page(title, body, nav="", extra_css="", lang="en"):
    return f"""<!DOCTYPE html>
<html lang="{lang}">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title} — Dako Studios Bootcamp</title>
<link rel="icon" type="image/svg+xml" href="/icon.svg">
<script>
  if (localStorage.theme === 'dark' || (!('theme' in localStorage) && window.matchMedia('(prefers-color-scheme: dark)').matches)) {{
    document.documentElement.classList.add('dark');
  }} else {{
    document.documentElement.classList.remove('dark');
  }}
</script>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@300;400;500;600;700;800&family=Space+Grotesk:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>{CSS}{extra_css}</style>
</head>
<body>
{nav}
<main>{body}</main>
<script>{JS}</script>
</body>
</html>"""

def _nav_student(student):
    pct = max(0, int((student["current_day"] - 1) / 20 * 100))
    free_badge = '' if student["paid_access"] else ' <span class="nav-badge">FREE</span>'
    return f"""<nav class="nav">
  <a href="/student" class="brand-link" style="display:flex;align-items:center;gap:10px;text-decoration:none;color:var(--foreground);">
    <svg width="22" height="21" viewBox="0 0 205 200" fill="none" xmlns="http://www.w3.org/2000/svg" style="color:var(--red);flex-shrink:0;"><path d="M 0 0 L 108 0 Q 205 0 205 100 Q 205 200 108 200 L 0 200 L 0 132 L 70 100 L 0 68 Z" fill="currentColor"/></svg>
    <div style="display:flex;flex-direction:column;justify-content:center;line-height:1;">
      <div style="font-family:'Space Grotesk',sans-serif;font-weight:800;font-size:14px;letter-spacing:-0.02em;color:var(--foreground);">DAKO</div>
      <div style="font-family:'Plus Jakarta Sans',sans-serif;font-size:7px;letter-spacing:0.15em;font-weight:700;color:#8E8E92;margin-top:1px;text-transform:uppercase;">ACADEMY</div>
    </div>
  </a>
  <div class="nav-right">
    <button class="theme-toggle" onclick="toggleTheme()" aria-label="Toggle Theme" style="margin: 0 10px;">
      <svg class="theme-icon-sun" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.93 4.93l1.41 1.41M17.66 17.66l1.41 1.41M2 12h2M20 12h2M6.34 17.66l-1.41 1.41M19.07 4.93l-1.41 1.41"/></svg>
      <svg class="theme-icon-moon" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3a6 6 0 0 0 9 9 9 9 0 1 1-9-9Z"/></svg>
    </button>
    <div style="display:flex;align-items:center;gap:12px">
      <div class="progress-wrap" style="width:110px"><div class="progress-fill" style="width:{pct}%"></div></div>
      <span style="color:rgba(255,255,255,.55);font-size:.7rem;font-family:'JetBrains Mono',monospace;font-weight:700">DAY {student['current_day']}/20</span>
    </div>
    <div class="nav-profile">
      <button class="nav-profile-btn" onclick="toggleProfileMenu(this)" aria-label="Profile menu">
        <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="8" r="4"/><path d="M4 20c0-4 3.6-7 8-7s8 3 8 7"/></svg>
      </button>
      <div class="nav-dropdown">
        <div class="nav-dropdown-header">{student['name'].split()[0]}{free_badge}</div>
        <a href="/student/account">Account Settings</a>
        <hr>
        <a href="/logout">Logout</a>
      </div>
    </div>
  </div>
</nav>"""

def _nav_coach(coach):
    pending = one("SELECT COUNT(*) as c FROM submissions WHERE submission_status='submitted' AND grading_status='pending'")["c"]
    badge = f'<span class="nav-badge">{pending}</span>' if pending else ""
    return f"""<nav class="nav">
  <a href="/coach/dashboard" class="brand-link" style="display:flex;align-items:center;gap:10px;text-decoration:none;color:var(--foreground);">
    <svg width="22" height="21" viewBox="0 0 205 200" fill="none" xmlns="http://www.w3.org/2000/svg" style="color:#fbbf24;flex-shrink:0;"><path d="M 0 0 L 108 0 Q 205 0 205 100 Q 205 200 108 200 L 0 200 L 0 132 L 70 100 L 0 68 Z" fill="currentColor"/></svg>
    <div style="display:flex;flex-direction:column;justify-content:center;line-height:1;">
      <div style="font-family:'Space Grotesk',sans-serif;font-weight:800;font-size:14px;letter-spacing:-0.02em;color:var(--foreground);">DAKO</div>
      <div style="font-family:'Plus Jakarta Sans',sans-serif;font-size:7px;letter-spacing:0.15em;font-weight:700;color:#fbbf24;margin-top:1px;text-transform:uppercase;">COACH</div>
    </div>
  </a>
  <div class="nav-right">
    <button class="theme-toggle" onclick="toggleTheme()" aria-label="Toggle Theme" style="margin: 0 10px;">
      <svg class="theme-icon-sun" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.93 4.93l1.41 1.41M17.66 17.66l1.41 1.41M2 12h2M20 12h2M6.34 17.66l-1.41 1.41M19.07 4.93l-1.41 1.41"/></svg>
      <svg class="theme-icon-moon" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3a6 6 0 0 0 9 9 9 9 0 1 1-9-9Z"/></svg>
    </button>
    <a href="/coach/dashboard">Dashboard {badge}</a>
    <a href="/coach/students">Students</a>
    <a href="/coach/curriculum">Curriculum</a>
    <a href="/coach/cohorts">Cohorts</a>
    <a href="/coach/payments">Payments</a>
    <a href="/coach/ops">Operations</a>
    <div class="nav-profile">
      <button class="nav-profile-btn" onclick="toggleProfileMenu(this)" aria-label="Profile menu">
        <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="8" r="4"/><path d="M4 20c0-4 3.6-7 8-7s8 3 8 7"/></svg>
      </button>
      <div class="nav-dropdown">
        <div class="nav-dropdown-header">{coach['name']}</div>
        <a href="/coach/account">Account Settings</a>
        <hr>
        <a href="/coach/logout">Logout</a>
      </div>
    </div>
  </div>
</nav>"""

# ─── Landing page ─────────────────────────────────────────────────────────────

_WEEK_TITLES = {
    1: "Computer & File System Fundamentals",
    2: "Internet, Email & Documents",
    3: "Research, Cloud & Cybersecurity",
    4: "AI Tools, Portfolio & Career",
}

_CT_STAGES = [
    ("01", "🔍", "AI Discovery",         "Before great content comes great understanding. In this stage creators use AI as a thinking partner to research their audience, map their niche, and surface insight that would take weeks to find manually."),
    ("02", "💡", "Strategic Ideation",   "Raw ideas mean nothing without structure. This stage transforms initial research into a defined content strategy — content pillars, audience personas, messaging frameworks, and a clear direction."),
    ("03", "🏗️", "System Design",         "Great creators build systems, not just content. You design your personal content operating system — workflows, calendars, batch production routines, AI delegation strategies."),
    ("04", "🎬", "Content Production",    "This is where thinking becomes making. Across video, written, visual, and audio formats, you learn the production fundamentals that allow a solo creator to produce at a studio standard."),
    ("05", "🚀", "Launch & Distribution", "Creating great content is only half the equation. Platform strategy, algorithm logic, scheduling systems, and campaign architecture — everything to put content in front of the right people."),
    ("06", "📊", "Performance Insight",   "Data is only valuable if you can act on it. You learn to read platform analytics, identify what is working, diagnose what is not, and build iteration cycles that compound improvement."),
    ("07", "♾️",  "Creative Evolution",   "The final stage is about long-term growth. Build in public, map your audience journey with UX thinking, and define what creative evolution means for your specific creator path."),
]

def _landing_page(t: dict, lang: str, days: list) -> str:
    weeks: dict[int, list] = {1: [], 2: [], 3: [], 4: []}
    for d in days:
        w = ((d["day"] - 1) // 5) + 1
        if 1 <= w <= 4:
            weeks[w].append(d)

    def week_html(w: int) -> str:
        rows = "".join(
            f'<div class="l-day-row">'
            f'<span class="l-day-num">DAY {d["day"]:02d}</span>'
            f'<span class="l-day-title">{d["title"]}</span>'
            f'<span class="l-day-badge {"l-day-free" if d["day"] <= FREE_DAYS else "l-day-locked"}">'
            f'{t["free_badge"] if d["day"] <= FREE_DAYS else t["locked_badge"]}</span>'
            f'</div>'
            for d in weeks[w]
        )
        open_attr = " open" if w == 1 else ""
        return (f'<details class="l-week"{open_attr}>'
                f'<summary>'
                f'<span class="l-week-label">{t["week"]} {w}</span>'
                f'<span class="l-week-name">{_WEEK_TITLES[w]}</span>'
                f'<span class="l-week-arr">▼</span>'
                f'</summary>'
                f'<div class="l-week-days">{rows}</div>'
                f'</details>')

    curriculum_html = "".join(week_html(w) for w in range(1, 5))

    stages_cards = "".join(
        f'<div class="l-stage{"  l-stage-active" if i == 0 else ""}" '
        f'onclick="lStage(this,{i})">'
        f'<div class="l-stage-num">{num}</div>'
        f'<span class="l-stage-icon">{icon}</span>'
        f'<div class="l-stage-name">{name}</div>'
        f'</div>'
        for i, (num, icon, name, _) in enumerate(_CT_STAGES)
    )
    stages_details = "".join(
        f'<div class="l-stage-detail{"  l-stage-active" if i == 0 else ""}" id="lsd{i}">'
        f'<h4>{num} — {name}</h4><p>{desc}</p>'
        f'</div>'
        for i, (num, _, name, desc) in enumerate(_CT_STAGES)
    )
    switcher = _lang_switcher(lang)
    lang_flat = _lang_switcher_flat(lang)

    return f"""<div class="landing-page">
<!-- NAV -->
<header class="l-header">
<nav class="l-nav">
  <a href="/" class="l-nav-brand">
    <svg width="24" height="23" viewBox="0 0 205 200" fill="none" xmlns="http://www.w3.org/2000/svg" style="color:var(--l-red);flex-shrink:0;">
      <path d="M 0 0 L 108 0 Q 205 0 205 100 Q 205 200 108 200 L 0 200 L 0 132 L 70 100 L 0 68 Z" fill="currentColor"/>
    </svg>
    <div class="l-brand-text">
      <div class="l-brand-title">DAKO</div>
      <div class="l-brand-sub">ACADEMY</div>
    </div>
  </a>
  <!-- desktop nav -->
  <div class="l-nav-right">
    {switcher}
    <a href="#digital-skills" class="l-nav-link">{t["nav_digital"]}</a>
    <a href="#creative-tech" class="l-nav-link">{t["nav_creative"]}</a>
    <a href="/login" class="l-nav-link">{t["nav_login"]}</a>
    <button class="theme-toggle" onclick="toggleTheme()" aria-label="Toggle Theme" style="margin: 0 10px;">
      <svg class="theme-icon-sun" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.93 4.93l1.41 1.41M17.66 17.66l1.41 1.41M2 12h2M20 12h2M6.34 17.66l-1.41 1.41M19.07 4.93l-1.41 1.41"/></svg>
      <svg class="theme-icon-moon" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3a6 6 0 0 0 9 9 9 9 0 1 1-9-9Z"/></svg>
    </button>
    <a href="/onboarding" class="l-nav-link l-nav-cta">{t["ds_cta"]}</a>
  </div>
  <!-- mobile controls: theme toggle + hamburger -->
  <div class="l-nav-mobile">
    <button class="theme-toggle" onclick="toggleTheme()" aria-label="Toggle Theme">
      <svg class="theme-icon-sun" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.93 4.93l1.41 1.41M17.66 17.66l1.41 1.41M2 12h2M20 12h2M6.34 17.66l-1.41 1.41M19.07 4.93l-1.41 1.41"/></svg>
      <svg class="theme-icon-moon" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3a6 6 0 0 0 9 9 9 9 0 1 1-9-9Z"/></svg>
    </button>
    <button class="l-hamburger" id="lHamburger" onclick="toggleMobileMenu()" aria-label="Menu" aria-expanded="false">
      <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"><line x1="2" y1="4.5" x2="14" y2="4.5"/><line x1="2" y1="8" x2="14" y2="8"/><line x1="2" y1="11.5" x2="14" y2="11.5"/></svg>
    </button>
    <!-- mobile dropdown menu -->
    <div class="l-mobile-menu" id="lMobileMenu">
      <a href="#digital-skills" class="l-mobile-link" onclick="closeMobileMenu()">{t["nav_digital"]}</a>
      <a href="#creative-tech" class="l-mobile-link" onclick="closeMobileMenu()">{t["nav_creative"]}</a>
      <a href="/login" class="l-mobile-link">{t["nav_login"]}</a>
      <a href="https://dako.studio" target="_blank" rel="noopener noreferrer" class="l-mobile-link" style="color:var(--l-muted)">Studio Hub ↗</a>
      <div class="l-mobile-sep"></div>
      <div class="l-mobile-lang-label">Language</div>
      <div class="l-mobile-lang-opts">{lang_flat}</div>
      <div class="l-mobile-sep"></div>
      <div class="l-mobile-cta-wrap">
        <a href="/onboarding" class="l-mobile-cta">{t["ds_cta"]}</a>
      </div>
    </div>
  </div>
</nav>
</header>

<!-- HERO -->
<section class="l-hero">
  <div class="l-hero-bg"></div>
  <div class="l-hero-lines"></div>
  <div class="l-hero-content">
    <div class="l-eyebrow"><span class="l-eyebrow-dot"></span><span class="l-eyebrow-text">{t["hero_eyebrow"]}</span></div>
    <h1>{t["hero_headline_1"]}<br><span class="l-accent">{t["hero_headline_accent"]}</span></h1>
    <p class="l-hero-sub">{t["hero_sub"]}</p>
  </div>
</section>

<!-- COURSE CARDS -->
<section class="l-courses">
  <div class="l-container">
    <div class="l-sec-label">Two Programmes</div>
    <h2 class="l-sec-title">Choose Your Path</h2>
    <div class="l-course-grid">
      <!-- Digital Skills -->
      <div class="l-course-card">
        <div class="l-course-badge">{t["ds_badge"]}</div>
        <div class="l-course-title">{t["ds_title"]}</div>
        <p class="l-course-desc">{t["ds_desc"]}</p>
        <div class="l-course-pills">
          <div class="l-pill"><span class="l-pill-dot"></span>{t["ds_pill_1"]}</div>
          <div class="l-pill"><span class="l-pill-dot"></span>{t["ds_pill_2"]}</div>
          <div class="l-pill"><span class="l-pill-dot"></span>{t["ds_pill_3"]}</div>
        </div>
        <div class="l-price-row"><span class="l-price">{t["ds_price"]}</span><span class="l-price-sub">{t["ds_price_sub"]}</span></div>
        <a href="/onboarding" class="l-cta">{t["ds_cta"]} →</a>
      </div>
      <!-- Creative Tech -->
      <div class="l-course-card">
        <div class="l-course-badge">{t["ct_badge"]}</div>
        <div class="l-course-title">{t["ct_title"]}</div>
        <p class="l-course-desc">{t["ct_desc"]}</p>
        <div class="l-course-pills">
          <div class="l-pill"><span class="l-pill-dot"></span>{t["ct_pill_1"]}</div>
          <div class="l-pill"><span class="l-pill-dot"></span>{t["ct_pill_2"]}</div>
          <div class="l-pill"><span class="l-pill-dot"></span>{t["ct_pill_3"]}</div>
        </div>
        <div class="l-price-row"><span class="l-price">{t["ct_price"]}</span><span class="l-price-sub">{t["ct_price_sub"]}</span></div>
        <a href="/apply/creative-tech" class="l-cta">{t["ct_cta"]} →</a>
      </div>
    </div>
  </div>
</section>

<!-- DIGITAL SKILLS DEEP-DIVE -->
<section class="l-section dark" id="digital-skills">
  <div class="l-container">
    <div class="l-sec-label">{t["ds_section_label"]}</div>
    <h2 class="l-sec-title">{t["ds_section_title"]}</h2>
    <p class="l-sec-desc">{t["ds_section_sub"]}</p>
    <!-- WHY IT WORKS -->
    <div style="margin-top:64px">
      <div class="l-sec-label">{t["why_label"]}</div>
      <h3 class="l-sec-title" style="font-size:clamp(28px,4vw,44px)">{t["why_title"]}</h3>
    </div>
    <div class="l-why-grid">
      <div class="l-why-card"><h4>{t["why_1_title"]}</h4><p>{t["why_1_body"]}</p></div>
      <div class="l-why-card"><h4>{t["why_2_title"]}</h4><p>{t["why_2_body"]}</p></div>
      <div class="l-why-card"><h4>{t["why_3_title"]}</h4><p>{t["why_3_body"]}</p></div>
      <div class="l-why-card"><h4>{t["why_4_title"]}</h4><p>{t["why_4_body"]}</p></div>
    </div>
  </div>
</section>

<!-- CURRICULUM -->
<section class="l-section">
  <div class="l-container">
    <div class="l-sec-label">{t["curriculum_label"]}</div>
    <h2 class="l-sec-title">{t["curriculum_title"]}</h2>
    <div class="l-weeks">{curriculum_html}</div>
  </div>
</section>

<!-- PAYWALL CALLOUT -->
<div class="l-paywall">
  <div class="l-sec-label">{t["paywall_label"]}</div>
  <h2>{t["paywall_title"]}</h2>
  <p>{t["paywall_sub"]}</p>
  <a href="/onboarding" class="l-cta">{t["paywall_cta"]} →</a>
</div>

<!-- CREATIVE TECH DEEP-DIVE -->
<section class="l-section dark" id="creative-tech">
  <div class="l-container">
    <div class="l-sec-label">Creative Tech Creator Bootcamp</div>
    <h2 class="l-sec-title">After This Bootcamp,<br>You Operate Differently.</h2>
    <div class="l-transform-grid">
      <div class="l-transform-col">
        <div class="l-transform-header"><span class="l-transform-tag l-tag-before">Before</span></div>
        <ul class="l-transform-list">
          <li class="l-muted-item"><span class="l-icon l-icon-x">✕</span>Random content creation with no structure</li>
          <li class="l-muted-item"><span class="l-icon l-icon-x">✕</span>Posting based on guesswork and trends</li>
          <li class="l-muted-item"><span class="l-icon l-icon-x">✕</span>No clear niche or audience direction</li>
          <li class="l-muted-item"><span class="l-icon l-icon-x">✕</span>Treating AI as a gimmick, not a tool</li>
          <li class="l-muted-item"><span class="l-icon l-icon-x">✕</span>No system for measuring what works</li>
        </ul>
      </div>
      <div class="l-transform-col after-col">
        <div class="l-transform-header"><span class="l-transform-tag l-tag-after">After</span></div>
        <ul class="l-transform-list">
          <li class="l-bright-item"><span class="l-icon l-icon-check">✓</span>A system-driven creator with repeatable workflows</li>
          <li class="l-bright-item"><span class="l-icon l-icon-check">✓</span>An AI-powered researcher who finds insight fast</li>
          <li class="l-bright-item"><span class="l-icon l-icon-check">✓</span>A strategic storyteller with a defined audience</li>
          <li class="l-bright-item"><span class="l-icon l-icon-check">✓</span>A digital media producer across video, audio, design</li>
          <li class="l-bright-item"><span class="l-icon l-icon-check">✓</span>A professional operating like a small creative studio</li>
        </ul>
      </div>
    </div>
  </div>
</section>

<!-- FRAMEWORK -->
<section class="l-section">
  <div class="l-container">
    <div style="text-align:center;margin-bottom:56px">
      <div class="l-sec-label">The Signature Framework</div>
      <h2 class="l-sec-title">Dakol's Creative AI System</h2>
      <p class="l-sec-desc" style="margin:0 auto">A 7-stage methodology that structures how modern creators think, build, and evolve.</p>
    </div>
    <div class="l-stages" id="lStageCards">{stages_cards}</div>
    <div id="lStageDetails">{stages_details}</div>
  </div>
</section>

<!-- OUTCOMES -->
<section class="l-section dark">
  <div class="l-container">
    <div class="l-sec-label">Your Portfolio Promise</div>
    <h2 class="l-sec-title">What You Will Have Built by Day 15</h2>
    <div class="l-outcomes-grid">
      <div class="l-outcome"><div class="l-outcome-n">01</div><div><strong>Personal AI Productivity Workflow</strong><span>A working system for using ChatGPT and Claude to accelerate research, ideation, scripting, and content planning.</span></div></div>
      <div class="l-outcome"><div class="l-outcome-n">02</div><div><strong>Content Strategy System</strong><span>Content pillars, audience persona, 4-week calendar, and a repeatable production workflow.</span></div></div>
      <div class="l-outcome"><div class="l-outcome-n">03</div><div><strong>Creator Brand Kit</strong><span>Visual identity, brand voice, typography, colour palette, and a completed 1-page brand board.</span></div></div>
      <div class="l-outcome"><div class="l-outcome-n">04</div><div><strong>Digital Campaign Concept</strong><span>A structured 5-day launch campaign with copy, visuals, platform strategy, and a campaign brief.</span></div></div>
      <div class="l-outcome"><div class="l-outcome-n">05</div><div><strong>Multi-Format Content Portfolio</strong><span>A produced video, written scripts, branded graphics, and a podcast episode.</span></div></div>
      <div class="l-outcome"><div class="l-outcome-n">06</div><div><strong>UX Product Idea</strong><span>An audience journey map and a UX-informed content experience concept developed in FigJam.</span></div></div>
      <div class="l-outcome"><div class="l-outcome-n">07</div><div><strong>Performance Analysis Report</strong><span>A written analysis demonstrating the ability to read data, find patterns, and make strategic decisions.</span></div></div>
      <div class="l-outcome"><div class="l-outcome-n">08</div><div><strong>Published First Piece of Content</strong><span>Day 15 is launch day. You leave having published — not just planned.</span></div></div>
    </div>
  </div>
</section>

<!-- TOOLS -->
<section class="l-section">
  <div class="l-container">
    <div style="text-align:center;margin-bottom:56px">
      <div class="l-sec-label">Tools You Will Learn</div>
      <h2 class="l-sec-title">The Professional Creator Stack</h2>
    </div>
    <div class="l-tools-grid">
      <div class="l-tool-cat"><div class="l-tool-cat-label">AI &amp; Research</div><div class="l-tool-items"><div class="l-tool-item"><div class="l-tool-icon">🤖</div>ChatGPT</div><div class="l-tool-item"><div class="l-tool-icon">🧠</div>Claude</div><div class="l-tool-item"><div class="l-tool-icon">🔎</div>Perplexity</div></div></div>
      <div class="l-tool-cat"><div class="l-tool-cat-label">Design &amp; Visual</div><div class="l-tool-items"><div class="l-tool-item"><div class="l-tool-icon">🎨</div>Canva</div><div class="l-tool-item"><div class="l-tool-icon">📐</div>Figma / FigJam</div><div class="l-tool-item"><div class="l-tool-icon">✨</div>Midjourney</div></div></div>
      <div class="l-tool-cat"><div class="l-tool-cat-label">Media Production</div><div class="l-tool-items"><div class="l-tool-item"><div class="l-tool-icon">🎙️</div>Riverside.fm</div><div class="l-tool-item"><div class="l-tool-icon">🎬</div>CapCut</div><div class="l-tool-item"><div class="l-tool-icon">🎞️</div>DaVinci Resolve</div></div></div>
      <div class="l-tool-cat"><div class="l-tool-cat-label">Productivity</div><div class="l-tool-items"><div class="l-tool-item"><div class="l-tool-icon">📋</div>Notion</div><div class="l-tool-item"><div class="l-tool-icon">✅</div>Asana</div><div class="l-tool-item"><div class="l-tool-icon">📅</div>Buffer / Later</div></div></div>
    </div>
  </div>
</section>

<!-- ABOUT -->
<section class="l-section dark">
  <div class="l-container">
    <div class="l-sec-label">Your Facilitator</div>
    <h2 class="l-sec-title" style="margin-bottom:48px">A Creative Technologist.<br>Not Just a Teacher.</h2>
    <div class="l-about-grid">
      <div class="l-about-col">
        <div class="l-about-role">Creative Tech Facilitator · Digital Media Producer</div>
        <div class="l-about-name">Dakol Masiyer</div>
        <p class="l-about-bio">Multidisciplinary producer, digital strategist, and entrepreneur with experience spanning film production, defence technology, content creation, and platform development. He operates as a creative technologist — bridging media, design, and AI systems.</p>
        <p class="l-about-bio">Each cohort is intentionally limited to ensure personalised attention. You are not one of hundreds. You are one of five.</p>
        <div class="l-about-tags">
          <span class="l-about-tag">AI Tools</span><span class="l-about-tag">Video Production</span><span class="l-about-tag">Content Strategy</span><span class="l-about-tag">UX Design</span><span class="l-about-tag">Podcast Production</span><span class="l-about-tag">Film Production</span>
        </div>
      </div>
      <div class="l-about-col">
        <div class="l-creds">
          <div class="l-cred"><div class="l-cred-icon">🎬</div><div><strong>Digital Media Producer</strong><span>Native Filmworks · First Features Project · Amazon Prime content</span></div></div>
          <div class="l-cred"><div class="l-cred-icon">🌍</div><div><strong>FIFA World Cup 2022 Volunteer</strong><span>Qatar — international production and cultural navigation</span></div></div>
          <div class="l-cred"><div class="l-cred-icon">⚙️</div><div><strong>Defence Technology &amp; Engineering</strong><span>DICON Ordnance Factory · NDA MSc Cybersecurity</span></div></div>
          <div class="l-cred"><div class="l-cred-icon">🚀</div><div><strong>Founder, Dakon Enterprises</strong><span>Dako Studios · DakoDash · SyncMaster Platform</span></div></div>
          <div class="l-cred"><div class="l-cred-icon">📊</div><div><strong>4+ Years Digital Strategy</strong><span>Integrated campaigns · UX · Content systems · Metrics</span></div></div>
        </div>
      </div>
    </div>
  </div>
</section>

<!-- FINAL CTA -->
<section class="l-final-cta">
  <div class="l-sec-label">Ready?</div>
  <h2>Pick Your Bootcamp.<br><span style="color:var(--red)">Start Today.</span></h2>
  <p>Self-paced digital skills or a live creative tech cohort. Both built to get you results.</p>
  <div class="l-cta-pair">
    <a href="/onboarding" class="l-cta">{t["ds_cta"]} →</a>
    <a href="/apply/creative-tech" class="l-cta l-cta-ghost">{t["ct_cta"]} →</a>
  </div>
</section>

<!-- FOOTER -->
<footer class="l-footer">
  <div class="l-container">
    <div class="l-footer-top">
      <div class="l-footer-brand-col">
        <div class="brand-link" style="display:inline-flex;align-items:center;gap:10px;text-decoration:none;color:var(--white);margin-bottom:10px;">
          <svg width="24" height="23" viewBox="0 0 205 200" fill="none" xmlns="http://www.w3.org/2000/svg" style="color:var(--l-red);flex-shrink:0;"><path d="M 0 0 L 108 0 Q 205 0 205 100 Q 205 200 108 200 L 0 200 L 0 132 L 70 100 L 0 68 Z" fill="currentColor"/></svg>
          <div style="display:flex;flex-direction:column;justify-content:center;line-height:1;text-align:left;">
            <div style="font-family:'Space Grotesk',sans-serif;font-weight:800;font-size:16px;letter-spacing:-0.02em;color:var(--white);">DAKO</div>
            <div style="font-family:'Plus Jakarta Sans',sans-serif;font-size:8px;letter-spacing:0.15em;font-weight:700;color:var(--muted);margin-top:1px;text-transform:uppercase;">ACADEMY</div>
          </div>
        </div>
        <p class="l-footer-desc">{t["footer_copy"]}</p>
      </div>
      <div class="l-footer-links-col">
        <div class="l-footer-links">
          <a href="https://dako.studio" target="_blank" rel="noopener noreferrer" class="l-footer-link">Studio Hub</a>
          <span class="l-footer-dot">·</span>
          <a href="https://dako.studio/labs" target="_blank" rel="noopener noreferrer" class="l-footer-link">Labs</a>
          <span class="l-footer-dot">·</span>
          <a href="https://dako.studio/#services" target="_blank" rel="noopener noreferrer" class="l-footer-link">Brand</a>
          <span class="l-footer-dot">·</span>
          <a href="https://dako.studio/#services" target="_blank" rel="noopener noreferrer" class="l-footer-link">Motion</a>
          <span class="l-footer-dot">·</span>
          <a href="https://dako.studio/#services" target="_blank" rel="noopener noreferrer" class="l-footer-link">Film</a>
          <span class="l-footer-dot">·</span>
          <a href="/" class="l-footer-link" style="color:var(--l-red);font-weight:600">Academy</a>
        </div>
        <div class="l-footer-socials">
          <a href="https://instagram.com/dako.studio" target="_blank" rel="noopener noreferrer">Instagram</a>
        </div>
      </div>
    </div>
    <div class="l-footer-bottom">
      <div>© 2026 Dako Studios. Abuja, Nigeria.</div>
      <div class="l-footer-meta">
        <a href="https://dako.studio/privacy" target="_blank" rel="noopener noreferrer">Privacy Policy</a>
        <span class="l-footer-dot">·</span>
        <a href="https://dako.studio/terms" target="_blank" rel="noopener noreferrer">Terms of Service</a>
        <span class="l-footer-dot">·</span>
        <span>Serving Nigeria &amp; the diaspora</span>
      </div>
    </div>
  </div>
</footer>

</div>
<script>
function lStage(el,idx){{
  document.querySelectorAll('.l-stage').forEach(s=>s.classList.remove('l-stage-active'));
  document.querySelectorAll('.l-stage-detail').forEach(d=>d.classList.remove('l-stage-active'));
  el.classList.add('l-stage-active');
  document.getElementById('lsd'+idx).classList.add('l-stage-active');
}}
</script>"""

@app.get("/icon.svg")
def get_icon():
    svg_data = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 205 200" fill="none"><path d="M 0 0 L 108 0 Q 205 0 205 100 Q 205 200 108 200 L 0 200 L 0 132 L 70 100 L 0 68 Z" fill="#C1272D" /></svg>"""
    return Response(content=svg_data, media_type="image/svg+xml")

@app.get("/favicon.ico")
def get_favicon():
    return RedirectResponse(url="/icon.svg")

# ─── Public: Auth ─────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    if _get_student(request): return RedirectResponse("/student", 302)
    lang = _get_lang(request)
    days = query("SELECT day, title FROM curriculum ORDER BY day")
    return HTMLResponse(_page("Welcome", _landing_page(get_t(lang), lang, days),
                              extra_css=LANDING_CSS + ONBOARDING_CSS, lang=lang))

@app.get("/login", response_class=HTMLResponse)
async def login_page():
    return HTMLResponse(_page("Login", _login_page(tab="login")))

@app.get("/register", response_class=HTMLResponse)
async def register_page():
    return RedirectResponse("/onboarding", 302)

def _login_page(error="", tab="login"):
    err = f'<div class="alert alert-error">{error}</div>' if error else ""
    return f"""<div class="login-wrap">
  <div class="login-box">
    <div class="login-logo" style="display:flex;flex-direction:column;align-items:center;margin-bottom:28px;">
      <a href="/" class="brand-link" style="display:inline-flex;align-items:center;gap:10px;text-decoration:none;color:var(--foreground);margin-bottom:12px;">
        <svg width="26" height="25" viewBox="0 0 205 200" fill="none" xmlns="http://www.w3.org/2000/svg" style="color:var(--red);flex-shrink:0;"><path d="M 0 0 L 108 0 Q 205 0 205 100 Q 205 200 108 200 L 0 200 L 0 132 L 70 100 L 0 68 Z" fill="currentColor"/></svg>
        <div style="display:flex;flex-direction:column;justify-content:center;line-height:1;text-align:left;">
          <div style="font-family:'Space Grotesk',sans-serif;font-weight:800;font-size:18px;letter-spacing:-0.02em;color:var(--foreground);">DAKO</div>
          <div style="font-family:'Plus Jakarta Sans',sans-serif;font-size:9px;letter-spacing:0.15em;font-weight:700;color:#8E8E92;margin-top:1px;text-transform:uppercase;">ACADEMY</div>
        </div>
      </a>
      <div class="tagline">Digital Skills Bootcamp · 20 Days</div>
    </div>
    {err}
    <div class="tab-nav">
      <button class="tab-btn {'active' if tab=='login' else ''}" onclick="showTab(this,'tab-login')">Login</button>
      <button class="tab-btn {'active' if tab=='register' else ''}" onclick="showTab(this,'tab-register')">Register</button>
    </div>
    <div id="tab-login" class="tab-panel {'active' if tab=='login' else ''}">
      <form method="POST" action="/login">
        <div class="form-group"><label class="form-label">Email</label><input type="email" name="email" placeholder="your@email.com" required></div>
        <div class="form-group"><label class="form-label">Password</label><input type="password" name="password" required></div>
        <button class="btn btn-dark btn-full">Login</button>
      </form>
    </div>
    <div id="tab-register" class="tab-panel {'active' if tab=='register' else ''}">
      <div style="text-align:center;padding:8px 0 16px">
        <div style="font-size:2rem;margin-bottom:12px">🎯</div>
        <p style="font-weight:700;font-size:1rem;margin:0 0 8px">New here?</p>
        <p style="color:#888;font-size:0.9rem;margin:0 0 20px">Days 1–{FREE_DAYS} are <strong>completely free</strong>. No card required to start.</p>
        <a href="/onboarding" class="btn btn-red btn-full" style="display:block;text-align:center;text-decoration:none">Start Free — Day 1 →</a>
      </div>
    </div>
    <div class="mt-3" style="text-align:center"><a href="/pricing" class="text-sm text-muted">See what's included →</a></div>
  </div>
</div>"""

@app.post("/login")
async def login(email: str = Form(...), password: str = Form(...)):
    student = one("SELECT * FROM students WHERE email=?", (email,))
    if not student or student["password_hash"] != _hash(password):
        return HTMLResponse(_page("Login", _login_page("Invalid email or password", "login")))
    tok = _token()
    run("INSERT INTO sessions (token, student_id) VALUES (?,?)", (tok, student["id"]))
    resp = RedirectResponse("/student", 302)
    resp.set_cookie("s_token", tok, httponly=True, max_age=86400 * 7, secure=True, samesite="lax")
    return resp

@app.post("/register")
async def register():
    return RedirectResponse("/onboarding", 302)

@app.get("/logout")
async def logout():
    resp = RedirectResponse("/", 302)
    resp.delete_cookie("s_token")
    return resp

# ─── Language ─────────────────────────────────────────────────────────────────

@app.post("/set-language")
async def set_language(request: Request, lang: str = Form(...)):
    if lang not in VALID_LANGS:
        lang = "en"
    resp = RedirectResponse(request.headers.get("referer", "/"), 302)
    resp.set_cookie("lang_pref", lang, httponly=False, max_age=86400 * 365,
                    samesite="lax", secure=True)
    return resp

# ─── Onboarding ───────────────────────────────────────────────────────────────

def _ob_progress(step: int) -> str:
    dots = ""
    for i in range(1, 4):
        cls = "ob-active" if i == step else ("ob-done" if i < step else "")
        check = "✓" if i < step else str(i)
        dots += f'<div class="ob-step-dot {cls}">{check}</div>'
        if i < 3:
            dots += '<div class="ob-step-line"></div>'
    return f'<div class="ob-steps">{dots}</div>'

def _ob_step1_page(t: dict, lang: str) -> str:
    opts = [
        ("beginner",   t["ob_opt_beginner"]),
        ("some_exp",   t["ob_opt_some"]),
        ("confident",  t["ob_opt_confident"]),
    ]
    options_html = "".join(
        f'<label class="ob-option" onclick="obSelect(this)">'
        f'<input type="radio" name="skill" value="{val}" required>'
        f'<div class="ob-option-check">✓</div>{label}</label>'
        for val, label in opts
    )
    switcher = _lang_switcher(lang)
    return f"""<div class="ob-wrap">
  <div class="ob-card">
    <a href="/" class="brand-link" style="display:inline-flex;align-items:center;gap:10px;text-decoration:none;color:var(--foreground);margin-bottom:32px;">
      <svg width="22" height="21" viewBox="0 0 205 200" fill="none" xmlns="http://www.w3.org/2000/svg" style="color:var(--red);flex-shrink:0;"><path d="M 0 0 L 108 0 Q 205 0 205 100 Q 205 200 108 200 L 0 200 L 0 132 L 70 100 L 0 68 Z" fill="currentColor"/></svg>
      <div style="display:flex;flex-direction:column;justify-content:center;line-height:1;text-align:left;">
        <div style="font-family:'Space Grotesk',sans-serif;font-weight:800;font-size:14px;letter-spacing:-0.02em;color:var(--foreground);">DAKO</div>
        <div style="font-family:'Plus Jakarta Sans',sans-serif;font-size:7px;letter-spacing:0.15em;font-weight:700;color:#8E8E92;margin-top:1px;text-transform:uppercase;">ACADEMY</div>
      </div>
    </a>
    {_ob_progress(1)}
    <h2 class="ob-heading">{t["ob_step1_heading"]}</h2>
    <p class="ob-sub">{t["ob_step1_sub"]}</p>
    <form method="POST" action="/onboarding/1">
      <div class="ob-options">{options_html}</div>
      <button type="submit" class="ob-btn">{t["ob_next"]} →</button>
    </form>
    <div class="ob-footer" style="margin-top:16px">{switcher}</div>
    <div class="ob-footer">{t["ob_already"]} <a href="/login">{t["ob_login"]}</a></div>
  </div>
</div>
<script>
function obSelect(el){{
  document.querySelectorAll('.ob-option').forEach(o=>o.classList.remove('ob-selected'));
  el.classList.add('ob-selected');
  el.querySelector('input').checked=true;
}}
</script>"""

def _ob_step2_page(t: dict, lang: str) -> str:
    countries = [
        "Nigeria", "Ghana", "Kenya", "South Africa", "Uganda", "Tanzania",
        "Ethiopia", "Rwanda", "Zambia", "Zimbabwe", "Senegal", "Côte d'Ivoire",
        "Cameroon", "Angola", "Mozambique", "Botswana",
        "Another African country", "Outside Africa",
    ]
    opts_html = "".join(f'<option value="{c}">{c}</option>' for c in countries)
    return f"""<div class="ob-wrap">
  <div class="ob-card">
    <a href="/" class="brand-link" style="display:inline-flex;align-items:center;gap:10px;text-decoration:none;color:var(--foreground);margin-bottom:32px;">
      <svg width="22" height="21" viewBox="0 0 205 200" fill="none" xmlns="http://www.w3.org/2000/svg" style="color:var(--red);flex-shrink:0;"><path d="M 0 0 L 108 0 Q 205 0 205 100 Q 205 200 108 200 L 0 200 L 0 132 L 70 100 L 0 68 Z" fill="currentColor"/></svg>
      <div style="display:flex;flex-direction:column;justify-content:center;line-height:1;text-align:left;">
        <div style="font-family:'Space Grotesk',sans-serif;font-weight:800;font-size:14px;letter-spacing:-0.02em;color:var(--foreground);">DAKO</div>
        <div style="font-family:'Plus Jakarta Sans',sans-serif;font-size:7px;letter-spacing:0.15em;font-weight:700;color:#8E8E92;margin-top:1px;text-transform:uppercase;">ACADEMY</div>
      </div>
    </a>
    {_ob_progress(2)}
    <h2 class="ob-heading">{t["ob_step2_heading"]}</h2>
    <p class="ob-sub">{t["ob_step2_sub"]}</p>
    <form method="POST" action="/onboarding/2">
      <select name="country" class="ob-select" required>
        <option value="" disabled selected>Select your country</option>
        {opts_html}
      </select>
      <button type="submit" class="ob-btn">{t["ob_next"]} →</button>
      <a href="/onboarding" class="ob-back">← {t["ob_back"]}</a>
    </form>
    <div class="ob-footer">{t["ob_already"]} <a href="/login">{t["ob_login"]}</a></div>
  </div>
</div>"""

def _ob_step3_page(t: dict, lang: str, error: str = "") -> str:
    err_html = f'<div class="ob-alert">{error}</div>' if error else ""
    return f"""<div class="ob-wrap">
  <div class="ob-card">
    <a href="/" class="brand-link" style="display:inline-flex;align-items:center;gap:10px;text-decoration:none;color:var(--foreground);margin-bottom:32px;">
      <svg width="22" height="21" viewBox="0 0 205 200" fill="none" xmlns="http://www.w3.org/2000/svg" style="color:var(--red);flex-shrink:0;"><path d="M 0 0 L 108 0 Q 205 0 205 100 Q 205 200 108 200 L 0 200 L 0 132 L 70 100 L 0 68 Z" fill="currentColor"/></svg>
      <div style="display:flex;flex-direction:column;justify-content:center;line-height:1;text-align:left;">
        <div style="font-family:'Space Grotesk',sans-serif;font-weight:800;font-size:14px;letter-spacing:-0.02em;color:var(--foreground);">DAKO</div>
        <div style="font-family:'Plus Jakarta Sans',sans-serif;font-size:7px;letter-spacing:0.15em;font-weight:700;color:#8E8E92;margin-top:1px;text-transform:uppercase;">ACADEMY</div>
      </div>
    </a>
    {_ob_progress(3)}
    <h2 class="ob-heading">{t["ob_step3_heading"]}</h2>
    <p class="ob-sub">{t["ob_step3_sub"]}</p>
    {err_html}
    <form method="POST" action="/onboarding/3">
      <div class="ob-form-group">
        <label class="ob-label">{t["ob_name_label"]}</label>
        <input type="text" name="name" class="ob-input" placeholder="Your full name" required>
      </div>
      <div class="ob-form-group">
        <label class="ob-label">{t["ob_email_label"]}</label>
        <input type="email" name="email" class="ob-input" placeholder="your@email.com" required>
      </div>
      <div class="ob-form-group">
        <label class="ob-label">{t["ob_pwd_label"]}</label>
        <input type="password" name="password" class="ob-input" placeholder="Min 6 characters" required minlength="6">
      </div>
      <input type="hidden" name="preferred_lang" value="{lang}">
      <button type="submit" class="ob-btn">{t["ob_submit"]}</button>
    </form>
    <p class="ob-fine">{t["ob_days_free"]} · {t["ob_full_access"]}</p>
    <div class="ob-footer">{t["ob_already"]} <a href="/login">{t["ob_login"]}</a></div>
  </div>
</div>"""

@app.get("/onboarding", response_class=HTMLResponse)
async def onboarding_step1(request: Request):
    if _get_student(request): return RedirectResponse("/student", 302)
    lang = _get_lang(request)
    t = get_t(lang)
    return HTMLResponse(_page("Get Started", _ob_step1_page(t, lang),
                              extra_css=LANDING_CSS + ONBOARDING_CSS, lang=lang))

@app.post("/onboarding/1")
async def onboarding_post1(request: Request, skill: str = Form(...)):
    valid = {"beginner", "some_exp", "confident"}
    if skill not in valid:
        skill = "beginner"
    resp = RedirectResponse("/onboarding/2", 302)
    resp.set_cookie("ob_skill", skill, httponly=False, max_age=3600, samesite="lax", secure=True)
    return resp

@app.get("/onboarding/2", response_class=HTMLResponse)
async def onboarding_step2(request: Request):
    if not request.cookies.get("ob_skill"):
        return RedirectResponse("/onboarding", 302)
    lang = _get_lang(request)
    t = get_t(lang)
    return HTMLResponse(_page("Where Are You Based?", _ob_step2_page(t, lang),
                              extra_css=LANDING_CSS + ONBOARDING_CSS, lang=lang))

@app.post("/onboarding/2")
async def onboarding_post2(request: Request, country: str = Form(...)):
    resp = RedirectResponse("/onboarding/3", 302)
    resp.set_cookie("ob_country", country[:100], httponly=False, max_age=3600,
                    samesite="lax", secure=True)
    return resp

@app.get("/onboarding/3", response_class=HTMLResponse)
async def onboarding_step3(request: Request):
    if not request.cookies.get("ob_skill") or not request.cookies.get("ob_country"):
        return RedirectResponse("/onboarding", 302)
    lang = _get_lang(request)
    t = get_t(lang)
    return HTMLResponse(_page("Create Account", _ob_step3_page(t, lang),
                              extra_css=LANDING_CSS + ONBOARDING_CSS, lang=lang))

@app.post("/onboarding/3")
async def onboarding_post3(
    request: Request,
    name: str = Form(...),
    email: str = Form(...),
    password: str = Form(...),
    preferred_lang: str = Form("en"),
):
    ob_skill   = request.cookies.get("ob_skill", "")
    ob_country = request.cookies.get("ob_country", "")
    lang       = _get_lang(request)
    t          = get_t(lang)
    if not ob_skill or not ob_country:
        return RedirectResponse("/onboarding", 302)
    if one("SELECT id FROM students WHERE email=?", (email.strip(),)):
        return HTMLResponse(_page("Create Account",
                                  _ob_step3_page(t, lang, error=t["ob_error_email"]),
                                  extra_css=LANDING_CSS + ONBOARDING_CSS, lang=lang))
    if preferred_lang not in VALID_LANGS:
        preferred_lang = "en"
    sid = run(
        "INSERT INTO students (name,email,password_hash,current_day,paid_access,"
        "skill_level,country,preferred_lang) VALUES (?,?,?,1,0,?,?,?)",
        (name.strip(), email.strip(), _hash(password), ob_skill, ob_country, preferred_lang),
    )
    tok = _token()
    run("INSERT INTO sessions (token, student_id) VALUES (?,?)", (tok, sid))
    from email_service import send_welcome
    send_welcome(name.strip(), email.strip(), lang=preferred_lang)
    resp = RedirectResponse("/student?welcome=1", 302)
    resp.set_cookie("s_token", tok, httponly=True, max_age=86400 * 7, secure=True, samesite="lax")
    resp.delete_cookie("ob_skill")
    resp.delete_cookie("ob_country")
    return resp

# ─── Creative Tech Apply ──────────────────────────────────────────────────────

def _ct_apply_page(t: dict, lang: str, success: bool = False, error: str = "") -> str:
    if success:
        body = f"""<div class="ob-wrap">
  <div class="ob-card" style="text-align:center">
    <div style="font-size:48px;margin-bottom:16px">✓</div>
    <h2 class="ob-heading" style="color:#E11D2E">{t.get("ct_apply_success","Application received.")}</h2>
    <p style="color:#bbb;margin-top:12px;font-size:14px">Dakol will be in touch within 48 hours.</p>
    <a href="/" class="ob-btn" style="display:block;margin-top:28px;text-decoration:none">← Back to Home</a>
  </div>
</div>"""
        return body
    err_html = f'<div class="ob-alert">{error}</div>' if error else ""
    countries_html = "".join(
        f'<option value="{c}">{c}</option>'
        for c in ["Nigeria","Ghana","Kenya","South Africa","Uganda","Tanzania",
                  "Ethiopia","Rwanda","Another African country","Outside Africa"]
    )
    return f"""<div class="ob-wrap" style="align-items:flex-start;padding-top:80px">
  <div class="ob-card" style="max-width:600px">
    <a href="/" class="brand-link" style="display:inline-flex;align-items:center;gap:10px;text-decoration:none;color:var(--foreground);margin-bottom:32px;">
      <svg width="22" height="21" viewBox="0 0 205 200" fill="none" xmlns="http://www.w3.org/2000/svg" style="color:var(--red);flex-shrink:0;"><path d="M 0 0 L 108 0 Q 205 0 205 100 Q 205 200 108 200 L 0 200 L 0 132 L 70 100 L 0 68 Z" fill="currentColor"/></svg>
      <div style="display:flex;flex-direction:column;justify-content:center;line-height:1;text-align:left;">
        <div style="font-family:'Space Grotesk',sans-serif;font-weight:800;font-size:14px;letter-spacing:-0.02em;color:var(--foreground);">DAKO</div>
        <div style="font-family:'Plus Jakarta Sans',sans-serif;font-size:7px;letter-spacing:0.15em;font-weight:700;color:#8E8E92;margin-top:1px;text-transform:uppercase;">ACADEMY</div>
      </div>
    </a>
    <h2 class="ob-heading">{t["ct_apply_heading"]}</h2>
    <p class="ob-sub">{t["ct_apply_sub"]}</p>
    {err_html}
    <form method="POST" action="/apply/creative-tech">
      <div class="ob-form-group">
        <label class="ob-label">{t["ct_apply_name"]}</label>
        <input type="text" name="name" class="ob-input" required>
      </div>
      <div class="ob-form-group">
        <label class="ob-label">{t["ct_apply_email"]}</label>
        <input type="email" name="email" class="ob-input" required>
      </div>
      <div class="ob-form-group">
        <label class="ob-label">{t["ct_apply_country"]}</label>
        <select name="country" class="ob-select" required>
          <option value="" disabled selected>Select</option>
          {countries_html}
        </select>
      </div>
      <div class="ob-form-group">
        <label class="ob-label">{t["ct_apply_background"]}</label>
        <textarea name="background" class="ob-input" style="min-height:90px;resize:vertical"
          placeholder="{t["ct_apply_background_ph"]}" required></textarea>
      </div>
      <div class="ob-form-group">
        <label class="ob-label">{t["ct_apply_motivation"]}</label>
        <textarea name="motivation" class="ob-input" style="min-height:90px;resize:vertical"
          placeholder="{t["ct_apply_motivation_ph"]}" required></textarea>
      </div>
      <button type="submit" class="ob-btn">{t["ct_apply_submit"]}</button>
    </form>
  </div>
</div>"""

@app.get("/apply/creative-tech", response_class=HTMLResponse)
async def ct_apply_get(request: Request):
    lang = _get_lang(request)
    t = get_t(lang)
    success = request.query_params.get("success") == "1"
    return HTMLResponse(_page("Apply — Creative Tech Bootcamp",
                              _ct_apply_page(t, lang, success=success),
                              extra_css=LANDING_CSS + ONBOARDING_CSS, lang=lang))

@app.post("/apply/creative-tech")
async def ct_apply_post(
    request: Request,
    name: str = Form(...),
    email: str = Form(...),
    country: str = Form(...),
    background: str = Form(...),
    motivation: str = Form(...),
):
    run("INSERT INTO creative_tech_applications (name,email,country,background,motivation)"
        " VALUES (?,?,?,?,?)",
        (name.strip(), email.strip(), country, background.strip(), motivation.strip()))
    return RedirectResponse("/apply/creative-tech?success=1", 302)

# ─── Public: Pricing ──────────────────────────────────────────────────────────

@app.get("/pricing", response_class=HTMLResponse)
async def pricing(request: Request):
    student = _get_student(request)
    cta = '<a href="/payment/checkout" class="btn btn-red btn-lg btn-full">Unlock All 20 Days</a>' if student and not student["paid_access"] else '<a href="/register" class="btn btn-red btn-lg btn-full">Start Free — Days 1–3</a>'
    if student:
        _pcurrency, _pprice = _student_currency_price(student)
    else:
        _pcurrency, _pprice = "USD", PRICE_USD
    _psym = _currency_symbol(_pcurrency)
    body = f"""
<div class="pricing-hero">
  <h1>The Complete Digital Skills Bootcamp</h1>
  <p>20 days. Practical missions. Real skills you can use from Day 1. Built for Africa.</p>
  <div class="flex gap-3 items-center justify-between" style="max-width:500px;margin:0 auto;flex-wrap:wrap">
    <div style="color:rgba(255,255,255,.7);font-size:.9rem">✓ Mobile-friendly lessons</div>
    <div style="color:rgba(255,255,255,.7);font-size:.9rem">✓ Coach-reviewed submissions</div>
    <div style="color:rgba(255,255,255,.7);font-size:.9rem">✓ Certificate on completion</div>
  </div>
</div>
<div class="container" style="max-width:900px">
  <div class="pricing-card">
    <div style="text-align:center;margin-bottom:24px">
      <div style="font-size:.8rem;font-weight:700;text-transform:uppercase;letter-spacing:.08em;color:#6b7280;margin-bottom:8px">Full Bootcamp Access</div>
      <div class="paywall-price"><span class="paywall-currency">{_psym}</span>{int(_pprice):,}</div>
      <div class="text-muted text-sm mt-2">One-time payment · Lifetime access · All payment methods{" · Nigerian students pay in ₦" if _pcurrency == "USD" and not student else ""}</div>
    </div>
    <ul class="feature-list">
      <li>Days 1–3 FREE — no card needed to start</li>
      <li>Full lesson content for all 20 days</li>
      <li>Video lessons + written guides</li>
      <li>Submit work &amp; get coach feedback</li>
      <li>Unlock next day only after passing</li>
      <li>Digital certificate on completion</li>
      <li>Pay by card, M-Pesa, MTN MoMo, bank transfer</li>
    </ul>
    <div style="margin-top:24px">{cta}</div>
    <div style="text-align:center;margin-top:12px"><span class="text-xs text-muted">Secure payment via Flutterwave</span></div>
  </div>

  <div class="card" style="margin-top:40px">
    <div class="card-title">What you will master</div>
    <div class="grid-3">
      {''.join(f'<div style="padding:12px;background:#f9fafb;border-radius:8px"><div style="font-size:.7rem;font-weight:700;color:#E63946;text-transform:uppercase;margin-bottom:4px">Days {days}</div><div style="font-size:.875rem;font-weight:600">{topic}</div></div>'
        for days, topic in [
          ("1–5","Computer & File System Fundamentals"),
          ("6–10","Internet, Email & Documents"),
          ("11–15","Research, Cloud & Cybersecurity"),
          ("16–20","AI Tools, Portfolio & Career"),
      ])}
    </div>
  </div>
</div>"""
    return HTMLResponse(_page("Pricing", body))

# ─── Payment: Flutterwave ─────────────────────────────────────────────────────

@app.get("/payment/checkout")
async def payment_checkout(request: Request):
    student = _get_student(request)
    if not student:
        return RedirectResponse("/", 302)
    if student["paid_access"]:
        return RedirectResponse("/student", 302)

    currency, price = _student_currency_price(student)
    payment_options = "card,ussd,banktransfer" if currency == "NGN" else "card"

    if not FLW_SECRET:
        if _is_local_dev() and ALLOW_PAYMENT_DEV_BYPASS:
            tx_ref = f"bootcamp-{student['id']}-{uuid.uuid4().hex[:8]}"
            now = datetime.utcnow().isoformat()[:19]
            run(
                """
                INSERT OR REPLACE INTO payments (
                    student_id, amount, currency, tx_ref, status, verification_status, verified_at,
                    webhook_received_at, reconciliation_attempts, last_reconciliation_error, flw_ref
                ) VALUES (?, ?, ?, ?, 'success', 'verified', ?, ?, 0, NULL, 'dev-bypass')
                """,
                (student["id"], price, currency, tx_ref, now, now),
            )
            run("UPDATE students SET paid_access=1 WHERE id=?", (student["id"],))
            log_payment_event("payment_initiated", tx_ref, student["id"], price)
            log_payment_event("payment_verified", tx_ref, student["id"], price, flw_ref="dev-bypass", status="success")
            log_payment_event("enrollment_activated", tx_ref, student["id"], price, flw_ref="dev-bypass")
            from email_service import send_payment_confirmed
            send_payment_confirmed(student["name"], student["email"], lang=student.get("preferred_lang", "en"))
            return RedirectResponse("/student?payment=success", 302)

        return HTMLResponse(_page("Payment",
            '<div class="container"><div class="alert alert-warn">Payment is not configured yet. '
            'Set FLUTTERWAVE_SECRET_KEY or FLW_CLIENT_SECRET in your environment variables.</div></div>'))

    tx_ref = f"bootcamp-{student['id']}-{uuid.uuid4().hex[:8]}"
    run("INSERT OR IGNORE INTO payments (student_id, amount, currency, tx_ref, status) VALUES (?,?,?,?,?)",
        (student["id"], price, currency, tx_ref, "pending"))

    log_payment_event("payment_initiated", tx_ref, student["id"], price)

    payload = {
        "tx_ref": tx_ref,
        "amount": price,
        "currency": currency,
        "redirect_url": f"{BASE_URL}/payment/return",
        "customer": {
            "email": student["email"],
            "name":  student["name"],
        },
        "customizations": {
            "title": "Dako Studios Bootcamp",
            "description": f"Full 20-day Digital Skills Bootcamp (Days {FREE_DAYS+1}–20)",
            "logo": f"{BASE_URL}/static/logo.png",
        },
        "payment_options": payment_options,
    }

    try:
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.post(
                "https://api.flutterwave.com/v3/payments",
                json=payload,
                headers=_flutterwave_auth_headers()
            )
        try:
            data = resp.json()
        except Exception:
            data = {"message": resp.text or "Flutterwave returned a non-JSON response"}
    except Exception as exc:
        log_payment_event("checkout_failed", tx_ref, student["id"], PRICE_USD, error=str(exc))
        return HTMLResponse(_page("Payment Error",
            '<div class="container"><div class="alert alert-error">Could not reach Flutterwave. '
            '<a href="/pricing">Try again</a>.</div></div>'))

    if data.get("status") == "success":
        return RedirectResponse(data["data"]["link"], 302)

    log_payment_event("checkout_failed", tx_ref, student["id"], PRICE_USD, flw_ref=None, error=f"Flutterwave API Error: Status Code {resp.status_code}, Response: {resp.text}")
    return HTMLResponse(_page("Payment Error",
        f'<div class="container"><div class="alert alert-error">Could not initiate payment: '
        f'{data.get("message","Unknown error")}. <a href="/pricing">Try again</a>.</div></div>'))


@app.get("/payment/return")
async def payment_return(request: Request, tx_ref: str = "", status: str = "", transaction_id: str = ""):
    student = _get_student(request)
    if not student:
        return RedirectResponse("/", 302)

    if status == "cancelled" or status == "failed" or not tx_ref:
        return RedirectResponse("/pricing?payment=cancelled", 302)

    # Check DB first — webhook may have already processed it
    payment = one("SELECT * FROM payments WHERE tx_ref=?", (tx_ref,))
    if payment and payment["status"] == "success":
        return RedirectResponse("/student?payment=success", 302)
    if payment and payment["status"] == "failed":
        return RedirectResponse("/pricing?payment=failed", 302)

    # Webhook hasn't fired yet — verify directly via Flutterwave API right now
    if payment and status == "completed":
        try:
            verified, result = await _verify_flutterwave_transaction(
                tx_ref, float(payment["amount"]), payment["currency"],
                transaction_id=transaction_id,
            )
            if verified:
                now = datetime.utcnow().isoformat()[:19]
                run("UPDATE payments SET status='success', verification_status='verified', verified_at=?, flw_ref=?, updated_at=? WHERE id=?",
                    (now, transaction_id, now, payment["id"]))
                run("UPDATE students SET paid_access=1 WHERE id=?", (payment["student_id"],))
                log_payment_event("payment_verified", tx_ref, payment["student_id"], payment["amount"],
                                  flw_ref=transaction_id, status="success")
                _s = one("SELECT name, email, preferred_lang FROM students WHERE id=?", (payment["student_id"],))
                if _s:
                    from email_service import send_payment_confirmed
                    send_payment_confirmed(_s["name"], _s["email"], lang=_s.get("preferred_lang") or "en")
                return RedirectResponse("/student?payment=success", 302)
        except Exception:
            pass  # fall through to holding page

    # Fallback holding page — shown only if verification is still pending
    holding_body = f"""
<div class="container" style="max-width:520px;padding-top:80px;text-align:center">
  <div class="card" style="padding:48px 36px">
    <div style="font-size:3rem;margin-bottom:16px;animation:spin 1.5s linear infinite;display:inline-block">⏳</div>
    <h2 style="font-size:1.4rem;font-weight:800;margin-bottom:8px">Confirming your payment…</h2>
    <p class="text-muted" style="margin-bottom:24px">We're verifying with Flutterwave. This usually takes a few seconds.</p>
    <p style="font-size:.78rem;color:#9ca3af;margin-bottom:28px">Ref: {tx_ref}</p>
    <a href="/student" class="btn btn-red btn-lg" style="display:inline-block;min-width:200px">Go to Dashboard</a>
    <p class="text-muted text-sm mt-3">Redirecting automatically in <span id="cd">8</span>s…</p>
  </div>
</div>
<style>@keyframes spin{{to{{transform:rotate(360deg)}}}}</style>
<script>
  var s=8,el=document.getElementById('cd');
  var t=setInterval(function(){{s--;el.textContent=s;if(s<=0){{clearInterval(t);window.location='/student';}}}},1000);
</script>"""
    return HTMLResponse(_page("Payment Processing", holding_body))

@app.post("/payment/webhook")
async def payment_webhook(request: Request):
    raw_body = await request.body()
    if not _verify_flutterwave_webhook(raw_body, request.headers):
        raise HTTPException(401, "Invalid signature")
        
    try:
        body = json.loads(raw_body.decode("utf-8"))
    except Exception:
        raise HTTPException(400, "Invalid JSON payload")
        
    event_type = body.get("event")
    data = body.get("data", {})
    tx_ref = data.get("tx_ref")
    flw_ref = str(data.get("id", "")) or data.get("flw_ref")
    
    if not tx_ref:
        return {"status": "ignored", "reason": "no tx_ref"}
        
    # Log the raw payload
    webhook_id = run(
        "INSERT INTO webhook_logs (tx_ref, flw_ref, event_type, payload_json, status) VALUES (?,?,?,?,?)",
        (tx_ref, flw_ref, event_type, json.dumps(body), "received")
    )
    
    payment = one("SELECT * FROM payments WHERE tx_ref=?", (tx_ref,))
    if not payment:
        run("UPDATE webhook_logs SET status='ignored_not_found' WHERE id=?", (webhook_id,))
        return {"status": "ignored", "reason": "tx_ref not found"}
        
    log_payment_event("webhook_received", tx_ref, payment["student_id"], payment["amount"], flw_ref=flw_ref, webhook_event_id=str(webhook_id))

    if payment["status"] == "success":
        # Idempotency
        run("UPDATE webhook_logs SET status='ignored_already_success' WHERE id=?", (webhook_id,))
        return {"status": "success", "message": "already processed"}
        
    if event_type != "charge.completed" or data.get("status") != "successful":
        run("UPDATE webhook_logs SET status='ignored_not_success' WHERE id=?", (webhook_id,))
        return {"status": "ignored", "reason": "event not successful"}

    # We must do an out-of-band verification via API to ensure the amount is correct
    verified = False
    error_msg = None
    provider_payload = body
    try:
        verified, verification_payload_or_error = await _verify_flutterwave_transaction(
            tx_ref,
            float(payment["amount"]),
            payment["currency"],
            transaction_id=str(data.get("id") or flw_ref),
        )
        if verified:
            provider_payload = verification_payload_or_error or {}
        else:
            error_msg = str(verification_payload_or_error)
    except Exception as e:
        error_msg = str(e)

    if not verified:
        # Handle reconciliation failure or actual mismatch
        run("UPDATE webhook_logs SET status='failed_verification' WHERE id=?", (webhook_id,))
        run(
            "UPDATE payments SET reconciliation_attempts=reconciliation_attempts+1, last_reconciliation_error=?, raw_provider_payload=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (error_msg, json.dumps(body), payment["id"])
        )
        log_payment_event("reconciliation_retry", tx_ref, payment["student_id"], payment["amount"], flw_ref=flw_ref, error=error_msg)
        raise HTTPException(502, f"Verification failed: {error_msg}")

    # Atomic enrollment activation
    try:
        with db.transaction(immediate=True) as conn:
            current_status = conn.execute(
                "SELECT status FROM payments WHERE id=?",
                (payment["id"],)
            ).fetchone()["status"]
            if current_status == "success":
                conn.execute("UPDATE webhook_logs SET status='ignored_already_success_lock' WHERE id=?", (webhook_id,))
                return {"status": "success"}

            now = datetime.utcnow().isoformat()[:19]
            conn.execute(
                "UPDATE payments SET status='success', verification_status='verified', verified_at=?, flw_ref=?, webhook_event_id=?, webhook_received_at=?, raw_provider_payload=?, updated_at=? WHERE id=?",
                (now, flw_ref, str(webhook_id), now, json.dumps(provider_payload), now, payment["id"])
            )
            conn.execute("UPDATE students SET paid_access=1 WHERE id=?", (payment["student_id"],))
            conn.execute("UPDATE webhook_logs SET status='processed' WHERE id=?", (webhook_id,))
    except Exception as e:
        run("UPDATE webhook_logs SET status='failed_db_lock' WHERE id=?", (webhook_id,))

        run(
            "UPDATE payments SET reconciliation_attempts=reconciliation_attempts+1, last_reconciliation_error=?, raw_provider_payload=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (str(e), json.dumps(body), payment["id"])
        )
        log_payment_event("reconciliation_retry", tx_ref, payment["student_id"], payment["amount"], flw_ref=flw_ref, error=f"DB error: {str(e)}")
        raise HTTPException(500, f"DB Error: {str(e)}")
        
    log_payment_event("payment_verified", tx_ref, payment["student_id"], payment["amount"], flw_ref=flw_ref, webhook_event_id=str(webhook_id), status="success")
    log_payment_event("enrollment_activated", tx_ref, payment["student_id"], payment["amount"], flw_ref=flw_ref, webhook_event_id=str(webhook_id))
    _s = one("SELECT name, email, preferred_lang FROM students WHERE id=?", (payment["student_id"],))
    if _s:
        from email_service import send_payment_confirmed
        send_payment_confirmed(_s["name"], _s["email"], lang=_s.get("preferred_lang") or "en")

    return {"status": "success"}

async def reconcile_pending_payments(limit: int = 25):
    pending = query(
        """
        SELECT *
        FROM payments
        WHERE status='pending'
        ORDER BY created_at ASC
        LIMIT ?
        """,
        (limit,),
    )

    summary = {"checked": 0, "verified": 0, "failed": 0, "skipped": 0}
    for payment in pending:
        summary["checked"] += 1
        tx_ref = payment["tx_ref"]
        transaction_id = str(payment["flw_ref"] or "")

        try:
            verified, provider_payload_or_error = await _verify_flutterwave_transaction(
                tx_ref,
                float(payment["amount"]),
                payment["currency"],
                transaction_id=transaction_id,
            )
            if not verified:
                summary["failed"] += 1
                run(
                    """
                    UPDATE payments
                    SET reconciliation_attempts=reconciliation_attempts+1,
                        last_reconciliation_error=?,
                        raw_provider_payload=COALESCE(raw_provider_payload, ?),
                        updated_at=CURRENT_TIMESTAMP
                    WHERE id=?
                    """,
                    (str(provider_payload_or_error), json.dumps({"tx_ref": tx_ref}), payment["id"]),
                )
                continue

            provider_payload = provider_payload_or_error or {}
            now = datetime.utcnow().isoformat()[:19]
            with db.transaction(immediate=True) as conn:
                current = conn.execute("SELECT status FROM payments WHERE id=?", (payment["id"],)).fetchone()
                if current and current["status"] == "success":
                    summary["skipped"] += 1
                    continue
                conn.execute(
                    """
                    UPDATE payments
                    SET status='success',
                        verification_status='verified',
                        verified_at=?,
                        webhook_received_at=COALESCE(webhook_received_at, ?),
                        raw_provider_payload=?,
                        updated_at=?
                    WHERE id=?
                    """,
                    (now, now, json.dumps(provider_payload), now, payment["id"]),
                )
                conn.execute("UPDATE students SET paid_access=1 WHERE id=?", (payment["student_id"],))
            summary["verified"] += 1
            log_payment_event(
                "payment_verified",
                tx_ref,
                payment["student_id"],
                payment["amount"],
                flw_ref=payment["flw_ref"],
                status="success",
            )
            log_payment_event(
                "enrollment_activated",
                tx_ref,
                payment["student_id"],
                payment["amount"],
                flw_ref=payment["flw_ref"],
            )
        except Exception as exc:
            summary["failed"] += 1
            run(
                """
                UPDATE payments
                SET reconciliation_attempts=reconciliation_attempts+1,
                    last_reconciliation_error=?,
                    updated_at=CURRENT_TIMESTAMP
                WHERE id=?
                """,
                (str(exc), payment["id"]),
            )

    return summary


@app.api_route("/internal/reconcile", methods=["GET", "POST"])
async def internal_reconcile(request: Request):
    # Server-to-server only: Vercel Cron sends GET with
    # `Authorization: Bearer $CRON_SECRET`; Cloud Scheduler can POST with
    # the same bearer token or an X-Internal-Token header.
    expected = os.getenv("RECONCILE_TOKEN") or os.getenv("CRON_SECRET", "")
    if not expected:
        raise HTTPException(503, "Reconciliation token not configured (set RECONCILE_TOKEN or CRON_SECRET)")
    auth = request.headers.get("authorization", "")
    supplied = auth[7:] if auth.lower().startswith("bearer ") else request.headers.get("x-internal-token", "")
    if not supplied or not secrets.compare_digest(supplied, expected):
        raise HTTPException(401, "Invalid token")
    summary = await reconcile_pending_payments(limit=50)
    return {"status": "ok", "resolved": summary["verified"], **summary}


@app.post("/coach/ops/payments/reconcile")
async def coach_ops_reconcile_payments(request: Request):
    coach = _get_coach(request)
    if not coach:
        return RedirectResponse("/coach", 302)

    summary = await reconcile_pending_payments(limit=50)
    body = f"""
<div class="container">
  <div class="card">
    <div class="card-title">Payment Reconciliation Complete</div>
    <div class="alert alert-success">Checked: {summary['checked']} | Verified: {summary['verified']} | Failed: {summary['failed']} | Skipped: {summary['skipped']}</div>
    <p class="text-muted mt-2"><a href="/coach/ops">Return to Ops</a></p>
  </div>
</div>
"""
    return HTMLResponse(_page("Payment Reconciliation", body, _nav_coach(coach)))

# ─── Student routes ───────────────────────────────────────────────────────────

@app.get("/student", response_class=HTMLResponse)
async def student_dashboard(request: Request):
    student = _get_student(request)
    if not student: return RedirectResponse("/", 302)
    student = dict(student)

    flash = request.query_params.get("payment", "")
    welcome = request.query_params.get("welcome", "")
    flash_html = ""
    if flash == "success":
        flash_html = '''<div style="background:#ecfdf5;border:1.5px solid #6ee7b7;border-radius:12px;padding:20px 24px;margin-bottom:20px;display:flex;align-items:center;gap:16px">
  <span style="font-size:2rem">✅</span>
  <div>
    <div style="font-weight:800;font-size:1.05rem;color:#065f46">Payment confirmed!</div>
    <div style="color:#047857;font-size:.9rem;margin-top:2px">All 20 days are now unlocked. <a href="/student/day/4" style="color:#065f46;font-weight:700">Start Day 4 →</a></div>
  </div>
</div>'''

    subs = query(
        "SELECT assessment_id AS day, grading_status AS status FROM submissions WHERE student_id=? AND submission_status != 'draft'",
        (student["id"],),
    )
    sub_map = {s["day"]: s["status"] for s in subs}
    title_map = {r["day"]: r["title"] for r in query("SELECT day, title FROM curriculum ORDER BY day")}

    days_html = ""
    for d in range(1, 21):
        status = sub_map.get(d)
        cur    = student["current_day"]
        paid   = student["paid_access"]
        is_free = d <= FREE_DAYS

        if d < cur:
            if status == "approved":    css, label = "graded-pass",     "✓ Passed"
            elif status == "needs_revision": css, label = "graded-revision", "⟳ Revision needed"
            else:                        css, label = "pending",         "⏳ Under review"
            href = f"/student/day/{d}"
        elif d == cur:
            if not paid and not is_free: css, label, href = "paywalled", "🔒 Unlock to continue", "/pricing"
            else:                        css, label, href = "available",  "→ Current day",        f"/student/day/{d}"
        else:
            if not paid and not is_free: css, label, href = "paywalled", "🔒 Paid access", "/pricing"
            else:                        css, label, href = "locked",    "🔒 Locked",      "#"

        days_html += f'<a href="{href}" class="day-card {css}"><div class="day-num">Day {d}</div><div class="day-title">{title_map.get(d,f"Day {d}")}</div><div class="day-status">{label}</div></a>'

    passed     = sum(1 for s in subs if s["status"] == "approved")
    pending_ct = sum(1 for s in subs if s["status"] == "pending")
    pct = int(passed / 20 * 100)

    upgrade_bar = ""
    if not student["paid_access"]:
        upgrade_bar = f'<div class="alert alert-warn flex items-center justify-between"><span>You\'re on the free trial — Days 1–{FREE_DAYS} only. Unlock all 20 days to continue.</span><a href="/pricing" class="btn btn-gold btn-sm" style="margin-left:16px;padding:6px 14px;font-size:.8rem">Unlock Now</a></div>'

    welcome_overlay = ""
    if welcome == "1":
        first_name = student['name'].split()[0]
        welcome_overlay = f"""
<div id="welcome-overlay" style="position:fixed;inset:0;background:rgba(0,0,0,.6);z-index:9999;display:flex;align-items:center;justify-content:center;padding:16px">
  <div class="card" style="max-width:480px;width:100%;padding:40px 36px;position:relative;text-align:center">
    <button onclick="document.getElementById('welcome-overlay').style.display='none'"
      style="position:absolute;top:14px;right:18px;background:none;border:none;font-size:1.4rem;cursor:pointer;color:#9ca3af">✕</button>
    <div style="font-size:2.5rem;margin-bottom:12px">🎉</div>
    <h2 style="font-size:1.4rem;font-weight:900;margin-bottom:4px">Welcome, {first_name}!</h2>
    <p class="text-muted" style="margin-bottom:28px;font-size:.95rem">Here's how your next 20 days work</p>
    <div style="text-align:left;display:flex;flex-direction:column;gap:16px;margin-bottom:32px">
      <div style="display:flex;gap:14px;align-items:flex-start">
        <span style="font-size:1.5rem;line-height:1">📚</span>
        <div><strong>Days 1–{FREE_DAYS} are FREE</strong><br><span class="text-muted text-sm">Dive straight in — no payment needed to start</span></div>
      </div>
      <div style="display:flex;gap:14px;align-items:flex-start">
        <span style="font-size:1.5rem;line-height:1">🔓</span>
        <div><strong>Days {FREE_DAYS+1}–20 unlock with one payment</strong><br><span class="text-muted text-sm">Pay once, access forever — all methods accepted</span></div>
      </div>
      <div style="display:flex;gap:14px;align-items:flex-start">
        <span style="font-size:1.5rem;line-height:1">✍️</span>
        <div><strong>Daily Missions</strong><br><span class="text-muted text-sm">Submit your work and get personal coach feedback</span></div>
      </div>
      <div style="display:flex;gap:14px;align-items:flex-start">
        <span style="font-size:1.5rem;line-height:1">🏆</span>
        <div><strong>Certificate on completion</strong><br><span class="text-muted text-sm">Complete all 20 days to earn your digital certificate</span></div>
      </div>
    </div>
    <a href="/student/day/1" onclick="document.getElementById('welcome-overlay').style.display='none'"
       class="btn btn-red btn-lg btn-full">Start Day 1 — Let's Go! →</a>
  </div>
</div>"""

    completion_banner = ""
    if student["current_day"] > 20:
        completion_banner = f'''<div style="background:linear-gradient(135deg,#1a1a1a 0%,#2d1a0e 100%);border:1.5px solid #f59e0b;border-radius:14px;padding:28px 28px;margin-bottom:20px;text-align:center">
  <div style="font-size:3rem;margin-bottom:8px">🏆</div>
  <div style="font-weight:900;font-size:1.3rem;color:#f59e0b;margin-bottom:4px">Bootcamp Complete!</div>
  <div style="color:#d1d5db;font-size:.9rem;margin-bottom:20px">Congratulations, {student['name']}. You completed all 20 days.</div>
  <a href="/student/certificate" class="btn btn-lg" style="background:#f59e0b;color:#000;font-weight:800">🎓 View &amp; Print Certificate →</a>
</div>'''

    body = f"""
{welcome_overlay}
<div class="container">
  {completion_banner}{flash_html}{upgrade_bar}
  <div class="card card-sm">
    <div class="flex items-center justify-between" style="margin-bottom:10px">
      <div><strong>Welcome back, {student['name']}!</strong>
        <span class="text-muted text-sm" style="margin-left:10px">{"All 20 days complete 🎉" if student["current_day"] > 20 else "Day " + str(student["current_day"]) + " of 20"}</span></div>
      <span class="badge badge-new">{pct}% complete</span>
    </div>
    <div class="progress-wrap"><div class="progress-fill" style="width:{pct}%"></div></div>
    <div class="flex gap-3 mt-2 text-sm text-muted">
      <span>✓ {passed} passed</span><span>⏳ {pending_ct} under review</span>
      <span>🔒 {max(0,20-student['current_day'])} locked</span>
    </div>
  </div>
  <div class="grid-days">{days_html}</div>
</div>"""
    return HTMLResponse(_page("Dashboard", body, _nav_student(student)))


@app.get("/student/certificate", response_class=HTMLResponse)
async def student_certificate(request: Request):
    student = _get_student(request)
    if not student: return RedirectResponse("/", 302)
    student = dict(student)
    if student["current_day"] <= 20:
        return RedirectResponse("/student", 302)
    completed_date = one(
        "SELECT MAX(submitted_at) as d FROM submissions WHERE student_id=? AND grading_status='approved'",
        (student["id"],)
    )
    date_str = str(completed_date["d"] or "")[:10] if completed_date else ""
    cert_body = f"""<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8"><title>Certificate — {student['name']}</title>
<style>
  @media print {{ .no-print {{ display:none }} body {{ margin:0 }} }}
  body {{ font-family: Georgia, serif; background:#fafaf7; color:#1a1a1a; margin:0; padding:40px 20px; text-align:center }}
  .cert {{ border:6px double #f59e0b; max-width:720px; margin:0 auto; padding:60px 48px; background:#fff; position:relative }}
  .logo {{ font-family: -apple-system, sans-serif; font-size:1.1rem; font-weight:900; letter-spacing:.08em; color:#e53e3e; margin-bottom:8px }}
  h1 {{ font-size:2.2rem; font-weight:400; margin:24px 0 8px }}
  .name {{ font-size:2.8rem; font-weight:700; color:#1a1a1a; border-bottom:2px solid #f59e0b; display:inline-block; padding-bottom:4px; margin:16px 0 }}
  .sub {{ font-size:1.1rem; color:#555; margin:0 0 32px }}
  .stamp {{ font-size:3.5rem; margin:16px 0 }}
  .date {{ font-size:.9rem; color:#888; margin-top:32px }}
</style>
</head>
<body>
<div class="cert">
  <div class="logo">DAKO STUDIOS</div>
  <div style="font-size:.85rem;color:#888;letter-spacing:.06em">DIGITAL SKILLS BOOTCAMP</div>
  <h1>Certificate of Completion</h1>
  <p style="color:#555;font-size:1rem">This certifies that</p>
  <div class="name">{student['name']}</div>
  <p class="sub">has successfully completed all 20 days of the<br><strong>Dako Studios Digital Skills Bootcamp</strong></p>
  <div class="stamp">🏆</div>
  <p style="color:#555;font-size:.9rem">Demonstrating proficiency in computer fundamentals, internet skills,<br>digital productivity, cybersecurity, and AI tools.</p>
  {"<div class='date'>Completed: " + date_str + "</div>" if date_str else ""}
</div>
<div class="no-print" style="text-align:center;margin-top:24px">
  <button onclick="window.print()" style="background:#f59e0b;color:#000;font-weight:700;padding:12px 28px;border:none;border-radius:8px;cursor:pointer;font-size:1rem">🖨️ Print / Save as PDF</button>
  <a href="/student" style="margin-left:16px;color:#555;font-size:.9rem">← Back to dashboard</a>
</div>
</body></html>"""
    return HTMLResponse(cert_body)


@app.get("/student/day/{day_num}", response_class=HTMLResponse)
async def student_day(day_num: int, request: Request):
    student = _get_student(request)
    if not student: return RedirectResponse("/", 302)
    student = dict(student)

    if day_num > student["current_day"]:
        return RedirectResponse("/student", 302)

    # Freemium gate
    if _requires_payment(day_num, student):
        return HTMLResponse(_page("Unlock Full Bootcamp", _paywall_page(student, day_num), _nav_student(student)))

    curr = one("SELECT * FROM curriculum WHERE day=?", (day_num,))
    if not curr: raise HTTPException(404, "Day not found")

    mission = json.loads(curr["mission_data"])
    # The old "day" column is now mapped to "assessment_id"
    subs = query("""
        SELECT s.*, a.answer_text, f.stored_path as screenshot_url 
        FROM submissions s 
        LEFT JOIN submission_answers a ON a.submission_id = s.id 
        LEFT JOIN submission_files f ON f.submission_id = s.id 
        WHERE s.student_id=? AND s.assessment_id=? AND s.submission_status != 'draft'
        ORDER BY s.submitted_at DESC
    """, (student["id"], day_num))

    # ── Lesson tab ────────────────────────────────────────────────────────────
    if curr["lesson_status"] == "published" and curr["lesson_html"]:
        video_html = ""
        if curr["video_url"]:
            embed = curr["video_url"]
            # convert watch URL to embed URL if needed
            if "watch?v=" in embed:
                embed = embed.replace("watch?v=", "embed/").split("&")[0]
            video_html = f'<div class="video-wrap"><iframe src="{embed}" allowfullscreen loading="lazy"></iframe></div>'
        lesson_tab_content = f"""<div class="card">
  {video_html}
  <div class="lesson-content">{curr['lesson_html']}</div>
</div>"""
    else:
        lesson_tab_content = '<div class="card"><div class="alert alert-info">Lesson content is being prepared by your coach. Come back soon — or jump straight to the mission!</div></div>'

    # ── Mission tab ───────────────────────────────────────────────────────────
    subs_html = ""
    for s in subs:
        st = s["grading_status"]
        badge_cls = {"approved":"badge-approved","revision_requested":"badge-revision"}.get(st,"badge-pending")
        label = {"approved":"Passed","revision_requested":"Needs Revision","pending":"Pending Review"}.get(st, st.capitalize())
        shots = f'<a href="/uploads/{s["screenshot_url"]}" target="_blank"><img class="img-thumb" src="/uploads/{s["screenshot_url"]}" alt="Screenshot"></a>' if s["screenshot_url"] else ""
        coach_fb = f'<div class="mt-2 text-sm" style="background:#f0f9ff;border-left:3px solid #0ea5e9;padding:10px;border-radius:0 6px 6px 0"><strong>Coach feedback:</strong> {s["feedback_summary"]}</div>' if s["feedback_summary"] else ""

        ai_row = one("""SELECT af.generated_feedback, af.strengths_summary, af.weaknesses_summary, af.improvement_suggestions
            FROM ai_feedback af
            JOIN grading_results gr ON gr.id = af.grading_result_id
            WHERE gr.submission_id=? AND af.feedback_status='visible'
            ORDER BY af.generated_at DESC LIMIT 1""", (s["id"],))
        if ai_row and ai_row["generated_feedback"]:
            strengths = f'<div><span style="color:#16a34a;font-weight:600">Strengths:</span> {ai_row["strengths_summary"]}</div>' if ai_row["strengths_summary"] else ""
            improve = f'<div style="margin-top:4px"><span style="color:#d97706;font-weight:600">To improve:</span> {ai_row["improvement_suggestions"]}</div>' if ai_row["improvement_suggestions"] else ""
            ai_fb = f'''<div class="mt-2" style="background:#faf5ff;border-left:3px solid #9333ea;padding:10px;border-radius:0 6px 6px 0;font-size:.85rem">
  <div style="font-weight:700;color:#7e22ce;margin-bottom:6px">✨ AI Feedback</div>
  <div>{ai_row["generated_feedback"]}</div>
  {strengths}{improve}
</div>'''
        else:
            ai_fb = ""

        subs_html += f"""<div class="sub-card {st}">
  <div class="flex items-center justify-between" style="margin-bottom:8px">
    <span class="badge {badge_cls}">{label}</span>
    <span class="text-xs text-muted">{str(s['submitted_at'])[:16]}</span>
  </div>
  <div class="text-sm" style="white-space:pre-wrap;margin-bottom:8px">{s['answer_text'] or '<em style="color:#9ca3af">No written answer submitted.</em>'}</div>
  {f'<div class="flex gap-2">{shots}</div>' if shots else ""}{coach_fb}{ai_fb}
</div>"""

    latest_status = dict(subs[0])["grading_status"] if subs else None
    expected = mission.get("expected_outcome", "")
    expected_html = f'<hr class="divider"><div style="background:#f0fdf4;border-radius:8px;padding:14px"><div class="text-xs" style="color:#166534;font-weight:700;margin-bottom:4px">EXPECTED OUTCOME</div><div class="text-sm">{expected}</div></div>' if expected else ""

    if latest_status != "approved":
        revision_note = '<div class="alert alert-info">Address the coach feedback below, then resubmit.</div>' if latest_status == "revision_requested" else ""
        submit_form = f"""<div class="card" style="position:sticky;top:80px">
  <div class="card-title">Submit Your Work</div>{revision_note}
  <form method="POST" action="/student/day/{day_num}/submit" enctype="multipart/form-data">
    <div class="form-group"><label class="form-label">Your Written Answer</label>
      <textarea name="answer" required placeholder="Describe what you did, what you learned, and any challenges..."></textarea></div>
    <div class="form-group"><label class="form-label">Screenshot Evidence</label>
      <input type="file" name="screenshot" accept="image/*" style="padding:8px;border:1.5px solid #d1d5db;border-radius:8px;width:100%">
      <div class="text-xs text-muted mt-2">Upload one screenshot showing your completed work</div></div>
    <button class="btn btn-red btn-full">Submit for Review</button>
  </form>
</div>"""
    else:
        nxt = f'<a href="/student/day/{day_num+1}" class="btn btn-green btn-full" style="margin-top:12px">Day {day_num+1} →</a>' if day_num < 20 else '<div class="mt-3 text-sm" style="color:#166534;font-weight:600">You have completed the bootcamp! 🎉</div>'
        submit_form = f'<div class="card" style="position:sticky;top:80px"><div class="alert alert-success" style="margin-bottom:0">✓ Day {day_num} passed!</div>{nxt}</div>'

    mission_tab_content = f"""<div class="grid-2">
  <div>
    <div class="card">
      <div class="card-title">Mission: {mission.get('title','')}</div>
      <div class="text-sm" style="white-space:pre-wrap;line-height:1.85">{mission.get('instructions','')}</div>
      {expected_html}
    </div>
    {f'<div class="card"><div class="card-title">Submission History</div>{subs_html}</div>' if subs_html else ""}
  </div>
  <div>{submit_form}</div>
</div>"""

    body = f"""<div class="container">
  <div class="card">
    <div class="flex items-center gap-2" style="margin-bottom:12px">
      <span class="badge badge-new">Day {day_num}</span>
      <a href="/student" class="text-sm text-muted">← All Days</a>
    </div>
    <h1 style="font-size:1.5rem;font-weight:800;margin-bottom:8px">{curr['title']}</h1>
    <p class="text-muted">{curr['goal']}</p>
  </div>
  <div class="tab-nav" style="background:#fff;border-radius:12px 12px 0 0;padding:0 20px;margin-bottom:0;box-shadow:0 1px 3px rgba(0,0,0,.07)">
    <button class="tab-btn active" onclick="showTab(this,'tab-lesson')">Lesson</button>
    <button class="tab-btn" onclick="showTab(this,'tab-mission')">Mission & Submit</button>
  </div>
  <div id="tab-lesson" class="tab-panel active" style="padding-top:4px">{lesson_tab_content}</div>
  <div id="tab-mission" class="tab-panel" style="padding-top:4px">{mission_tab_content}</div>
</div>"""

    return HTMLResponse(_page(f"Day {day_num}", body, _nav_student(student)))


def _paywall_page(student, day_num):
    currency, price = _student_currency_price(student)
    sym = _currency_symbol(currency)
    days_left = 20 - day_num + 1
    return f"""<div class="container" style="max-width:560px;padding-top:32px">
  <div class="card" style="padding:40px 36px;text-align:center">
    <div style="font-size:3rem;margin-bottom:16px">🔓</div>
    <h2 style="font-size:1.5rem;font-weight:800;margin-bottom:8px">Unlock Day {day_num}–20</h2>
    <p style="color:#6b7280;margin-bottom:6px">You've completed the free trial (Days 1–{FREE_DAYS}). Nice work!</p>
    <p style="color:#6b7280;margin-bottom:24px">Unlock once to access the remaining <strong>{days_left} days</strong> — including missions, coach feedback, and your certificate.</p>
    <div style="background:#f9fafb;border-radius:12px;padding:20px;margin-bottom:20px">
      <div style="font-size:2.5rem;font-weight:900;color:#1a1a1a">{sym}{int(price):,}</div>
      <div style="color:#9ca3af;font-size:.85rem;margin-top:4px">One-time payment · Lifetime access</div>
    </div>
    <ul style="text-align:left;list-style:none;padding:0;margin:0 0 24px;display:flex;flex-direction:column;gap:10px">
      <li style="display:flex;gap:10px;align-items:center"><span style="color:#16a34a;font-weight:700">✓</span> {20 - FREE_DAYS} days of lessons &amp; hands-on missions</li>
      <li style="display:flex;gap:10px;align-items:center"><span style="color:#16a34a;font-weight:700">✓</span> Personal coach feedback on every submission</li>
      <li style="display:flex;gap:10px;align-items:center"><span style="color:#16a34a;font-weight:700">✓</span> Pay by card, bank transfer, M-Pesa, MTN MoMo, Verve</li>
      <li style="display:flex;gap:10px;align-items:center"><span style="color:#16a34a;font-weight:700">✓</span> Digital certificate on completion</li>
    </ul>
    <a href="/payment/checkout" class="btn btn-red btn-lg btn-full">Unlock All 20 Days — {sym}{int(price):,} →</a>
    <div style="margin-top:16px"><a href="/student" style="color:#9ca3af;font-size:.85rem">← Back to dashboard</a></div>
  </div>
</div>"""


from submission_pipeline import process_safe_upload, finalize_submission
from assessment_session import start_attempt, autosave_attempt, finalize_attempt
from exam_engine import materialize_exam_attempt, get_exam_state
from assessment_logger import log_assessment_event

@app.post("/student/api/autosave")
async def autosave_api(request: Request, day_num: int = Form(...), answer: str = Form(...)):
    student = _get_student(request)
    if not student: raise HTTPException(401, "Not logged in")
    
    att = start_attempt(student["id"], day_num)
    autosave_attempt(att["id"], answer_text=answer)
    return {"status": "success"}

@app.post("/student/day/{day_num}/submit")
async def submit_day(day_num: int, request: Request, answer: str = Form(...), screenshot: UploadFile = File(None)):
    student = _get_student(request)
    if not student: return RedirectResponse("/", 302)
    student = dict(student)
    if day_num > student["current_day"]: raise HTTPException(403, "Day not yet unlocked")
    if _requires_payment(day_num, student): raise HTTPException(402, "Payment required")

    assessment = one("SELECT id FROM assessments WHERE id=?", (day_num,))
    if not assessment:
        run("INSERT INTO assessments (id, title, description, assessment_type) VALUES (?, ?, ?, ?)", (day_num, f"Day {day_num}", "Auto-generated", "assignment"))
        
    try:
        att = start_attempt(student["id"], day_num)
        attempt_id = att["id"]
        sub_id = att["submission_id"]
        
        autosave_attempt(attempt_id, answer_text=answer)
        
        if screenshot and screenshot.filename:
            await process_safe_upload(sub_id, screenshot)
            
        finalize_attempt(attempt_id)
        finalize_submission(sub_id)
        from email_service import send_submission_received, send_coach_new_submission
        send_submission_received(student["name"], student["email"], day_num, lang=student.get("preferred_lang") or "en")
        send_coach_new_submission(student["name"], day_num, sub_id)

    except HTTPException as e:
        raise e

    return RedirectResponse(f"/student/day/{day_num}", 302)

# ─── Student: Account ─────────────────────────────────────────────────────────

def _student_account_page(student, submissions=None, payments=None, error="", saved=False):
    alert = ""
    if saved:
        alert = '<div class="alert alert-success">Settings saved.</div>'
    elif error:
        alert = f'<div class="alert alert-error">{error}</div>'

    access = 'Full Access <span style="color:#16a34a">✓</span>' if student["paid_access"] else 'Free Trial (Days 1–3)'

    # Submission history
    sub_html = ""
    if submissions is not None:
        total = len(submissions)
        passed = sum(1 for s in submissions if s["grading_status"] == "approved")
        revision = sum(1 for s in submissions if s["grading_status"] == "revision_requested")
        rows = "".join(
            f'<tr><td>Day {s["assessment_id"]}</td><td>{str(s["submitted_at"])[:10]}</td>'
            f'<td><span class="badge badge-{"approved" if s["grading_status"]=="approved" else "revision-requested" if s["grading_status"]=="revision_requested" else "pending"}">'
            f'{"Passed" if s["grading_status"]=="approved" else "Needs Revision" if s["grading_status"]=="revision_requested" else "Pending"}</span></td></tr>'
            for s in submissions[:20]
        )
        sub_html = f"""<hr class="divider"><h4>Submission History</h4>
<div class="grid-stats mt-3" style="grid-template-columns:repeat(3,1fr)">
  <div class="stat"><div class="stat-num">{total}</div><div class="stat-label">Submitted</div></div>
  <div class="stat"><div class="stat-num" style="color:#16a34a">{passed}</div><div class="stat-label">Passed</div></div>
  <div class="stat"><div class="stat-num" style="color:#d97706">{revision}</div><div class="stat-label">Revision</div></div>
</div>
<table class="table mt-3"><thead><tr><th>Day</th><th>Date</th><th>Status</th></tr></thead>
<tbody>{"".join([rows]) if rows else "<tr><td colspan='3' class='text-muted'>No submissions yet.</td></tr>"}</tbody></table>"""

    # Payment history
    pay_html = ""
    if payments:
        rows = "".join(
            f'<tr><td>{str(p["created_at"])[:10]}</td><td>${p["amount"]} {p["currency"]}</td>'
            f'<td><span class="badge badge-{"approved" if p["status"]=="success" else "pending" if p["status"]=="pending" else "revision"}">{p["status"].title()}</span></td></tr>'
            for p in payments
        )
        pay_html = f"""<hr class="divider"><h4>Payment History</h4>
<table class="table mt-3"><thead><tr><th>Date</th><th>Amount</th><th>Status</th></tr></thead>
<tbody>{rows}</tbody></table>"""

    return f"""<div class="container"><div class="card" style="max-width:560px;margin:0 auto">
  <div class="card-title">Account Settings</div>
  <hr class="divider">
  <div style="display:grid;grid-template-columns:1fr 1fr;gap:8px 24px;margin-bottom:20px;font-size:.9rem">
    <span class="text-muted">Email</span><span>{student['email']}</span>
    <span class="text-muted">Member since</span><span>{str(student['created_at'])[:10]}</span>
    <span class="text-muted">Progress</span><span>Day {student['current_day']} / 20</span>
    <span class="text-muted">Access</span><span>{access}</span>
  </div>
  <hr class="divider">
  {alert}
  <form method="POST" action="/student/account">
    <div class="form-group">
      <label class="form-label">Display Name</label>
      <input type="text" name="name" value="{student['name']}" required>
    </div>
    <hr class="divider">
    <p class="text-muted" style="font-size:.85rem;margin-bottom:12px">Leave password fields blank to keep your current password.</p>
    <div class="form-group">
      <label class="form-label">Current Password</label>
      <input type="password" name="current_password" autocomplete="current-password">
    </div>
    <div class="form-group">
      <label class="form-label">New Password</label>
      <input type="password" name="new_password" autocomplete="new-password">
    </div>
    <div class="form-group">
      <label class="form-label">Confirm New Password</label>
      <input type="password" name="confirm_password" autocomplete="new-password">
    </div>
    <button class="btn btn-red">Save Changes</button>
  </form>
  {sub_html}
  {pay_html}
</div></div>"""

@app.get("/student/account", response_class=HTMLResponse)
async def student_account_get(request: Request):
    student = _get_student(request)
    if not student: return RedirectResponse("/", 302)
    student = dict(student)
    saved = request.query_params.get("saved", "")
    submissions = query(
        "SELECT assessment_id, grading_status, submitted_at FROM submissions "
        "WHERE student_id=? AND submission_status != 'draft' ORDER BY submitted_at DESC",
        (student["id"],)
    )
    payments = query(
        "SELECT created_at, amount, currency, status FROM payments WHERE student_id=? ORDER BY created_at DESC",
        (student["id"],)
    )
    return HTMLResponse(_page("Account Settings",
        _student_account_page(student, submissions=submissions, payments=payments or None, saved=bool(saved)),
        _nav_student(student)))

@app.post("/student/account")
async def student_account_post(request: Request,
                                name: str = Form(...),
                                current_password: str = Form(""),
                                new_password: str = Form(""),
                                confirm_password: str = Form("")):
    student = _get_student(request)
    if not student: return RedirectResponse("/", 302)
    student = dict(student)

    def err(msg):
        submissions = query(
            "SELECT assessment_id, grading_status, submitted_at FROM submissions "
            "WHERE student_id=? AND submission_status != 'draft' ORDER BY submitted_at DESC",
            (student["id"],)
        )
        payments = query(
            "SELECT created_at, amount, currency, status FROM payments WHERE student_id=? ORDER BY created_at DESC",
            (student["id"],)
        )
        return HTMLResponse(_page("Account Settings",
            _student_account_page(student, submissions=submissions, payments=payments or None, error=msg),
            _nav_student(student)))

    name = name.strip()
    if not name:
        return err("Display name cannot be blank.")

    new_hash = student["password_hash"]
    if current_password or new_password or confirm_password:
        if not current_password:
            return err("Enter your current password to change it.")
        if _hash(current_password) != student["password_hash"]:
            return err("Current password is incorrect.")
        if len(new_password) < 6:
            return err("New password must be at least 6 characters.")
        if new_password != confirm_password:
            return err("New passwords do not match.")
        if _hash(new_password) == student["password_hash"]:
            return err("New password must be different from your current password.")
        new_hash = _hash(new_password)

    run("UPDATE students SET name=?, password_hash=? WHERE id=?", (name, new_hash, student["id"]))
    return RedirectResponse("/student/account?saved=1", 302)

# ─── Exams ────────────────────────────────────────────────────────────────────

@app.post("/student/exam/{exam_id}/start")
async def start_exam(exam_id: int, request: Request):
    student = _get_student(request)
    if not student: return RedirectResponse("/", 302)
    
    exam = one("SELECT assessment_id, duration_minutes FROM exams WHERE id=?", (exam_id,))
    if not exam: raise HTTPException(404, "Exam not found")
    
    try:
        # Start authoritative session
        att = start_attempt(student["id"], exam["assessment_id"], time_limit_sec=exam["duration_minutes"] * 60)
        attempt_id = att["id"]
        
        # Materialize layout
        materialize_exam_attempt(attempt_id, exam_id)
        
        log_assessment_event("exam_started", att["submission_id"], exam["assessment_id"], 1)
        
        return RedirectResponse(f"/student/exam/{attempt_id}", 302)
    except HTTPException as e:
        raise e

@app.get("/student/exam/{attempt_id}")
async def view_exam(attempt_id: int, request: Request):
    student = _get_student(request)
    if not student: return RedirectResponse("/", 302)

    state = get_exam_state(attempt_id)
    if state["session_status"] != "active":
        ended_body = f"""<div class="container" style="max-width:520px;padding-top:60px;text-align:center">
  <div class="card" style="padding:40px 32px">
    <div style="font-size:2.5rem;margin-bottom:12px">⏱️</div>
    <h2 style="font-size:1.3rem;font-weight:800;margin-bottom:8px">Exam session {state['session_status']}</h2>
    <p class="text-muted" style="margin-bottom:24px">This exam attempt is no longer active.</p>
    <a href="/student" class="btn btn-red">Back to Dashboard</a>
  </div>
</div>"""
        return HTMLResponse(_page("Exam Ended", ended_body))

    questions = state.get("questions", [])
    q_html = ""
    for i, q in enumerate(questions):
        choices_html = ""
        for c in q.get("choices", []):
            choices_html += f"""<label style="display:flex;align-items:center;gap:10px;padding:10px 14px;border:1.5px solid #e5e7eb;border-radius:8px;cursor:pointer;margin-bottom:8px">
  <input type="radio" name="q_{q['question_key']}" value="{c['choice_key']}"
    onchange="autoSave('{q['question_key']}', this.value)" style="width:16px;height:16px;accent-color:#e53e3e">
  <span>{c['choice_text']}</span>
</label>"""
        if not choices_html:
            choices_html = f"""<textarea name="q_{q['question_key']}" rows="3"
  onblur="autoSave('{q['question_key']}', this.value)"
  placeholder="Your answer…"
  style="width:100%;padding:10px 14px;border:1.5px solid #d1d5db;border-radius:8px;font-size:.9rem;resize:vertical"></textarea>"""
        q_html += f"""<div class="card" style="padding:24px;margin-bottom:16px">
  <div style="font-size:.78rem;font-weight:700;color:#9ca3af;margin-bottom:8px">QUESTION {i+1}</div>
  <p style="font-weight:600;margin-bottom:16px">{q['question_text']}</p>
  {choices_html}
</div>"""

    expires = state.get("expires_at", "")
    body = f"""<div class="container" style="max-width:680px;padding-top:32px">
  <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:24px">
    <h1 style="font-size:1.4rem;font-weight:800;margin:0">Exam</h1>
    <div id="timer" style="font-size:1rem;font-weight:700;color:#e53e3e;background:#fff1f2;padding:6px 16px;border-radius:20px">⏱ Loading…</div>
  </div>
  <form id="exam-form" method="POST" action="/student/exam/{attempt_id}/submit">
    {q_html}
    <div style="text-align:center;padding:24px 0">
      <button type="submit" class="btn btn-red btn-lg" onclick="return confirm('Submit your exam? You cannot change answers after submitting.')">Submit Exam →</button>
    </div>
  </form>
</div>
<script>
var expires = new Date("{expires}").getTime();
function tick(){{
  var now=Date.now(), diff=Math.max(0, Math.round((expires-now)/1000));
  var m=Math.floor(diff/60), s=diff%60;
  document.getElementById('timer').textContent='⏱ '+m+':'+(s<10?'0':'')+s;
  if(diff<=0){{document.getElementById('exam-form').submit();return;}}
  setTimeout(tick,1000);
}}
if(expires)tick();
function autoSave(key, val){{
  fetch('/student/exam/api/save',{{method:'POST',headers:{{'Content-Type':'application/x-www-form-urlencoded'}},
    body:'attempt_id={attempt_id}&question_key='+encodeURIComponent(key)+'&answer='+encodeURIComponent(val)}});
}}
</script>"""
    return HTMLResponse(_page("Exam", body))

@app.post("/student/exam/api/save")
async def save_exam_answer(request: Request, attempt_id: int = Form(...), question_key: str = Form(...), answer: str = Form(...)):
    student = _get_student(request)
    if not student: raise HTTPException(401, "Not logged in")
    
    try:
        autosave_attempt(attempt_id, question_key=question_key, answer_text=answer)
        # Log event
        att = one("SELECT submission_id, assessment_id FROM assessment_attempts WHERE id=?", (attempt_id,))
        if att:
            log_assessment_event("exam_answer_saved", att["submission_id"], att["assessment_id"], 1)
        return {"status": "success"}
    except HTTPException as e:
        raise e

@app.post("/student/exam/{attempt_id}/submit")
async def submit_exam(attempt_id: int, request: Request):
    student = _get_student(request)
    if not student: raise HTTPException(401, "Not logged in")

    att = one("SELECT submission_id, assessment_id FROM assessment_attempts WHERE id=?", (attempt_id,))
    if not att:
        raise HTTPException(404, "Exam attempt not found")

    try:
        finalize_attempt(attempt_id)
        finalize_submission(att["submission_id"])
        log_assessment_event("exam_submitted", att["submission_id"], att["assessment_id"], 1)
    except HTTPException as e:
        raise e

    return RedirectResponse("/student", 302)

# ─── Coach: Auth ──────────────────────────────────────────────────────────────

@app.get("/coach", response_class=HTMLResponse)
async def coach_home(request: Request):
    if _get_coach(request): return RedirectResponse("/coach/dashboard", 302)
    return HTMLResponse(_page("Coach Login", _coach_login_page()))

def _coach_login_page(error=""):
    err = f'<div class="alert alert-error">{error}</div>' if error else ""
    return f"""<div class="login-wrap"><div class="login-box">
  <div class="login-logo"><div class="logo-text">DAKO <span class="r">STUDIOS</span></div><div class="tagline">Coach Portal</div></div>
  {err}
  <form method="POST" action="/coach/login">
    <div class="form-group"><label class="form-label">Username</label><input type="text" name="username" required autocomplete="username"></div>
    <div class="form-group"><label class="form-label">Password</label><input type="password" name="password" required></div>
    <button class="btn btn-red btn-full">Coach Login</button>
  </form>
</div></div>"""

@app.post("/coach/login")
async def coach_login(username: str = Form(...), password: str = Form(...)):
    coach = one("SELECT * FROM coaches WHERE username=?", (username,))
    if not coach or coach["password_hash"] != _hash(password):
        return HTMLResponse(_page("Coach Login", _coach_login_page("Invalid credentials")))
    tok = _token()
    run("INSERT INTO coach_sessions (token, coach_id) VALUES (?,?)", (tok, coach["id"]))
    resp = RedirectResponse("/coach/dashboard", 302)
    resp.set_cookie("c_token", tok, httponly=True, max_age=86400 * 7, secure=True, samesite="lax")
    return resp

@app.get("/coach/logout")
async def coach_logout():
    resp = RedirectResponse("/coach", 302)
    resp.delete_cookie("c_token")
    return resp

# ─── Coach: Account ───────────────────────────────────────────────────────────

def _coach_account_page(coach, error="", saved=False):
    alert = ""
    if saved:
        alert = '<div class="alert alert-success">Settings saved.</div>'
    elif error:
        alert = f'<div class="alert alert-error">{error}</div>'
    return f"""<div class="container"><div class="card" style="max-width:560px;margin:0 auto">
  <div class="card-title">Account Settings</div>
  <hr class="divider">
  <div style="display:grid;grid-template-columns:1fr 1fr;gap:8px 24px;margin-bottom:20px;font-size:.9rem">
    <span class="text-muted">Username</span><span>{coach['username']}</span>
    <span class="text-muted">Member since</span><span>{str(coach['created_at'])[:10]}</span>
  </div>
  <hr class="divider">
  {alert}
  <form method="POST" action="/coach/account">
    <div class="form-group">
      <label class="form-label">Display Name</label>
      <input type="text" name="name" value="{coach['name']}" required>
    </div>
    <hr class="divider">
    <p class="text-muted" style="font-size:.85rem;margin-bottom:12px">Leave password fields blank to keep your current password.</p>
    <div class="form-group">
      <label class="form-label">Current Password</label>
      <input type="password" name="current_password" autocomplete="current-password">
    </div>
    <div class="form-group">
      <label class="form-label">New Password</label>
      <input type="password" name="new_password" autocomplete="new-password">
    </div>
    <div class="form-group">
      <label class="form-label">Confirm New Password</label>
      <input type="password" name="confirm_password" autocomplete="new-password">
    </div>
    <button class="btn btn-red">Save Changes</button>
  </form>
</div></div>"""

@app.get("/coach/account", response_class=HTMLResponse)
async def coach_account_get(request: Request):
    coach = _get_coach(request)
    if not coach: return RedirectResponse("/coach", 302)
    coach = dict(coach)
    saved = request.query_params.get("saved", "")
    return HTMLResponse(_page("Account Settings", _coach_account_page(coach, saved=bool(saved)), _nav_coach(coach)))

@app.post("/coach/account")
async def coach_account_post(request: Request,
                              name: str = Form(...),
                              current_password: str = Form(""),
                              new_password: str = Form(""),
                              confirm_password: str = Form("")):
    coach = _get_coach(request)
    if not coach: return RedirectResponse("/coach", 302)
    coach = dict(coach)

    def err(msg):
        return HTMLResponse(_page("Account Settings", _coach_account_page(coach, error=msg), _nav_coach(coach)))

    name = name.strip()
    if not name:
        return err("Display name cannot be blank.")

    new_hash = coach["password_hash"]
    if current_password or new_password or confirm_password:
        if not current_password:
            return err("Enter your current password to change it.")
        if _hash(current_password) != coach["password_hash"]:
            return err("Current password is incorrect.")
        if len(new_password) < 6:
            return err("New password must be at least 6 characters.")
        if new_password != confirm_password:
            return err("New passwords do not match.")
        if _hash(new_password) == coach["password_hash"]:
            return err("New password must be different from your current password.")
        new_hash = _hash(new_password)

    run("UPDATE coaches SET name=?, password_hash=? WHERE id=?", (name, new_hash, coach["id"]))
    return RedirectResponse("/coach/account?saved=1", 302)

# ─── Coach: Dashboard (grading) ───────────────────────────────────────────────

@app.get("/coach/dashboard", response_class=HTMLResponse)
async def coach_dashboard(request: Request):
    coach = _get_coach(request)
    if not coach: return RedirectResponse("/coach", 302)
    coach = dict(coach)

    pending = query("""SELECT su.*, su.assessment_id AS day, st.name as sname, st.email as semail, a.answer_text, f.stored_path as screenshot_url
        FROM submissions su 
        JOIN students st ON su.student_id=st.id
        LEFT JOIN submission_answers a ON a.submission_id = su.id
        LEFT JOIN submission_files f ON f.submission_id = su.id
        WHERE su.submission_status='submitted' AND su.grading_status='pending'
        ORDER BY su.submitted_at ASC""")

    total_students = one("SELECT COUNT(*) as c FROM students")["c"]
    total_graded   = one("SELECT COUNT(*) as c FROM submissions WHERE grading_status!='pending'")["c"]
    total_passed   = one("SELECT COUNT(*) as c FROM submissions WHERE grading_status='approved'")["c"]
    total_revenue  = one("SELECT COALESCE(SUM(amount),0) as t FROM payments WHERE status='success'")["t"]

    rows_html = ""
    for p in pending:
        shots = f'<a href="/uploads/{p["screenshot_url"]}" target="_blank"><img class="img-thumb" src="/uploads/{p["screenshot_url"]}" alt="Screenshot"></a>' if p["screenshot_url"] else ""
        curr = one("SELECT title FROM curriculum WHERE day=?", (p["assessment_id"],))
        rows_html += f"""<div class="card card-sm">
  <div class="flex items-center justify-between" style="margin-bottom:12px">
    <div><strong>{p['sname']}</strong><span class="text-muted text-sm" style="margin-left:8px">{p['semail']}</span>
      <span class="badge badge-new" style="margin-left:8px">Day {p['day']}: {curr['title'] if curr else f"Day {p['assessment_id']}"}</span></div>
    <span class="text-xs text-muted">{str(p['submitted_at'])[:16]}</span>
  </div>
  <div style="background:#f9fafb;border-radius:8px;padding:14px;margin-bottom:12px">
    <div class="text-xs text-muted" style="font-weight:700;margin-bottom:6px">STUDENT ANSWER</div>
    <div class="text-sm" style="white-space:pre-wrap">{p['answer_text']}</div>
  </div>
  {f'<div class="flex gap-2" style="margin-bottom:12px">{shots}</div>' if shots else ""}
  <form method="POST" action="/coach/grade/{p['id']}">
    <div style="margin-bottom:10px">
      <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:6px">
        <label style="font-size:.75rem;font-weight:700;color:#6b7280;text-transform:uppercase;letter-spacing:.04em">Feedback to Student</label>
        <button type="button" onclick="aiDraft({p['id']}, this)" style="font-size:.75rem;padding:4px 10px;border-radius:6px;border:1px solid #e5e7eb;background:#f9fafb;cursor:pointer;color:#374151">✨ AI Draft</button>
      </div>
      <textarea id="fb-{p['id']}" name="feedback" placeholder="Write feedback for the student, or click ✨ AI Draft to generate a suggestion..." style="width:100%;min-height:100px;font-size:.875rem;padding:10px;border:1px solid #e5e7eb;border-radius:8px;resize:vertical;box-sizing:border-box"></textarea>
    </div>
    <div style="display:flex;gap:8px;justify-content:flex-end">
      <button name="verdict" value="revision_requested" class="btn btn-orange">⟳ Needs Revision</button>
      <button name="verdict" value="approved" class="btn btn-green">✓ Pass</button>
    </div>
  </form>
</div>"""

    if not rows_html:
        rows_html = '<div class="card" style="text-align:center;padding:48px;color:#6b7280"><div style="font-size:2.5rem;margin-bottom:12px">🎉</div><div style="font-weight:600;font-size:1.1rem">All caught up!</div><div class="text-sm mt-2">No pending submissions.</div></div>'

    body = f"""<div class="container">
  <div class="grid-stats">
    <div class="stat"><div class="stat-num">{total_students}</div><div class="stat-label">Students</div></div>
    <div class="stat"><div class="stat-num">{len(pending)}</div><div class="stat-label">Pending Review</div></div>
    <div class="stat"><div class="stat-num">{total_passed}</div><div class="stat-label">Passed</div></div>
    <div class="stat"><div class="stat-num">${total_revenue:.0f}</div><div class="stat-label">Revenue</div></div>
  </div>
  <div class="card-title" style="margin-bottom:16px">Pending Submissions ({len(pending)})</div>
  {rows_html}
</div>"""
    return HTMLResponse(_page("Coach Dashboard", body, _nav_coach(coach)))


@app.get("/coach/grade/{sub_id}/ai-suggest")
async def coach_ai_suggest(sub_id: int, request: Request):
    from fastapi.responses import JSONResponse
    from ai_feedback_engine import call_gemini_text, _api_key_sequence
    coach = _get_coach(request)
    if not coach:
        return JSONResponse({"error": "Not authorised"}, status_code=401)

    if not _api_key_sequence():
        return JSONResponse({"error": "No Gemini API keys configured in Vercel env vars."})

    sub = one("""SELECT su.*, a.answer_text, c.title as day_title, st.name as student_name
                 FROM submissions su
                 JOIN students st ON su.student_id = st.id
                 LEFT JOIN submission_answers a ON a.submission_id = su.id
                 LEFT JOIN curriculum c ON c.day = su.assessment_id
                 WHERE su.id=?""", (sub_id,))
    if not sub:
        return JSONResponse({"error": "Submission not found"}, status_code=404)

    prompt = (
        f"You are a bootcamp coach writing concise, encouraging feedback for a student.\n"
        f"Student: {sub['student_name']}\n"
        f"Day {sub['assessment_id']}: {sub['day_title'] or ''}\n\n"
        f"Student's answer:\n{sub['answer_text'] or '(no text answer)'}\n\n"
        f"Write 2-3 sentences of actionable feedback. Be specific, warm, and direct. "
        f"No preamble — just the feedback text itself."
    )

    try:
        text = call_gemini_text(prompt, max_tokens=256)
        return JSONResponse({"feedback": text})
    except Exception as exc:
        return JSONResponse({"error": f"AI generation failed: {exc}"})


@app.post("/coach/grade/{sub_id}")
async def coach_grade(sub_id: int, request: Request, verdict: str = Form(...), feedback: str = Form("")):
    coach = _get_coach(request)
    if not coach: return RedirectResponse("/coach", 302)
    coach = dict(coach)
    sub = one("SELECT * FROM submissions WHERE id=?", (sub_id,))
    if not sub: raise HTTPException(404)

    # Gradual migration: check if async grading is enabled (defaulting to True)
    async_grading = os.getenv("ASYNC_GRADING", "true").lower() in ("true", "1", "yes")

    if async_grading:
        # Check if a grading job is already active or completed to prevent duplicates
        existing = one("SELECT status FROM assessment_jobs WHERE submission_id=? AND status IN ('pending', 'running', 'completed')", (sub_id,))
        if not existing:
            from assessment_queue import enqueue_grading_job
            enqueue_grading_job(sub_id, verdict, feedback.strip(), coach["name"])
    else:
        # Legacy synchronous grading flow
        run("UPDATE submissions SET grading_status=?,feedback_summary=? WHERE id=?",
            (verdict, feedback, sub_id))
        
        # Log the grading event natively
        from assessment_logger import log_assessment_event
        # In legacy mode we fake an evaluation
        log_assessment_event("assessment_completed", sub_id, rubric_id=sub["assessment_id"], rubric_version=1, total_score=100.0 if verdict=='approved' else 0.0, passed=(verdict=='approved'), reviewer=coach["name"])
            
        st = one("SELECT * FROM students WHERE id=?", (sub["student_id"],))
        if verdict == "approved" and st:
            if st["current_day"] == sub["assessment_id"] and st["current_day"] < 20:
                run("UPDATE students SET current_day=current_day+1 WHERE id=?", (st["id"],))
            _lang = st.get("preferred_lang") or "en"
            from email_service import send_day_passed, send_completion
            next_day = (st["current_day"] or 1) + 1
            if next_day > 20:
                send_completion(st["name"], st["email"], lang=_lang)
            else:
                send_day_passed(st["name"], st["email"], sub["assessment_id"], next_day, lang=_lang)
        elif verdict == "revision_requested" and st:
            from email_service import send_revision_requested
            send_revision_requested(st["name"], st["email"], sub["assessment_id"], feedback.strip(), lang=st.get("preferred_lang") or "en")

    return RedirectResponse("/coach/dashboard", 302)

# ─── Coach: Students ──────────────────────────────────────────────────────────

@app.get("/coach/students", response_class=HTMLResponse)
async def coach_students(request: Request):
    coach = _get_coach(request)
    if not coach: return RedirectResponse("/coach", 302)
    coach = dict(coach)

    students = query("""SELECT s.*, c.name as cohort_name
        FROM students s LEFT JOIN cohorts c ON s.cohort_id=c.id
        ORDER BY s.current_day DESC, s.created_at ASC""")

    rows = ""
    for s in students:
        passed  = one("SELECT COUNT(*) as c FROM submissions WHERE student_id=? AND grading_status='approved'", (s["id"],))["c"]
        pending = one("SELECT COUNT(*) as c FROM submissions WHERE student_id=? AND grading_status='pending'",  (s["id"],))["c"]
        pct = int(passed / 20 * 100)
        paid_badge = '<span class="badge badge-paid" style="margin-left:6px">paid</span>' if s["paid_access"] else ""
        cohort_str = f'<span class="text-xs text-muted" style="margin-left:6px">{s["cohort_name"]}</span>' if s["cohort_name"] else ""
        rows += f"""<div class="student-row">
  <div><strong>{s['name']}</strong>{paid_badge}{cohort_str}
    <div class="text-xs text-muted">{s['email']} · joined {str(s['created_at'])[:10]}</div></div>
  <div style="width:150px">
    <div class="text-xs text-muted" style="margin-bottom:4px">Day {s['current_day']}/20 · {pct}%</div>
    <div class="progress-wrap"><div class="progress-fill" style="width:{pct}%"></div></div>
  </div>
  <div class="text-sm text-muted">{passed} passed &nbsp;·&nbsp; {pending} pending</div>
</div>"""

    body = f"""<div class="container"><div class="card">
  <div class="card-title">All Students ({len(students)})</div>
  {rows if rows else '<p class="text-muted">No students registered yet.</p>'}
</div></div>"""
    return HTMLResponse(_page("Students", body, _nav_coach(coach)))

# ─── Coach: Curriculum editor ─────────────────────────────────────────────────

@app.get("/coach/curriculum", response_class=HTMLResponse)
async def coach_curriculum(request: Request):
    coach = _get_coach(request)
    if not coach: return RedirectResponse("/coach", 302)
    coach = dict(coach)

    days = query("SELECT day, title, lesson_status, length(lesson_html) as len FROM curriculum ORDER BY day")
    rows = ""
    for d in days:
        st = d["lesson_status"]
        badge = f'<span class="badge badge-{"published" if st=="published" else "draft"}">{st}</span>'
        words = f'{d["len"]//5 if d["len"] else 0} words approx'
        rows += f"""<tr>
  <td><strong>Day {d['day']}</strong></td>
  <td>{d['title']}</td>
  <td>{badge}</td>
  <td class="text-muted text-sm">{words if d['len'] else '—'}</td>
  <td><a href="/coach/curriculum/{d['day']}" class="btn btn-ghost" style="padding:4px 12px;font-size:.8rem">Edit</a></td>
</tr>"""

    body = f"""<div class="container"><div class="card">
  <div class="flex items-center justify-between" style="margin-bottom:16px">
    <div class="card-title" style="margin:0">Curriculum Editor</div>
    <span class="text-sm text-muted">Run <code>python generate_lessons.py</code> to auto-generate drafts</span>
  </div>
  <table class="table"><thead><tr>
    <th>#</th><th>Title</th><th>Status</th><th>Content</th><th></th>
  </tr></thead><tbody>{rows}</tbody></table>
</div></div>"""
    return HTMLResponse(_page("Curriculum", body, _nav_coach(coach)))


@app.get("/coach/curriculum/{day_num}", response_class=HTMLResponse)
async def coach_edit_day(day_num: int, request: Request):
    coach = _get_coach(request)
    if not coach: return RedirectResponse("/coach", 302)
    coach = dict(coach)
    curr = one("SELECT * FROM curriculum WHERE day=?", (day_num,))
    if not curr: raise HTTPException(404)

    saved = request.query_params.get("saved", "")
    flash = '<div class="alert alert-success">Saved successfully.</div>' if saved else ""
    pub_checked = "checked" if curr["lesson_status"] == "published" else ""

    body = f"""<div class="container" style="max-width:900px">
  {flash}
  <div class="card">
    <div class="flex items-center gap-3" style="margin-bottom:20px">
      <a href="/coach/curriculum" class="text-sm text-muted">← Curriculum</a>
      <span class="badge badge-new">Day {day_num}</span>
      <strong>{curr['title']}</strong>
    </div>
    <form method="POST" action="/coach/curriculum/{day_num}">
      <div class="form-group">
        <label class="form-label">Lesson HTML</label>
        <div class="text-xs text-muted" style="margin-bottom:6px">Use &lt;h3&gt;, &lt;p&gt;, &lt;ul&gt;, &lt;ol&gt;, &lt;dl&gt;, &lt;strong&gt;, &lt;blockquote&gt; only. Run generate_lessons.py to auto-fill.</div>
        <textarea name="lesson_html" rows="20" style="font-family:monospace;font-size:.82rem">{curr['lesson_html'] or ''}</textarea>
      </div>
      <div class="form-group">
        <label class="form-label">YouTube Embed URL</label>
        <input type="text" name="video_url" value="{curr['video_url'] or ''}" placeholder="https://www.youtube.com/embed/VIDEO_ID">
        <div class="text-xs text-muted mt-2">Paste the embed URL (youtube.com/embed/...) or a regular watch URL — it will be converted automatically.</div>
      </div>
      <div class="form-group" style="display:flex;align-items:center;gap:10px">
        <input type="checkbox" name="publish" id="pub" value="1" {pub_checked} style="width:auto;margin:0">
        <label for="pub" style="font-weight:600;font-size:.9rem;cursor:pointer">Published (visible to students)</label>
      </div>
      <div class="flex gap-3">
        <button class="btn btn-dark">Save</button>
        <a href="/student/day/{day_num}" target="_blank" class="btn btn-ghost">Preview as Student →</a>
      </div>
    </form>
  </div>
</div>"""
    return HTMLResponse(_page(f"Edit Day {day_num}", body, _nav_coach(coach)))


@app.post("/coach/curriculum/{day_num}")
async def coach_save_day(day_num: int, request: Request,
                          lesson_html: str = Form(""),
                          video_url: str   = Form(""),
                          publish: str     = Form("")):
    coach = _get_coach(request)
    if not coach: return RedirectResponse("/coach", 302)
    status = "published" if publish == "1" else "draft"
    run("UPDATE curriculum SET lesson_html=?,video_url=?,lesson_status=? WHERE day=?",
        (lesson_html.strip(), video_url.strip(), status, day_num))
    return RedirectResponse(f"/coach/curriculum/{day_num}?saved=1", 302)

# ─── Coach: Cohorts ───────────────────────────────────────────────────────────

@app.get("/coach/cohorts", response_class=HTMLResponse)
async def coach_cohorts(request: Request):
    coach = _get_coach(request)
    if not coach: return RedirectResponse("/coach", 302)
    coach = dict(coach)

    cohorts = query("SELECT * FROM cohorts ORDER BY created_at DESC")
    rows = ""
    for c in cohorts:
        seats = one("SELECT COUNT(*) as n FROM students WHERE cohort_id=?", (c["id"],))["n"]
        revenue = one("SELECT COALESCE(SUM(amount),0) as t FROM payments WHERE cohort_id=? AND status='success'", (c["id"],))["t"]
        status_badge = '<span class="badge badge-approved">Open</span>' if c["is_open"] else '<span class="badge badge-locked">Closed</span>'
        close_btn = f'<form method="POST" action="/coach/cohorts/{c["id"]}/close" style="display:inline"><button class="btn btn-ghost" style="padding:4px 10px;font-size:.8rem">Close</button></form>' if c["is_open"] else ""
        rows += f"""<div class="cohort-row">
  <div><strong>{c['name']}</strong> {status_badge}
    <div class="text-xs text-muted">Starts {c['start_date']} · ${c['price_usd']} {c['currency']}{f' · max {c["max_seats"]} seats' if c['max_seats'] else ''}</div>
  </div>
  <div class="text-sm text-muted">{seats} students · ${revenue:.0f} revenue</div>
  {close_btn}
</div>"""

    body = f"""<div class="container"><div class="card">
  <div class="card-title">Cohorts</div>
  {rows if rows else '<p class="text-muted mb-2">No cohorts yet.</p>'}
  <hr class="divider">
  <div class="card-title">Create New Cohort</div>
  <form method="POST" action="/coach/cohorts">
    <div class="grid-3">
      <div class="form-group"><label class="form-label">Cohort Name</label>
        <input type="text" name="name" placeholder="Cohort 1 — Jan 2026" required></div>
      <div class="form-group"><label class="form-label">Start Date</label>
        <input type="date" name="start_date" required></div>
      <div class="form-group"><label class="form-label">Price (USD)</label>
        <input type="number" name="price_usd" value="{PRICE_USD}" min="0" step="0.01" required></div>
    </div>
    <div class="form-group" style="max-width:200px"><label class="form-label">Max Seats (optional)</label>
      <input type="number" name="max_seats" placeholder="Leave blank for unlimited" min="1"></div>
    <button class="btn btn-dark">Create Cohort</button>
  </form>
</div></div>"""
    return HTMLResponse(_page("Cohorts", body, _nav_coach(coach)))


@app.post("/coach/cohorts")
async def create_cohort(request: Request, name: str = Form(...), start_date: str = Form(...),
                         price_usd: float = Form(...), max_seats: str = Form("")):
    coach = _get_coach(request)
    if not coach: return RedirectResponse("/coach", 302)
    seats = int(max_seats) if max_seats.strip().isdigit() else None
    run("INSERT INTO cohorts (name,start_date,price_usd,currency,max_seats) VALUES (?,?,?,'USD',?)",
        (name, start_date, price_usd, seats))
    return RedirectResponse("/coach/cohorts", 302)


@app.post("/coach/cohorts/{cohort_id}/close")
async def close_cohort(cohort_id: int, request: Request):
    coach = _get_coach(request)
    if not coach: return RedirectResponse("/coach", 302)
    run("UPDATE cohorts SET is_open=0 WHERE id=?", (cohort_id,))
    return RedirectResponse("/coach/cohorts", 302)

# ─── Coach: Payments ──────────────────────────────────────────────────────────

@app.get("/coach/payments", response_class=HTMLResponse)
async def coach_payments(request: Request):
    coach = _get_coach(request)
    if not coach: return RedirectResponse("/coach", 302)
    coach = dict(coach)

    payments = query("""SELECT p.*, s.name as sname, s.email as semail
        FROM payments p JOIN students s ON p.student_id=s.id
        ORDER BY p.created_at DESC LIMIT 200""")

    total = one("SELECT COALESCE(SUM(amount),0) as t FROM payments WHERE status='success'")["t"]

    rows = "".join(f"""<tr>
  <td>{str(p['created_at'])[:16]}</td>
  <td><strong>{p['sname']}</strong><br><span class="text-xs text-muted">{p['semail']}</span></td>
  <td>${p['amount']:.2f} {p['currency']}</td>
  <td><span class="badge {'badge-approved' if p['status']=='success' else 'badge-pending' if p['status']=='pending' else 'badge-revision'}">{p['status']}</span></td>
  <td class="text-xs text-muted">{p['tx_ref'][:20]}…</td>
</tr>""" for p in payments)

    body = f"""<div class="container"><div class="card">
  <div class="flex items-center justify-between" style="margin-bottom:16px">
    <div class="card-title" style="margin:0">Payment Ledger</div>
    <div class="stat-num">${total:.2f} <span class="text-sm text-muted font-normal">total revenue</span></div>
  </div>
  {f'<table class="table"><thead><tr><th>Date</th><th>Student</th><th>Amount</th><th>Status</th><th>Ref</th></tr></thead><tbody>{rows}</tbody></table>' if rows else '<p class="text-muted">No payments yet.</p>'}
</div></div>"""
    return HTMLResponse(_page("Payments", body, _nav_coach(coach)))

# ─── Coach: Ops ───────────────────────────────────────────────────────────────

@app.get("/coach/ops", response_class=HTMLResponse)
async def coach_ops_dashboard(request: Request):
    coach = _get_coach(request)
    if not coach: return RedirectResponse("/coach", 302)
    coach = dict(coach)

    # Queue state
    queue_counts = query("SELECT status, COUNT(*) as c FROM assessment_jobs GROUP BY status")
    queue_state = {row["status"]: row["c"] for row in queue_counts}
    
    # Failed/Dead jobs
    failed_jobs = query("SELECT * FROM assessment_jobs WHERE status IN ('failed', 'dead') ORDER BY created_at DESC LIMIT 50")
    
    # Failed Payments
    failed_payments = query("SELECT p.*, s.email FROM payments p JOIN students s ON p.student_id=s.id WHERE p.status='failed' ORDER BY p.created_at DESC LIMIT 50")
    
    # Pending Reconciliations
    pending_recon = query("SELECT p.*, s.email FROM payments p JOIN students s ON p.student_id=s.id WHERE p.status='pending' AND p.reconciliation_attempts > 0 ORDER BY p.updated_at DESC LIMIT 50")

    def job_row(j):
        return f'''<tr>
            <td>{j['id']}</td>
            <td>{j['submission_id']}</td>
            <td><span class="badge badge-revision">{j['status']}</span></td>
            <td>{j['created_at'][:19]}</td>
            <td>
                <form method="POST" action="/coach/ops/jobs/{j['id']}/retry" style="display:inline">
                    <button class="btn btn-ghost" style="padding:4px 10px;font-size:.8rem">Retry</button>
                </form>
            </td>
        </tr>'''

    def payment_row(p):
        return f'''<tr>
            <td>{p['tx_ref'][:16]}…</td>
            <td>{p['email']}</td>
            <td>${p['amount']}</td>
            <td class="text-xs text-muted">{p['last_reconciliation_error'] or ''}</td>
            <td>{p['created_at'][:19]}</td>
        </tr>'''

    body = f"""<div class="container">
    <div class="card">
        <div class="flex items-center justify-between mb-2">
            <div class="card-title" style="margin:0">Operations Panel</div>
            <div class="flex gap-2">
                <form method="POST" action="/coach/ops/payments/reconcile" style="display:inline">
                    <button class="btn btn-orange" style="padding:6px 12px;font-size:.8rem">Reconcile Pending Payments</button>
                </form>
                <a href="/coach/ops/audit" class="btn btn-ghost" style="padding:6px 12px;font-size:.8rem">View Audit Logs</a>
            </div>
        </div>
        <hr class="divider">
        
        <h4>Assessment Queue State</h4>
        <div class="grid-stats mt-3">
            <div class="stat"><div class="stat-num">{queue_state.get('pending', 0)}</div><div class="stat-label">Pending</div></div>
            <div class="stat"><div class="stat-num">{queue_state.get('running', 0)}</div><div class="stat-label">Running</div></div>
            <div class="stat"><div class="stat-num" style="color:#991b1b">{queue_state.get('failed', 0)}</div><div class="stat-label">Failed</div></div>
            <div class="stat"><div class="stat-num" style="color:#000">{queue_state.get('dead', 0)}</div><div class="stat-label">Dead</div></div>
        </div>
        
        <hr class="divider">
        
        <h4>Dead & Failed Jobs</h4>
        <table class="table mt-3"><thead><tr><th>Job ID</th><th>Sub ID</th><th>Status</th><th>Created</th><th>Action</th></tr></thead>
        <tbody>{''.join(job_row(j) for j in failed_jobs) if failed_jobs else '<tr><td colspan="5" class="text-muted">No failed or dead jobs.</td></tr>'}</tbody></table>
        
        <hr class="divider">
        
        <h4>Pending Reconciliations (Retrying)</h4>
        <table class="table mt-3"><thead><tr><th>Tx Ref</th><th>Student</th><th>Amount</th><th>Last Error</th><th>Created</th></tr></thead>
        <tbody>{''.join(payment_row(p) for p in pending_recon) if pending_recon else '<tr><td colspan="5" class="text-muted">No pending reconciliations.</td></tr>'}</tbody></table>
        
        <hr class="divider">
        
        <h4>Failed Payments</h4>
        <table class="table mt-3"><thead><tr><th>Tx Ref</th><th>Student</th><th>Amount</th><th>Error</th><th>Created</th></tr></thead>
        <tbody>{''.join(payment_row(p) for p in failed_payments) if failed_payments else '<tr><td colspan="5" class="text-muted">No failed payments.</td></tr>'}</tbody></table>
    </div>
</div>"""
    return HTMLResponse(_page("Operations", body, _nav_coach(coach)))

@app.post("/coach/ops/jobs/{job_id}/retry")
async def coach_ops_retry_job(job_id: str, request: Request):
    coach = _get_coach(request)
    if not coach: return RedirectResponse("/coach", 302)
    
    # Reset job to pending so the queue can pick it up again
    run("UPDATE assessment_jobs SET status='pending' WHERE id=?", (job_id,))
    return RedirectResponse("/coach/ops", 302)

@app.get("/coach/ops/audit", response_class=HTMLResponse)
async def coach_ops_audit(request: Request):
    coach = _get_coach(request)
    if not coach: return RedirectResponse("/coach", 302)
    coach = dict(coach)
    
    logs = query("SELECT * FROM webhook_logs ORDER BY created_at DESC LIMIT 100")
    
    def log_row(l):
        status_color = "#166534" if l['status'] == "processed" else "#991b1b"
        return f'''<div class="sub-card" style="border-left-color:{status_color}">
            <div class="flex justify-between mb-2">
                <strong>{l['event_type'] or 'unknown'}</strong>
                <span class="text-xs text-muted">{l['created_at']}</span>
            </div>
            <div class="text-sm mb-2">Status: <span style="color:{status_color};font-weight:bold">{l['status']}</span> | Tx Ref: {l['tx_ref']}</div>
            <pre style="background:#1C1C1E;color:var(--foreground);padding:10px;border-radius:6px;font-size:.75rem;overflow-x:auto">{l['payload_json']}</pre>
        </div>'''

    body = f"""<div class="container">
    <div class="card">
        <div class="flex items-center gap-3 mb-4">
            <a href="/coach/ops" class="btn btn-ghost" style="padding:4px 10px;font-size:.8rem">← Back</a>
            <div class="card-title" style="margin:0">Webhook Audit Logs</div>
        </div>
        {''.join(log_row(l) for l in logs) if logs else '<p class="text-muted">No logs found.</p>'}
    </div>
</div>"""
    return HTMLResponse(_page("Audit Logs", body, _nav_coach(coach)))

# ─── File serving ─────────────────────────────────────────────────────────────

@app.get("/uploads/{filename:path}")
async def serve_upload(filename: str, request: Request):
    if not _get_student(request) and not _get_coach(request):
        return RedirectResponse("/", 302)
    fpath = UPLOADS / filename
    if fpath.exists():
        return FileResponse(fpath)

    if _using_blob_storage():
        # stored_path is a full blob URL (https://...blob.vercel-storage.com/...)
        # HTTP path normalisation collapses https:// → https:/ in transit; restore it.
        if filename.startswith("https:/") and not filename.startswith("https://"):
            filename = "https://" + filename[7:]
        if not filename.startswith("https://"):
            raise HTTPException(404)
        # Private blobs require the bearer token — the SDK's get() makes an
        # unauthenticated fetch which returns 403. Proxy via httpx with auth header.
        blob_token = os.getenv("BLOB_READ_WRITE_TOKEN", "")
        async with httpx.AsyncClient(follow_redirects=True, timeout=30) as http_client:
            blob_resp = await http_client.get(
                filename, headers={"Authorization": f"Bearer {blob_token}"}
            )
        if blob_resp.status_code == 404:
            raise HTTPException(404)
        if blob_resp.status_code != 200:
            raise HTTPException(blob_resp.status_code)
        import io
        return StreamingResponse(
            io.BytesIO(blob_resp.content),
            media_type=blob_resp.headers.get("content-type", "application/octet-stream"),
            headers={"X-Content-Type-Options": "nosniff"},
        )

    raise HTTPException(404)

# ─── Ops: Overrides ──────────────────────────────────────────────────────────

@app.post("/coach/ops/grade/override/{grading_result_id}")
async def override_grade(grading_result_id: int, request: Request, new_score: float = Form(...), new_status: str = Form(...), reason: str = Form(...)):
    coach = _get_coach(request)
    if not coach: return RedirectResponse("/coach", 302)
    
    from rubric_engine import manual_override
    manual_override(grading_result_id, new_score, new_status, reason, dict(coach)["name"])
    
    return RedirectResponse("/coach/dashboard", 302)

# ─── Ops: AI Feedback ─────────────────────────────────────────────────────────

from fastapi import HTTPException

@app.post("/coach/ops/feedback/{feedback_id}/hide")
async def hide_ai_feedback(feedback_id: int, request: Request):
    coach = _get_coach(request)
    if not coach: return RedirectResponse("/coach", 302)
    
    conn = db.get_connection()
    try:
        conn.execute("BEGIN IMMEDIATE")
        fb = conn.execute("SELECT grading_result_id FROM ai_feedback WHERE id=?", (feedback_id,)).fetchone()
        if not fb: raise HTTPException(404, "Feedback not found")
        
        conn.execute("UPDATE ai_feedback SET feedback_status='hidden' WHERE id=?", (feedback_id,))
        
        gr = conn.execute("SELECT submission_id, rubric_id, rubric_version FROM grading_results WHERE id=?", (fb["grading_result_id"],)).fetchone()
        conn.commit()
        
        if gr:
            from assessment_logger import log_assessment_event
            log_assessment_event("feedback_hidden", gr["submission_id"], gr["rubric_id"], gr["rubric_version"], reviewer=dict(coach)["name"])
            
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        db.return_connection(conn)
        
    return RedirectResponse(request.headers.get("referer", "/coach/dashboard"), 302)

@app.post("/coach/ops/feedback/{grading_result_id}/regenerate")
async def regenerate_ai_feedback(grading_result_id: int, request: Request):
    coach = _get_coach(request)
    if not coach: return RedirectResponse("/coach", 302)
    
    import uuid
    from ai_feedback_queue import trigger_feedback_job
    
    conn = db.get_connection()
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("UPDATE ai_feedback SET feedback_status='hidden' WHERE grading_result_id=?", (grading_result_id,))
        
        job_id = str(uuid.uuid4())
        conn.execute(
            "INSERT INTO ai_feedback_jobs (id, grading_result_id) VALUES (?, ?)",
            (job_id, grading_result_id)
        )
        
        gr = conn.execute("SELECT submission_id, rubric_id, rubric_version FROM grading_results WHERE id=?", (grading_result_id,)).fetchone()
        conn.commit()
        
        if gr:
            from assessment_logger import log_assessment_event
            log_assessment_event("feedback_regenerated", gr["submission_id"], gr["rubric_id"], gr["rubric_version"], reviewer=dict(coach)["name"])
            
        trigger_feedback_job(job_id, grading_result_id)
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        db.return_connection(conn)

    return RedirectResponse(request.headers.get("referer", "/coach/dashboard"), 302)


@app.post("/coach/applications/{app_id}/status")
async def coach_application_set_status(request: Request, app_id: int, status: str = Form(...)):
    coach = _get_coach(request)
    if not coach:
        return RedirectResponse("/coach", 302)
    if status not in ("pending", "reviewed", "accepted", "rejected"):
        raise HTTPException(400, "Invalid status")
    run("UPDATE creative_tech_applications SET status=? WHERE id=?", (status, app_id))
    return RedirectResponse("/coach/applications", 302)


_APP_STATUS_COLORS = {
    "pending":  ("background:#374151;color:#d1d5db", "Pending"),
    "reviewed": ("background:#1e3a5f;color:#93c5fd", "Reviewed"),
    "accepted": ("background:#14532d;color:#86efac", "Accepted"),
    "rejected": ("background:#7f1d1d;color:#fca5a5", "Rejected"),
}


@app.get("/coach/applications", response_class=HTMLResponse)
async def coach_applications(request: Request):
    coach = _get_coach(request)
    if not coach:
        return RedirectResponse("/coach", 302)
    apps = query(
        "SELECT id, name, email, country, background, motivation, created_at, "
        "COALESCE(status,'pending') as status "
        "FROM creative_tech_applications ORDER BY created_at DESC"
    )
    def _status_badge(s):
        style, label = _APP_STATUS_COLORS.get(s, _APP_STATUS_COLORS["pending"])
        return f'<span style="font-size:11px;padding:2px 8px;border-radius:9999px;{style}">{label}</span>'
    def _status_form(app_id, current):
        opts = "".join(
            f'<option value="{v}"{" selected" if v==current else ""}>{label}</option>'
            for v, (_, label) in _APP_STATUS_COLORS.items()
        )
        return (
            f'<form method="post" action="/coach/applications/{app_id}/status" style="display:inline">'
            f'<select name="status" onchange="this.form.submit()" '
            f'style="background:#111;color:#ddd;border:1px solid #333;border-radius:4px;padding:2px 4px;font-size:12px">'
            f'{opts}</select></form>'
        )
    rows = "".join(
        f'<tr style="border-bottom:1px solid #2a2a2a">'
        f'<td style="padding:10px 8px">{a["name"]}</td>'
        f'<td style="padding:10px 8px"><a href="mailto:{a["email"]}" style="color:#e53e3e">{a["email"]}</a></td>'
        f'<td style="padding:10px 8px">{a["country"]}</td>'
        f'<td style="padding:10px 8px;max-width:200px;white-space:normal;color:#bbb">{a["background"][:100]}{"…" if len(a["background"])>100 else ""}</td>'
        f'<td style="padding:10px 8px;max-width:200px;white-space:normal;color:#bbb">{a["motivation"][:100]}{"…" if len(a["motivation"])>100 else ""}</td>'
        f'<td style="padding:10px 8px;white-space:nowrap">{a["created_at"][:10]}</td>'
        f'<td style="padding:10px 8px">{_status_form(a["id"], a["status"])}</td>'
        f'</tr>'
        for a in apps
    )
    body = f"""
<h2>Creative Tech Applications ({len(apps)})</h2>
<table style="width:100%;border-collapse:collapse;font-size:13px">
  <thead><tr style="background:#1a1a1a;text-align:left">
    <th style="padding:10px 8px">Name</th>
    <th style="padding:10px 8px">Email</th>
    <th style="padding:10px 8px">Country</th>
    <th style="padding:10px 8px">Background</th>
    <th style="padding:10px 8px">Motivation</th>
    <th style="padding:10px 8px">Date</th>
    <th style="padding:10px 8px">Status</th>
  </tr></thead>
  <tbody>{rows if rows else '<tr><td colspan="7" style="padding:16px;color:#888">No applications yet.</td></tr>'}</tbody>
</table>"""
    nav = '<a href="/coach/dashboard">← Dashboard</a>'
    return HTMLResponse(_page("Applications", body, nav=nav))


from pydantic import BaseModel, Field

class ContactRequest(BaseModel):
    name: str = Field(..., min_length=2)
    contact_info: str = Field(..., min_length=5)
    service: str = Field(...)
    message: str = Field(..., min_length=10)

@app.post("/api/contact")
async def receive_contact_message(payload: ContactRequest):
    # 1. Save message in the database
    try:
        run(
            "INSERT INTO contact_messages (name, contact_info, service, message) VALUES (?, ?, ?, ?)",
            (payload.name, payload.contact_info, payload.service, payload.message)
        )
    except Exception as exc:
        import logging
        logging.error("Failed to save contact message to database: %s", exc)
        raise HTTPException(status_code=500, detail="Database write failed")

    # 2. Trigger real-time email notification (Option C integration)
    try:
        from email_service import send_email, _html_wrap
        import os
        contact_email = os.getenv("CONTACT_EMAIL", "hello@dako.studio")
        subject = f"New Project Brief: {payload.name} ({payload.service.upper()})"
        html_body = _html_wrap(f"""
            <h2 style="font-size:1.25rem;font-weight:800;color:#CC0A0A;margin:0 0 16px">New Project Brief Submitted</h2>
            <p style="color:#bbb;margin:0 0 12px">A new inquiry has been received from the Dako Studios website.</p>
            <hr style="border:none;border-top:1px solid #2a2a2a;margin:16px 0" />
            <table style="width:100%;border-collapse:collapse;color:#ddd;font-size:14px;line-height:1.6">
              <tr>
                <td style="padding:6px 0;font-weight:bold;width:120px">Client Name:</td>
                <td style="padding:6px 0">{payload.name}</td>
              </tr>
              <tr>
                <td style="padding:6px 0;font-weight:bold">Contact Info:</td>
                <td style="padding:6px 0">{payload.contact_info}</td>
              </tr>
              <tr>
                <td style="padding:6px 0;font-weight:bold">Service Area:</td>
                <td style="padding:6px 0;text-transform:capitalize">{payload.service}</td>
              </tr>
              <tr>
                <td style="padding:6px 0;font-weight:bold;vertical-align:top">Project Brief:</td>
                <td style="padding:6px 0;white-space:pre-wrap">{payload.message}</td>
              </tr>
            </table>
        """)
        send_email(contact_email, subject, html_body)
    except Exception as exc:
        import logging
        logging.warning("Failed to send contact notification email: %s", exc)

    return {"status": "success", "message": "Inquiry submitted successfully"}

