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
from fastapi import FastAPI, Request, Form, File, UploadFile, HTTPException
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

def _lang_switcher(current_lang: str) -> str:
    labels = {"en": "EN", "pcm": "PCM", "yo": "YO", "ha": "HA", "ig": "IG"}
    btns = "".join(
        f'<form method="POST" action="/set-language" style="display:inline;margin:0">'
        f'<input type="hidden" name="lang" value="{code}">'
        f'<button type="submit" class="lang-btn{"  lang-btn-active" if code == current_lang else ""}">{labels[code]}</button>'
        f'</form>'
        for code in SUPPORTED_LANGS
    )
    return f'<div class="lang-switcher">{btns}</div>'

def _is_local_dev() -> bool:
    return BASE_URL.startswith("http://localhost") or BASE_URL.startswith("http://127.0.0.1")

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
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;background:#f0f2f5;color:#1a1a1a;line-height:1.5;min-height:100vh}
a{color:inherit}
.nav{background:#1C1C1E;color:#fff;padding:0 28px;height:60px;display:flex;align-items:center;justify-content:space-between;position:sticky;top:0;z-index:100}
.nav-brand{font-weight:800;font-size:1.05rem;letter-spacing:-.01em}
.nav-brand .r{color:#E63946}
.nav-right{display:flex;align-items:center;gap:20px;font-size:.85rem}
.nav-right a{color:rgba(255,255,255,.65);text-decoration:none}
.nav-right a:hover{color:#fff}
.nav-user{color:rgba(255,255,255,.85)}
.nav-badge{background:#E63946;color:#fff;padding:2px 8px;border-radius:20px;font-size:.7rem;font-weight:700}
.container{max-width:1080px;margin:0 auto;padding:28px 20px}
.card{background:#fff;border-radius:12px;padding:28px;box-shadow:0 1px 3px rgba(0,0,0,.07),0 4px 16px rgba(0,0,0,.04);margin-bottom:20px}
.card-sm{padding:18px 22px}
.card-title{font-size:1.2rem;font-weight:700;margin-bottom:16px}
.btn{display:inline-flex;align-items:center;justify-content:center;gap:6px;padding:10px 22px;border-radius:8px;border:none;cursor:pointer;font-size:.9rem;font-weight:600;text-decoration:none;transition:opacity .15s,transform .1s;font-family:inherit;white-space:nowrap}
.btn:hover{opacity:.87}.btn:active{transform:scale(.98)}
.btn-red{background:#E63946;color:#fff}
.btn-dark{background:#1C1C1E;color:#fff}
.btn-green{background:#22c55e;color:#fff}
.btn-orange{background:#f97316;color:#fff}
.btn-gold{background:#f59e0b;color:#fff}
.btn-ghost{background:transparent;border:2px solid #d1d5db;color:#1a1a1a}
.btn-full{width:100%}
.btn-lg{padding:14px 32px;font-size:1rem}
.form-group{margin-bottom:18px}
.form-label{display:block;font-size:.85rem;font-weight:600;margin-bottom:6px}
input[type=text],input[type=email],input[type=password],input[type=number],input[type=date],textarea,select{width:100%;padding:10px 14px;border:1.5px solid #d1d5db;border-radius:8px;font-size:.9rem;font-family:inherit;background:#fff;color:#1a1a1a;transition:border-color .15s}
input:focus,textarea:focus,select:focus{outline:none;border-color:#E63946}
textarea{resize:vertical;min-height:110px}
.badge{display:inline-flex;align-items:center;padding:3px 10px;border-radius:20px;font-size:.72rem;font-weight:700;text-transform:uppercase;letter-spacing:.04em}
.badge-pending{background:#fef3c7;color:#92400e}
.badge-approved{background:#dcfce7;color:#166534}
.badge-revision{background:#fee2e2;color:#991b1b}
.badge-locked{background:#f3f4f6;color:#6b7280}
.badge-new{background:#dbeafe;color:#1e40af}
.badge-draft{background:#f3f4f6;color:#6b7280}
.badge-published{background:#dcfce7;color:#166534}
.badge-paid{background:#fef3c7;color:#92400e}
.grid-2{display:grid;grid-template-columns:2fr 1fr;gap:20px}
.grid-3{display:grid;grid-template-columns:repeat(3,1fr);gap:16px}
.grid-stats{display:grid;grid-template-columns:repeat(4,1fr);gap:16px;margin-bottom:24px}
.grid-days{display:grid;grid-template-columns:repeat(auto-fill,minmax(155px,1fr));gap:12px}
.stat{background:#fff;border-radius:10px;padding:18px 20px;box-shadow:0 1px 4px rgba(0,0,0,.07)}
.stat-num{font-size:2rem;font-weight:800;color:#E63946}
.stat-label{font-size:.78rem;color:#6b7280;font-weight:600;text-transform:uppercase;letter-spacing:.04em;margin-top:2px}
.day-card{border-radius:10px;padding:14px 16px;border:2px solid transparent;transition:all .15s;display:block;text-decoration:none;color:inherit}
.day-card.locked{background:#f9fafb;border-color:#e5e7eb;opacity:.55;pointer-events:none}
.day-card.paywalled{background:#fffbeb;border-color:#fbbf24;cursor:pointer}
.day-card.paywalled:hover{transform:translateY(-2px);box-shadow:0 4px 16px rgba(251,191,36,.25)}
.day-card.available{background:#fff;border-color:#E63946;box-shadow:0 2px 8px rgba(230,57,70,.12)}
.day-card.available:hover{transform:translateY(-2px);box-shadow:0 6px 20px rgba(230,57,70,.2)}
.day-card.pending{background:#eff6ff;border-color:#3b82f6}
.day-card.graded-pass{background:#f0fdf4;border-color:#22c55e}
.day-card.graded-revision{background:#fff7ed;border-color:#f97316}
.day-num{font-size:.68rem;font-weight:700;text-transform:uppercase;letter-spacing:.08em;color:#9ca3af;margin-bottom:4px}
.day-title{font-size:.82rem;font-weight:600;line-height:1.35}
.day-status{font-size:.7rem;color:#6b7280;margin-top:6px}
.progress-wrap{background:#f3f4f6;border-radius:8px;height:8px;overflow:hidden}
.progress-fill{height:100%;background:linear-gradient(90deg,#E63946,#ff6b7a);border-radius:8px}
.alert{padding:12px 16px;border-radius:8px;margin-bottom:18px;font-size:.875rem}
.alert-error{background:#fee2e2;color:#991b1b;border-left:4px solid #ef4444}
.alert-success{background:#dcfce7;color:#166534;border-left:4px solid #22c55e}
.alert-info{background:#dbeafe;color:#1e40af;border-left:4px solid #3b82f6}
.alert-warn{background:#fef3c7;color:#92400e;border-left:4px solid #fbbf24}
.divider{border:none;border-top:1px solid #f0f2f5;margin:20px 0}
.text-muted{color:#6b7280}.text-sm{font-size:.875rem}.text-xs{font-size:.78rem}
.mt-2{margin-top:8px}.mt-3{margin-top:12px}.mt-4{margin-top:16px}.mb-2{margin-bottom:8px}
.flex{display:flex}.flex-1{flex:1}.gap-2{gap:8px}.gap-3{gap:12px}
.items-center{align-items:center}.justify-between{justify-content:space-between}
.img-thumb{width:90px;height:72px;object-fit:cover;border-radius:6px;border:1px solid #e5e7eb;cursor:pointer}
.sub-card{border-left:4px solid #e5e7eb;padding:16px;border-radius:0 8px 8px 0;background:#f9fafb;margin-bottom:12px}
.sub-card.approved{border-color:#22c55e;background:#f0fdf4}
.sub-card.needs_revision{border-color:#f97316;background:#fff7ed}
.sub-card.pending{border-color:#3b82f6;background:#eff6ff}
.student-row{display:flex;align-items:center;justify-content:space-between;padding:14px 0;border-bottom:1px solid #f0f2f5}
.student-row:last-child{border-bottom:none}
.table{width:100%;border-collapse:collapse;font-size:.875rem}
.table th{text-align:left;padding:10px 12px;background:#f9fafb;border-bottom:2px solid #f0f2f5;font-size:.75rem;font-weight:700;text-transform:uppercase;letter-spacing:.04em;color:#6b7280}
.table td{padding:10px 12px;border-bottom:1px solid #f0f2f5}
.table tr:last-child td{border-bottom:none}
.login-wrap{min-height:100vh;display:flex;align-items:center;justify-content:center;padding:20px;background:linear-gradient(135deg,#1C1C1E 0%,#2c2c2e 50%,#1C1C1E 100%)}
.login-box{background:#fff;border-radius:16px;padding:40px;width:100%;max-width:420px;box-shadow:0 20px 60px rgba(0,0,0,.3)}
.login-logo{text-align:center;margin-bottom:28px}
.login-logo .logo-text{font-size:1.5rem;font-weight:800}
.logo-text .r{color:#E63946}
.tagline{color:#6b7280;font-size:.85rem;margin-top:4px}
.tab-nav{display:flex;border-bottom:2px solid #f0f2f5;margin-bottom:24px}
.tab-btn{flex:1;padding:10px;text-align:center;font-size:.875rem;font-weight:600;color:#6b7280;cursor:pointer;border-bottom:2px solid transparent;margin-bottom:-2px;background:none;border-top:none;border-left:none;border-right:none;font-family:inherit;transition:color .15s}
.tab-btn.active{color:#E63946;border-bottom-color:#E63946}
.tab-panel{display:none}.tab-panel.active{display:block}
.lesson-content h3{font-size:1rem;font-weight:700;margin:20px 0 8px;color:#1C1C1E}
.lesson-content h3:first-child{margin-top:0}
.lesson-content p{margin-bottom:12px;line-height:1.75;color:#374151}
.lesson-content ul,.lesson-content ol{padding-left:20px;margin-bottom:12px}
.lesson-content li{margin-bottom:6px;line-height:1.65}
.lesson-content dl{margin-bottom:12px}
.lesson-content dt{font-weight:700;color:#1C1C1E;margin-top:10px}
.lesson-content dd{color:#374151;padding-left:16px;margin-top:2px}
.lesson-content blockquote{border-left:4px solid #E63946;padding:10px 16px;background:#fff5f5;border-radius:0 8px 8px 0;margin:16px 0;font-style:italic}
.video-wrap{position:relative;padding-bottom:56.25%;height:0;overflow:hidden;border-radius:10px;margin-bottom:20px}
.video-wrap iframe{position:absolute;top:0;left:0;width:100%;height:100%;border:none;border-radius:10px}
.paywall-box{text-align:center;padding:48px 32px}
.paywall-price{font-size:3rem;font-weight:900;color:#E63946;line-height:1}
.paywall-currency{font-size:1.5rem;font-weight:600;vertical-align:top;margin-top:6px;display:inline-block}
.feature-list{list-style:none;text-align:left;max-width:320px;margin:20px auto}
.feature-list li{padding:8px 0;display:flex;align-items:center;gap:10px;font-size:.95rem}
.feature-list li::before{content:"✓";color:#22c55e;font-weight:900;font-size:1.1rem}
.pricing-hero{background:linear-gradient(135deg,#1C1C1E,#2c2c2e);color:#fff;padding:80px 40px;text-align:center;border-radius:0 0 20px 20px}
.pricing-hero h1{font-size:2.5rem;font-weight:900;margin-bottom:16px}
.pricing-hero p{font-size:1.1rem;color:rgba(255,255,255,.75);max-width:560px;margin:0 auto 32px}
.pricing-card{max-width:420px;margin:-40px auto 0;background:#fff;border-radius:16px;padding:36px;box-shadow:0 20px 60px rgba(0,0,0,.15);position:relative;z-index:10}
.cohort-row{display:flex;align-items:center;justify-content:space-between;padding:14px 0;border-bottom:1px solid #f0f2f5}
.cohort-row:last-child{border-bottom:none}
@media(max-width:768px){.grid-2{grid-template-columns:1fr}.grid-stats{grid-template-columns:1fr 1fr}.grid-days{grid-template-columns:repeat(auto-fill,minmax(130px,1fr))}.pricing-hero{padding:48px 24px}.pricing-hero h1{font-size:1.75rem}}
.nav-profile{position:relative}
.nav-profile-btn{background:rgba(255,255,255,.12);border:none;color:#fff;width:34px;height:34px;border-radius:50%;display:flex;align-items:center;justify-content:center;cursor:pointer;transition:background .15s;flex-shrink:0}
.nav-profile-btn:hover{background:rgba(255,255,255,.22)}
.nav-dropdown{position:absolute;right:0;top:calc(100% + 8px);background:#fff;border-radius:10px;box-shadow:0 8px 32px rgba(0,0,0,.18);min-width:188px;z-index:200;display:none;overflow:hidden}
.nav-dropdown.open{display:block}
.nav-dropdown-header{padding:11px 16px;border-bottom:1px solid #f0f2f5;font-size:.78rem;font-weight:700;color:#6b7280;text-transform:uppercase;letter-spacing:.05em;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.nav-dropdown a{display:block;padding:10px 16px;color:#1a1a1a !important;text-decoration:none;font-size:.875rem;transition:background .1s}
.nav-dropdown a:hover{background:#f9fafb;color:#1a1a1a !important}
.nav-dropdown hr{border:none;border-top:1px solid #f0f2f5;margin:4px 0}
"""

LANDING_CSS = """
@import url('https://fonts.googleapis.com/css2?family=Bebas+Neue&family=DM+Sans:wght@300;400;500;600;700&display=swap');
:root{--red:#E11D2E;--red2:#c91828;--red-dim:rgba(225,29,46,.10);--red-glow:rgba(225,29,46,.25);--black:#0A0A0A;--g1:#111111;--g2:#1A1A1A;--g3:#242424;--g4:#333333;--muted:#777777;--light:#BBBBBB;--white:#FFFFFF}
.landing-page{background:var(--black);color:var(--white);font-family:'DM Sans',sans-serif;font-size:15px;line-height:1.65;overflow-x:hidden;min-height:100vh}
.landing-page *{box-sizing:border-box}
/* noise overlay */
.landing-page::after{content:'';position:fixed;inset:0;background-image:url("data:image/svg+xml,%3Csvg viewBox='0 0 200 200' xmlns='http://www.w3.org/2000/svg'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.85' numOctaves='4' stitchTiles='stitch'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23n)' opacity='0.035'/%3E%3C/svg%3E");pointer-events:none;z-index:9999;opacity:.5}
/* ── NAV ── */
.l-nav{position:fixed;top:0;left:0;right:0;z-index:500;display:flex;align-items:center;justify-content:space-between;padding:0 48px;height:64px;background:rgba(10,10,10,.96);backdrop-filter:blur(16px);border-bottom:1px solid var(--g3)}
.l-nav-brand{font-family:'Bebas Neue',sans-serif;font-size:20px;letter-spacing:3px;color:var(--white);text-decoration:none}
.l-nav-brand em{color:var(--red);font-style:normal}
.l-nav-right{display:flex;align-items:center;gap:12px}
.l-nav-link{color:var(--muted);font-size:12px;font-weight:600;letter-spacing:1.2px;text-transform:uppercase;text-decoration:none;padding:0 14px;height:64px;display:flex;align-items:center;transition:color .2s}
.l-nav-link:hover{color:var(--white)}
.l-nav-cta{background:var(--red);color:var(--white)!important;padding:0 20px!important;font-weight:700!important;border-bottom:none!important}
.l-nav-cta:hover{background:var(--red2)!important}
/* lang switcher */
.lang-switcher{display:flex;gap:4px}
.lang-btn{background:transparent;border:1px solid var(--g4);color:var(--muted);font-family:'DM Sans',sans-serif;font-size:11px;font-weight:700;letter-spacing:1px;padding:5px 10px;border-radius:3px;cursor:pointer;transition:all .15s}
.lang-btn:hover{border-color:var(--white);color:var(--white)}
.lang-btn-active{border-color:var(--red);color:var(--red)}
/* ── HERO ── */
.l-hero{position:relative;min-height:calc(100vh - 64px);display:flex;align-items:center;justify-content:center;overflow:hidden;padding:80px 48px;margin-top:64px}
.l-hero-bg{position:absolute;inset:0;background:radial-gradient(ellipse 80% 60% at 50% 100%,rgba(225,29,46,.08) 0%,transparent 70%),linear-gradient(180deg,var(--black) 0%,var(--g1) 100%)}
.l-hero-lines{position:absolute;inset:0;background-image:linear-gradient(rgba(255,255,255,.025) 1px,transparent 1px),linear-gradient(90deg,rgba(255,255,255,.025) 1px,transparent 1px);background-size:80px 80px}
.l-hero-content{position:relative;z-index:2;max-width:980px;text-align:center}
.l-eyebrow{display:inline-flex;align-items:center;gap:10px;margin-bottom:32px;animation:fadeUpL .7s ease both}
.l-eyebrow-dot{width:6px;height:6px;background:var(--red);border-radius:50%;animation:pulseL 2s ease infinite}
@keyframes pulseL{0%,100%{opacity:1;transform:scale(1)}50%{opacity:.5;transform:scale(1.4)}}
.l-eyebrow-text{font-size:11px;font-weight:700;letter-spacing:3px;text-transform:uppercase;color:var(--muted)}
.l-hero h1{font-family:'Bebas Neue',sans-serif;font-size:clamp(64px,11vw,120px);line-height:.88;letter-spacing:2px;margin-bottom:24px;animation:fadeUpL .7s .08s ease both}
.l-hero h1 .l-accent{color:var(--red)}
.l-hero-sub{font-size:18px;color:var(--light);max-width:580px;margin:0 auto 48px;animation:fadeUpL .7s .2s ease both}
@keyframes fadeUpL{from{opacity:0;transform:translateY(20px)}to{opacity:1;transform:translateY(0)}}
/* ── CONTAINER ── */
.l-container{max-width:1080px;margin:0 auto;padding:0 48px}
.l-sec-label{font-size:11px;font-weight:700;letter-spacing:3px;text-transform:uppercase;color:var(--red);margin-bottom:12px}
.l-sec-title{font-family:'Bebas Neue',sans-serif;font-size:clamp(36px,5vw,60px);line-height:.95;letter-spacing:1px;margin-bottom:20px}
.l-sec-desc{font-size:16px;color:var(--light);max-width:520px;font-weight:400}
/* ── COURSE CARDS ── */
.l-courses{padding:100px 0;border-top:1px solid var(--g3)}
.l-course-grid{display:grid;grid-template-columns:1fr 1fr;gap:3px;background:var(--g3);border-radius:6px;overflow:hidden;margin-top:56px}
.l-course-card{background:var(--black);padding:48px 40px;position:relative;overflow:hidden;transition:background .2s}
.l-course-card:hover{background:var(--g2)}
.l-course-card::before{content:'';position:absolute;top:0;left:0;right:0;height:3px;background:var(--red);transform:scaleX(0);transition:transform .3s;transform-origin:left}
.l-course-card:hover::before{transform:scaleX(1)}
.l-course-badge{display:inline-flex;align-items:center;gap:6px;background:var(--red-dim);border:1px solid rgba(225,29,46,.3);color:var(--red);font-size:10px;font-weight:700;letter-spacing:2px;text-transform:uppercase;padding:5px 12px;border-radius:2px;margin-bottom:20px}
.l-course-title{font-family:'Bebas Neue',sans-serif;font-size:36px;letter-spacing:1px;line-height:1;margin-bottom:16px}
.l-course-desc{font-size:14px;color:var(--light);line-height:1.7;margin-bottom:28px;max-width:400px}
.l-course-pills{display:flex;flex-wrap:wrap;gap:8px;margin-bottom:28px}
.l-pill{display:flex;align-items:center;gap:6px;padding:6px 12px;border:1px solid var(--g4);border-radius:100px;font-size:12px;font-weight:600;color:var(--light)}
.l-pill-dot{width:5px;height:5px;background:var(--red);border-radius:50%;flex-shrink:0}
.l-price-row{display:flex;align-items:flex-end;gap:8px;margin-bottom:8px}
.l-price{font-family:'Bebas Neue',sans-serif;font-size:52px;line-height:1;color:var(--white)}
.l-price-sub{font-size:12px;color:var(--muted);padding-bottom:8px}
.l-cta{display:inline-flex;align-items:center;gap:8px;padding:14px 28px;background:var(--red);color:var(--white);font-family:'DM Sans',sans-serif;font-size:12px;font-weight:700;letter-spacing:1.5px;text-transform:uppercase;border:none;border-radius:3px;cursor:pointer;text-decoration:none;transition:all .2s;margin-top:12px}
.l-cta:hover{background:var(--red2);transform:translateY(-2px)}
.l-cta-ghost{background:transparent;border:1px solid var(--g4);color:var(--white)}
.l-cta-ghost:hover{border-color:var(--white);background:transparent}
/* ── SECTION ── */
.l-section{padding:100px 0;border-top:1px solid var(--g3)}
.l-section.dark{background:var(--g1)}
/* ── WHY CARDS ── */
.l-why-grid{display:grid;grid-template-columns:repeat(4,1fr);gap:3px;background:var(--g3);border-radius:6px;overflow:hidden;margin-top:56px}
.l-why-card{background:var(--black);padding:36px 28px;transition:background .2s}
.l-why-card:hover{background:var(--g2)}
.l-why-card h4{font-family:'Bebas Neue',sans-serif;font-size:22px;letter-spacing:1px;margin-bottom:12px}
.l-why-card p{font-size:13px;color:var(--muted);line-height:1.7}
/* ── CURRICULUM ── */
.l-weeks{display:flex;flex-direction:column;gap:3px;margin-top:56px}
.l-week{background:var(--black);border:1px solid var(--g3);border-radius:3px;overflow:hidden}
.l-week summary{display:flex;align-items:center;gap:18px;padding:20px 24px;cursor:pointer;user-select:none;list-style:none;transition:background .2s}
.l-week summary::-webkit-details-marker{display:none}
.l-week summary:hover{background:var(--g2)}
.l-week-label{font-size:10px;font-weight:700;letter-spacing:2px;text-transform:uppercase;color:var(--red);min-width:60px}
.l-week-name{flex:1;font-weight:600;font-size:14px}
.l-week-arr{width:20px;height:20px;border:1px solid var(--g4);border-radius:50%;display:flex;align-items:center;justify-content:center;font-size:9px;color:var(--muted);transition:transform .3s;flex-shrink:0}
details[open] .l-week-arr{transform:rotate(180deg);border-color:var(--red);color:var(--red)}
.l-week-days{padding:0 24px 20px;display:flex;flex-direction:column;gap:3px}
.l-day-row{display:flex;align-items:center;gap:16px;padding:12px 16px;background:var(--g2);border-radius:3px}
.l-day-num{font-family:'Bebas Neue',sans-serif;font-size:13px;color:var(--red);min-width:48px}
.l-day-title{flex:1;font-size:13px;font-weight:500}
.l-day-badge{font-size:10px;font-weight:700;letter-spacing:1.5px;text-transform:uppercase;padding:3px 8px;border-radius:2px;flex-shrink:0}
.l-day-free{background:var(--red-dim);color:var(--red);border:1px solid rgba(225,29,46,.3)}
.l-day-locked{background:var(--g3);color:var(--muted)}
/* ── PAYWALL CALLOUT ── */
.l-paywall{padding:80px 48px;background:linear-gradient(135deg,var(--g2),var(--black));border-top:1px solid var(--g3);border-bottom:1px solid var(--g3);text-align:center}
.l-paywall h2{font-family:'Bebas Neue',sans-serif;font-size:clamp(36px,6vw,72px);letter-spacing:2px;margin-bottom:12px}
.l-paywall p{font-size:15px;color:var(--light);max-width:540px;margin:0 auto 32px}
/* ── TRANSFORM GRID ── */
.l-transform-grid{display:grid;grid-template-columns:1fr 1fr;gap:3px;background:var(--g3);border-radius:6px;overflow:hidden;margin-top:56px}
.l-transform-col{background:var(--g1);padding:48px 40px}
.l-transform-col.after-col{background:var(--black)}
.l-transform-header{display:flex;align-items:center;gap:12px;margin-bottom:32px}
.l-transform-tag{font-size:10px;font-weight:700;letter-spacing:2px;text-transform:uppercase;padding:5px 12px;border-radius:2px}
.l-tag-before{background:var(--g3);color:var(--muted)}
.l-tag-after{background:var(--red);color:var(--white)}
.l-transform-list{display:flex;flex-direction:column;gap:14px;list-style:none}
.l-transform-list li{display:flex;align-items:flex-start;gap:12px;font-size:15px}
.l-icon{width:22px;height:22px;border-radius:50%;display:flex;align-items:center;justify-content:center;font-size:10px;flex-shrink:0;margin-top:2px}
.l-icon-x{background:var(--g3);color:var(--muted)}
.l-icon-check{background:var(--red-dim);color:var(--red);border:1px solid rgba(225,29,46,.3)}
.l-muted-item{color:var(--muted)}
.l-bright-item{color:var(--white);font-weight:500}
/* ── FRAMEWORK ── */
.l-stages{display:grid;grid-template-columns:repeat(7,1fr);gap:3px;background:var(--g3);border-radius:6px;overflow:hidden;margin-top:56px;margin-bottom:16px}
.l-stage{background:var(--black);padding:24px 12px;text-align:center;cursor:pointer;transition:background .2s;position:relative;overflow:hidden}
.l-stage:hover,.l-stage.l-stage-active{background:var(--g2)}
.l-stage.l-stage-active::after{content:'';position:absolute;bottom:0;left:0;right:0;height:3px;background:var(--red)}
.l-stage-num{font-family:'Bebas Neue',sans-serif;font-size:28px;color:var(--red);line-height:1;margin-bottom:6px;opacity:.5}
.l-stage.l-stage-active .l-stage-num{opacity:1}
.l-stage-icon{font-size:20px;margin-bottom:8px;display:block}
.l-stage-name{font-size:10px;font-weight:700;letter-spacing:1px;text-transform:uppercase;color:var(--muted);line-height:1.3}
.l-stage.l-stage-active .l-stage-name{color:var(--white)}
.l-stage-detail{background:var(--g2);border:1px solid var(--g3);border-radius:4px;padding:28px;display:none}
.l-stage-detail.l-stage-active{display:block}
.l-stage-detail h4{font-family:'Bebas Neue',sans-serif;font-size:26px;letter-spacing:1px;margin-bottom:8px}
.l-stage-detail p{font-size:14px;color:var(--light);line-height:1.7;max-width:680px}
/* ── OUTCOMES ── */
.l-outcomes-grid{display:grid;grid-template-columns:repeat(2,1fr);gap:3px;background:var(--g3);border-radius:6px;overflow:hidden;margin-top:56px}
.l-outcome{background:var(--black);padding:28px;display:flex;gap:16px;transition:background .2s}
.l-outcome:hover{background:var(--g2)}
.l-outcome-n{font-family:'Bebas Neue',sans-serif;font-size:13px;color:var(--red);min-width:28px;padding-top:2px}
.l-outcome strong{display:block;font-size:14px;font-weight:700;margin-bottom:6px}
.l-outcome span{font-size:12px;color:var(--muted);line-height:1.6}
/* ── TOOLS ── */
.l-tools-grid{display:grid;grid-template-columns:repeat(4,1fr);gap:3px;background:var(--g3);border-radius:6px;overflow:hidden;margin-top:56px}
.l-tool-cat{background:var(--black);padding:28px 24px}
.l-tool-cat-label{font-size:10px;font-weight:700;letter-spacing:2.5px;text-transform:uppercase;color:var(--red);margin-bottom:16px;padding-bottom:10px;border-bottom:1px solid var(--g3)}
.l-tool-items{display:flex;flex-direction:column;gap:10px}
.l-tool-item{display:flex;align-items:center;gap:10px;font-size:13px;font-weight:500}
.l-tool-icon{width:26px;height:26px;background:var(--g3);border-radius:4px;display:flex;align-items:center;justify-content:center;font-size:13px;flex-shrink:0}
/* ── ABOUT / CREDS ── */
.l-about-grid{display:grid;grid-template-columns:1fr 1fr;gap:3px;background:var(--g3);border-radius:6px;overflow:hidden;margin-top:56px}
.l-about-col{background:var(--black);padding:40px}
.l-about-name{font-family:'Bebas Neue',sans-serif;font-size:38px;letter-spacing:1px;line-height:1;margin-bottom:6px}
.l-about-role{font-size:11px;font-weight:700;letter-spacing:2px;text-transform:uppercase;color:var(--red);margin-bottom:16px}
.l-about-bio{font-size:14px;color:var(--light);line-height:1.75;margin-bottom:14px}
.l-about-tags{display:flex;flex-wrap:wrap;gap:8px;margin-top:16px}
.l-about-tag{font-size:11px;font-weight:600;padding:4px 12px;border:1px solid var(--g4);border-radius:100px;color:var(--muted)}
.l-creds{display:flex;flex-direction:column;gap:10px}
.l-cred{display:flex;gap:14px;padding:14px;background:var(--g2);border:1px solid var(--g3);border-left:3px solid var(--red);border-radius:0 4px 4px 0}
.l-cred-icon{font-size:16px;flex-shrink:0;margin-top:2px}
.l-cred strong{display:block;font-size:13px;font-weight:700;margin-bottom:2px}
.l-cred span{font-size:12px;color:var(--muted)}
/* ── FINAL CTA ── */
.l-final-cta{padding:100px 48px;text-align:center;background:linear-gradient(135deg,var(--g1),var(--black))}
.l-final-cta h2{font-family:'Bebas Neue',sans-serif;font-size:clamp(40px,7vw,88px);line-height:.9;letter-spacing:2px;margin-bottom:16px}
.l-final-cta p{font-size:16px;color:var(--light);max-width:500px;margin:0 auto 36px}
.l-cta-pair{display:flex;gap:14px;justify-content:center;flex-wrap:wrap}
/* ── FOOTER ── */
.l-footer{padding:48px 0 28px;border-top:1px solid var(--g3)}
.l-footer-inner{display:flex;flex-direction:column;align-items:center;text-align:center;gap:10px}
.l-footer-brand{font-family:'Bebas Neue',sans-serif;font-size:22px;letter-spacing:3px}
.l-footer-brand em{color:var(--red);font-style:normal}
.l-footer p{font-size:12px;color:var(--muted)}
.l-footer a{color:var(--red);text-decoration:none}
/* ── RESPONSIVE ── */
@media(max-width:768px){
  .l-nav{padding:0 20px}.l-nav-right .l-nav-link:not(.l-nav-cta){display:none}
  .l-hero{padding:60px 20px}.l-container{padding:0 20px}
  .l-course-grid{grid-template-columns:1fr}
  .l-why-grid{grid-template-columns:1fr 1fr}
  .l-transform-grid,.l-about-grid{grid-template-columns:1fr}
  .l-stages{grid-template-columns:repeat(4,1fr)}
  .l-outcomes-grid{grid-template-columns:1fr}
  .l-tools-grid{grid-template-columns:1fr 1fr}
  .l-paywall,.l-final-cta{padding:60px 20px}
}
@media(max-width:480px){
  .l-why-grid,.l-tools-grid{grid-template-columns:1fr}
  .l-stages{grid-template-columns:repeat(4,1fr)}
  .lang-switcher{flex-wrap:wrap}
}
"""

ONBOARDING_CSS = """
.ob-wrap{min-height:100vh;display:flex;align-items:center;justify-content:center;padding:40px 20px;background:var(--black,#0A0A0A)}
.ob-card{background:#1A1A1A;border:1px solid #242424;border-radius:6px;padding:44px;width:100%;max-width:520px;position:relative}
.ob-card::before{content:'';position:absolute;top:0;left:0;right:0;height:3px;background:#E11D2E;border-radius:3px 3px 0 0}
.ob-brand{font-family:'Bebas Neue',sans-serif;font-size:18px;letter-spacing:3px;color:#fff;margin-bottom:32px;display:block;text-decoration:none}
.ob-brand em{color:#E11D2E;font-style:normal}
.ob-steps{display:flex;align-items:center;gap:6px;margin-bottom:36px}
.ob-step-dot{width:28px;height:28px;border-radius:50%;display:flex;align-items:center;justify-content:center;font-size:11px;font-weight:700;border:2px solid #333;color:#777;transition:all .2s}
.ob-step-dot.ob-active{border-color:#E11D2E;color:#E11D2E;background:rgba(225,29,46,.1)}
.ob-step-dot.ob-done{border-color:#E11D2E;background:#E11D2E;color:#fff}
.ob-step-line{flex:1;height:1px;background:#333}
.ob-heading{font-family:'Bebas Neue',sans-serif;font-size:32px;letter-spacing:1px;color:#fff;margin-bottom:8px}
.ob-sub{font-size:14px;color:#777;margin-bottom:28px}
.ob-options{display:flex;flex-direction:column;gap:10px;margin-bottom:28px}
.ob-option{display:flex;align-items:center;gap:14px;padding:16px 18px;background:#111;border:1px solid #333;border-radius:3px;cursor:pointer;font-size:14px;font-weight:500;color:#bbb;transition:all .15s;position:relative}
.ob-option:hover{border-color:#555;color:#fff;background:#1A1A1A}
.ob-option input[type=radio]{position:absolute;opacity:0;width:0;height:0}
.ob-option.ob-selected{border-color:#E11D2E;color:#fff;background:rgba(225,29,46,.08)}
.ob-option-check{width:18px;height:18px;border-radius:50%;border:2px solid #444;flex-shrink:0;display:flex;align-items:center;justify-content:center;font-size:9px;color:transparent;transition:all .15s}
.ob-option.ob-selected .ob-option-check{border-color:#E11D2E;background:#E11D2E;color:#fff}
.ob-select{width:100%;background:#111;border:1px solid #333;border-radius:3px;color:#bbb;font-family:'DM Sans',sans-serif;font-size:14px;padding:12px 16px;transition:border-color .15s;outline:none;margin-bottom:28px;cursor:pointer}
.ob-select:focus{border-color:#E11D2E}
.ob-select option{background:#1A1A1A}
.ob-form-group{margin-bottom:18px}
.ob-label{display:block;font-size:11px;font-weight:700;letter-spacing:1.5px;text-transform:uppercase;color:#666;margin-bottom:8px}
.ob-input{width:100%;background:#111;border:1px solid #333;border-radius:3px;color:#fff;font-family:'DM Sans',sans-serif;font-size:14px;padding:12px 16px;transition:border-color .15s;outline:none}
.ob-input:focus{border-color:#E11D2E}
.ob-btn{width:100%;padding:14px;background:#E11D2E;color:#fff;font-family:'DM Sans',sans-serif;font-weight:800;font-size:12px;letter-spacing:2px;text-transform:uppercase;border:none;border-radius:3px;cursor:pointer;transition:all .2s}
.ob-btn:hover{background:#c91828;transform:translateY(-1px)}
.ob-back{background:none;border:none;color:#555;font-size:12px;font-weight:600;letter-spacing:1px;text-transform:uppercase;cursor:pointer;padding:0;font-family:'DM Sans',sans-serif;margin-top:14px;display:block}
.ob-back:hover{color:#bbb}
.ob-footer{text-align:center;margin-top:20px;font-size:13px;color:#555}
.ob-footer a{color:#E11D2E;text-decoration:none}
.ob-alert{padding:12px 16px;background:rgba(225,29,46,.1);border:1px solid rgba(225,29,46,.3);border-radius:3px;color:#ff6b7a;font-size:13px;margin-bottom:20px}
.ob-fine{text-align:center;font-size:12px;color:#555;margin-top:12px}
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
document.addEventListener('click', function(e) {
    if (!e.target.closest('.nav-profile')) {
        document.querySelectorAll('.nav-dropdown.open').forEach(m => m.classList.remove('open'));
    }
});
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
  <div class="nav-brand">DAKO <span class="r">STUDIOS</span> BOOTCAMP</div>
  <div class="nav-right">
    <div style="display:flex;align-items:center;gap:10px">
      <div class="progress-wrap" style="width:110px"><div class="progress-fill" style="width:{pct}%"></div></div>
      <span style="color:rgba(255,255,255,.55);font-size:.75rem">Day {student['current_day']}/20</span>
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
  <div class="nav-brand">DAKO <span class="r">STUDIOS</span> — <span style="color:#fbbf24;font-size:.8rem">COACH</span></div>
  <div class="nav-right">
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

    return f"""<div class="landing-page">
<!-- NAV -->
<nav class="l-nav">
  <a href="/" class="l-nav-brand">DAKO<em>.</em>STUDIOS</a>
  <div class="l-nav-right">
    {switcher}
    <a href="#digital-skills" class="l-nav-link">{t["nav_digital"]}</a>
    <a href="#creative-tech" class="l-nav-link">{t["nav_creative"]}</a>
    <a href="/login" class="l-nav-link">{t["nav_login"]}</a>
    <a href="/onboarding" class="l-nav-link l-nav-cta">{t["ds_cta"]}</a>
  </div>
</nav>

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
  <div class="l-container"><div class="l-footer-inner">
    <div class="l-footer-brand">DAKO<em>.</em>STUDIOS</div>
    <p>{t["footer_copy"]}</p>
    <p>{t["footer_contact"]} · <a href="mailto:masiyerdakol@gmail.com">masiyerdakol@gmail.com</a></p>
  </div></div>
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
    <div class="login-logo">
      <div class="logo-text">DAKO <span class="r">STUDIOS</span></div>
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
      <div class="alert alert-info" style="margin-bottom:16px">Days 1–{FREE_DAYS} are <strong>completely free</strong>. No card required to start.</div>
      <form method="POST" action="/register">
        <div class="form-group"><label class="form-label">Full Name</label><input type="text" name="name" placeholder="Your Name" required></div>
        <div class="form-group"><label class="form-label">Email</label><input type="email" name="email" placeholder="your@email.com" required></div>
        <div class="form-group"><label class="form-label">Password</label><input type="password" name="password" placeholder="Min 6 characters" required minlength="6"></div>
        <button class="btn btn-red btn-full">Start Free — Day 1</button>
      </form>
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
async def register(name: str = Form(...), email: str = Form(...), password: str = Form(...)):
    if one("SELECT id FROM students WHERE email=?", (email,)):
        return HTMLResponse(_page("Register", _login_page("Email already registered", "register")))
    sid = run("INSERT INTO students (name,email,password_hash,current_day,paid_access) VALUES (?,?,?,1,0)",
              (name.strip(), email.strip(), _hash(password)))
    tok = _token()
    run("INSERT INTO sessions (token, student_id) VALUES (?,?)", (tok, sid))
    resp = RedirectResponse("/student", 302)
    resp.set_cookie("s_token", tok, httponly=True, max_age=86400 * 7, secure=True, samesite="lax")
    return resp

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
    <a href="/" class="ob-brand">DAKO<em>.</em>STUDIOS</a>
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
    <a href="/" class="ob-brand">DAKO<em>.</em>STUDIOS</a>
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
    <a href="/" class="ob-brand">DAKO<em>.</em>STUDIOS</a>
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
    <a href="/" class="ob-brand">DAKO<em>.</em>STUDIOS</a>
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

    body = f"""
{welcome_overlay}
<div class="container">
  {flash_html}{upgrade_bar}
  <div class="card card-sm">
    <div class="flex items-center justify-between" style="margin-bottom:10px">
      <div><strong>Welcome back, {student['name']}!</strong>
        <span class="text-muted text-sm" style="margin-left:10px">Day {student['current_day']} of 20</span></div>
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
  <div class="text-sm" style="white-space:pre-wrap;margin-bottom:8px">{s['answer_text']}</div>
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
    return f"""<div class="container" style="max-width:600px">
  <div class="card paywall-box">
    <div style="font-size:3rem;margin-bottom:16px">🔒</div>
    <h2 style="font-size:1.5rem;font-weight:800;margin-bottom:8px">Day {day_num} is locked</h2>
    <p class="text-muted" style="margin-bottom:24px">Days {FREE_DAYS+1}–20 require full access. Unlock once and learn forever.</p>
    <div class="paywall-price" style="margin-bottom:8px"><span class="paywall-currency">{sym}</span>{int(price):,}</div>
    <div class="text-muted text-sm" style="margin-bottom:24px">One-time · Lifetime access · All payment methods</div>
    <ul class="feature-list" style="margin-bottom:24px">
      <li>{20 - FREE_DAYS} more days of lessons and missions</li>
      <li>Coach feedback on every submission</li>
      <li>Pay by card, M-Pesa, MTN MoMo, bank transfer</li>
      <li>Digital certificate on completion</li>
    </ul>
    <a href="/payment/checkout" class="btn btn-red btn-lg btn-full">Unlock Full Bootcamp — {sym}{int(price):,}</a>
    <div class="mt-3"><a href="/student" class="text-sm text-muted">← Back to dashboard</a></div>
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
        revision = sum(1 for s in submissions if s["grading_status"] == "revision")
        rows = "".join(
            f'<tr><td>Day {s["assessment_id"]}</td><td>{str(s["submitted_at"])[:10]}</td>'
            f'<td><span class="badge badge-{"approved" if s["grading_status"]=="approved" else "revision" if s["grading_status"]=="revision" else "pending"}">'
            f'{s["grading_status"].title()}</span></td></tr>'
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
            f'<td><span class="badge badge-{"approved" if p["status"]=="success" else "revision"}">{p["status"].title()}</span></td></tr>'
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
        return HTMLResponse(_page("Exam Ended", f"<div class='alert'>Exam session is {state['session_status']}</div>"))
        
    # In a real app we'd render the questions array.
    # For now we just return JSON state
    return state

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
    
    try:
        att = one("SELECT submission_id, assessment_id FROM assessment_attempts WHERE id=?", (attempt_id,))
        finalize_attempt(attempt_id)
        
        if att:
            finalize_submission(att["submission_id"])
            log_assessment_event("exam_submitted", att["submission_id"], att["assessment_id"], 1)
            
        return RedirectResponse("/student/dashboard", 302)
    except HTTPException as e:
        raise e

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
      <span class="badge badge-new" style="margin-left:8px">Day {p['day']}: {curr['title'] if curr else ''}</span></div>
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
            
        if verdict == "approved":
            st = one("SELECT * FROM students WHERE id=?", (sub["student_id"],))
            if st and st["current_day"] == sub["assessment_id"] and st["current_day"] < 20:
                run("UPDATE students SET current_day=current_day+1 WHERE id=?", (st["id"],))

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
            <pre style="background:#1C1C1E;color:#fff;padding:10px;border-radius:6px;font-size:.75rem;overflow-x:auto">{l['payload_json']}</pre>
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


@app.get("/coach/applications", response_class=HTMLResponse)
async def coach_applications(request: Request):
    coach = _get_coach(request)
    if not coach:
        return RedirectResponse("/coach", 302)
    apps = query(
        "SELECT id, name, email, country, background, motivation, created_at "
        "FROM creative_tech_applications ORDER BY created_at DESC"
    )
    rows = "".join(
        f'<tr>'
        f'<td>{a["name"]}</td>'
        f'<td><a href="mailto:{a["email"]}">{a["email"]}</a></td>'
        f'<td>{a["country"]}</td>'
        f'<td style="max-width:240px;white-space:normal">{a["background"][:120]}{"…" if len(a["background"])>120 else ""}</td>'
        f'<td style="max-width:240px;white-space:normal">{a["motivation"][:120]}{"…" if len(a["motivation"])>120 else ""}</td>'
        f'<td style="white-space:nowrap">{a["created_at"][:10]}</td>'
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
  </tr></thead>
  <tbody>{rows if rows else '<tr><td colspan="6" style="padding:16px;color:#888">No applications yet.</td></tr>'}</tbody>
</table>"""
    nav = f'<a href="/coach/dashboard">← Dashboard</a>'
    return HTMLResponse(_page("Applications", body, nav=nav))
