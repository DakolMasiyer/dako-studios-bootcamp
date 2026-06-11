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
PRICE_USD   = float(os.getenv("BOOTCAMP_PRICE_USD", "49"))
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
"""

def _page(title, body, nav=""):
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title} — Dako Studios Bootcamp</title>
<style>{CSS}</style>
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

# ─── Public: Auth ─────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    if _get_student(request): return RedirectResponse("/student", 302)
    return HTMLResponse(_page("Welcome", _login_page()))

@app.get("/login", response_class=HTMLResponse)
async def login_page():
    return HTMLResponse(_page("Login", _login_page(tab="login")))

@app.get("/register", response_class=HTMLResponse)
async def register_page():
    return HTMLResponse(_page("Register", _login_page(tab="register")))

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

# ─── Public: Pricing ──────────────────────────────────────────────────────────

@app.get("/pricing", response_class=HTMLResponse)
async def pricing(request: Request):
    student = _get_student(request)
    cta = '<a href="/payment/checkout" class="btn btn-red btn-lg btn-full">Unlock All 20 Days</a>' if student and not student["paid_access"] else '<a href="/register" class="btn btn-red btn-lg btn-full">Start Free — Days 1–3</a>'
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
      <div class="paywall-price"><span class="paywall-currency">$</span>{int(PRICE_USD)}</div>
      <div class="text-muted text-sm mt-2">One-time payment · Lifetime access · All payment methods</div>
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

    if not FLW_SECRET:
        if _is_local_dev() and ALLOW_PAYMENT_DEV_BYPASS:
            tx_ref = f"dako-{student['id']}-{uuid.uuid4().hex[:8]}"
            now = datetime.utcnow().isoformat()[:19]
            run(
                """
                INSERT OR REPLACE INTO payments (
                    student_id, amount, currency, tx_ref, status, verification_status, verified_at,
                    webhook_received_at, reconciliation_attempts, last_reconciliation_error, flw_ref
                ) VALUES (?, ?, ?, ?, 'success', 'verified', ?, ?, 0, NULL, 'dev-bypass')
                """,
                (student["id"], PRICE_USD, "USD", tx_ref, now, now),
            )
            run("UPDATE students SET paid_access=1 WHERE id=?", (student["id"],))
            log_payment_event("payment_initiated", tx_ref, student["id"], PRICE_USD)
            log_payment_event("payment_verified", tx_ref, student["id"], PRICE_USD, flw_ref="dev-bypass", status="success")
            log_payment_event("enrollment_activated", tx_ref, student["id"], PRICE_USD, flw_ref="dev-bypass")
            return RedirectResponse("/student?payment=success", 302)

        return HTMLResponse(_page("Payment",
            '<div class="container"><div class="alert alert-warn">Payment is not configured yet. '
            'Set FLUTTERWAVE_SECRET_KEY or FLW_CLIENT_SECRET in your environment variables.</div></div>'))

    tx_ref = f"dako-{student['id']}-{uuid.uuid4().hex[:8]}"
    # Insert pending payment record
    run("INSERT OR IGNORE INTO payments (student_id, amount, currency, tx_ref, status) VALUES (?,?,?,?,?)",
        (student["id"], PRICE_USD, "USD", tx_ref, "pending"))
        
    log_payment_event("payment_initiated", tx_ref, student["id"], PRICE_USD)

    payload = {
        "tx_ref": tx_ref,
        "amount": PRICE_USD,
        "currency": "USD",
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
        "payment_options": "card,mobilemoneyghana,mobilemoneyrwanda,mobilemoneyzambia,ussd,banktransfer",
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
async def payment_return(request: Request, tx_ref: str = "", status: str = ""):
    student = _get_student(request)
    if not student:
        return RedirectResponse("/", 302)

    if status == "cancelled" or status == "failed" or not tx_ref:
        return RedirectResponse("/pricing?payment=cancelled", 302)

    # In Phase 2, the frontend redirect is NOT authoritative.
    # The webhook updates the database. We just check the current status here.
    # Wait briefly just in case the webhook is still processing
    import asyncio
    await asyncio.sleep(1.0)
    
    payment = one("SELECT status, verification_status FROM payments WHERE tx_ref=?", (tx_ref,))
    if payment and payment["status"] == "success":
        return RedirectResponse("/student?payment=success", 302)
    elif payment and payment["status"] == "failed":
        return RedirectResponse("/pricing?payment=failed", 302)
    else:
        # Still pending, tell the user we are processing
        return HTMLResponse(_page("Payment Processing",
            '<div class="container"><div class="alert alert-info">We are verifying your payment. It might take a moment. '
            '<a href="/student">Go to dashboard</a>.</div></div>'))

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
    flash_html = ""
    if flash == "success":
        flash_html = '<div class="alert alert-success">Payment confirmed! All 20 days are now unlocked.</div>'

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

    body = f"""<div class="container">
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
        fb = f'<div class="mt-2 text-sm"><strong>Coach feedback:</strong> {s["feedback_summary"]}</div>' if s["feedback_summary"] else ""
        subs_html += f"""<div class="sub-card {st}">
  <div class="flex items-center justify-between" style="margin-bottom:8px">
    <span class="badge {badge_cls}">{label}</span>
    <span class="text-xs text-muted">{str(s['submitted_at'])[:16]}</span>
  </div>
  <div class="text-sm" style="white-space:pre-wrap;margin-bottom:8px">{s['answer_text']}</div>
  {f'<div class="flex gap-2">{shots}</div>' if shots else ""}{fb}
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
    return f"""<div class="container" style="max-width:600px">
  <div class="card paywall-box">
    <div style="font-size:3rem;margin-bottom:16px">🔒</div>
    <h2 style="font-size:1.5rem;font-weight:800;margin-bottom:8px">Day {day_num} is locked</h2>
    <p class="text-muted" style="margin-bottom:24px">Days {FREE_DAYS+1}–20 require full access. Unlock once and learn forever.</p>
    <div class="paywall-price" style="margin-bottom:8px"><span class="paywall-currency">$</span>{int(PRICE_USD)}</div>
    <div class="text-muted text-sm" style="margin-bottom:24px">One-time · Lifetime access · All payment methods</div>
    <ul class="feature-list" style="margin-bottom:24px">
      <li>{20 - FREE_DAYS} more days of lessons and missions</li>
      <li>Coach feedback on every submission</li>
      <li>Pay by card, M-Pesa, MTN MoMo, bank transfer</li>
      <li>Digital certificate on completion</li>
    </ul>
    <a href="/payment/checkout" class="btn btn-red btn-lg btn-full">Unlock Full Bootcamp — ${int(PRICE_USD)}</a>
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
  <form method="POST" action="/coach/grade/{p['id']}" style="display:flex;gap:10px;align-items:flex-start">
    <textarea name="feedback" placeholder="Feedback (optional)..." style="flex:1;min-height:64px;font-size:.85rem"></textarea>
    <div style="display:flex;flex-direction:column;gap:8px;min-width:150px">
      <button name="verdict" value="approved" class="btn btn-green">✓ Pass</button>
      <button name="verdict" value="revision_requested" class="btn btn-orange">⟳ Needs Revision</button>
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
