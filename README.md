# Dako Studios Bootcamp

Teaching platform for 100 concurrent students learning digital skills across a 20-day curriculum. Built with FastAPI plus managed Postgres and Blob storage for production.

## Quick Start

```bash
bash run.sh
```

The script creates a virtual environment, installs dependencies, initialises the database, and starts the server.

Before first run, create and fill in the runtime config at:

`/Users/dakolmasiyer/Projects/Dako Studios Bootcamp/.env`

Required keys for production:

- `FLUTTERWAVE_SECRET_KEY`
- `FLUTTERWAVE_PUBLIC_KEY`
- `FLW_SECRET_HASH` or `FLUTTERWAVE_WEBHOOK_SECRET`
- `FLUTTERWAVE_ROUTER_URL`
- `FLW_CLIENT_SECRET` or `FLUTTERWAVE_SECRET_KEY`
- `GOOGLE_API_KEY` or `GEMINI_API_KEY`
- `ALLOW_PAYMENT_DEV_BYPASS=false`
- `DATABASE_URL`
- `BLOB_READ_WRITE_TOKEN`

Flutterwave dashboard mapping:

- Public key -> `FLUTTERWAVE_PUBLIC_KEY` or `FLW_CLIENT_ID`
- Secret key -> `FLW_CLIENT_SECRET` or `FLUTTERWAVE_SECRET_KEY`
- Encryption key -> only needed for direct card encryption flows; do not use it as your webhook secret
- Webhook secret hash -> `FLW_SECRET_HASH` or `FLUTTERWAVE_WEBHOOK_SECRET`

Shared Flutterwave router project:

`/Users/dakolmasiyer/Projects/Flutterwave`

Production storage stack:

- `DATABASE_URL` should point to a managed Postgres database from the Vercel Marketplace, preferably Neon on the free tier to start.
- `BLOB_READ_WRITE_TOKEN` should come from a Vercel Blob store created in the project storage settings.
- `FLW_CLIENT_SECRET` or `FLUTTERWAVE_SECRET_KEY` is used as the Flutterwave V3 bearer token for payment creation and verification.
- `FLW_SECRET_HASH` is used to verify incoming webhooks with the `flutterwave-signature` header. If your dashboard only exposes a webhook secret field, store that value there.
- Screenshot uploads use Blob when the token is present; local disk remains a development fallback only.

| URL | Purpose |
|-----|---------|
| http://localhost:8000 | Student portal (register / login) |
| http://localhost:8000/coach | Coach dashboard |

**Default coach credentials:** `admin` / `coach2024`

## How It Works

**Students**
1. Register at the student portal
2. Work through Day 1 to Day 20 in sequence
3. Each day: read the mission instructions, complete the task, write a response, upload a screenshot
4. Submit and wait for coach review
5. Pass: next day unlocks automatically; Needs Revision: resubmit after addressing feedback

**Coaches**
1. Login at `/coach`
2. Dashboard shows all pending submissions in chronological order
3. View student answers and screenshots, write feedback, click Pass or Needs Revision
4. Students tab shows all students with progress bars

## Curriculum (20 Days)

| Days | Theme |
|------|-------|
| 1–5  | Computer and file system fundamentals |
| 6–10 | Internet, search, email, documents |
| 11–15 | Research, cloud storage, cybersecurity |
| 16–20 | Passwords, AI tools, prompt engineering, portfolio |

## Project Structure

```
.
├── bootcamp_app.py          # FastAPI application (all routes, HTML, CSS)
├── dako_bootcamp_init_db.py # Database schema + 20-day curriculum seed
├── requirements.txt         # fastapi, uvicorn, python-multipart
├── run.sh                   # One-command startup script
├── data/
│   └── bootcamp.db          # SQLite database (WAL mode, auto-created)
└── uploads/
    └── screenshots/         # Student-uploaded screenshot files (auto-created)
```

## Technical Details

- **Database:** SQLite locally, PostgreSQL in production via `DATABASE_URL`
- **Sessions:** Stored in SQLite, delivered via `httponly` cookies (no in-memory state, survives restarts)
- **Auth:** SHA-256 password hashing; separate student and coach session tables
- **File uploads:** Saved to Vercel Blob in production when `BLOB_READ_WRITE_TOKEN` is set; local disk remains the fallback for development
- **HTML:** Inline CSS only — no CDN or external assets
- **Auto-advance:** When a coach marks a submission as Pass, the student's current day increments automatically

## Troubleshooting

**Port already in use**
```bash
lsof -ti:8000 | xargs kill -9
bash run.sh
```

**Reset the database**
```bash
rm data/bootcamp.db
python3 dako_bootcamp_init_db.py
```

**Screenshots not saving**
```bash
mkdir -p uploads/screenshots
```

**Switch to production storage**
1. Create a managed Postgres database in Vercel Marketplace and copy its connection string into `DATABASE_URL`.
2. Create a Vercel Blob store in the project Storage tab.
3. Copy the generated token into `BLOB_READ_WRITE_TOKEN`.
4. Redeploy the project so uploads and data use the managed services.
