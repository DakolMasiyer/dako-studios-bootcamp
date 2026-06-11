#!/bin/bash
# Dako Studios Bootcamp — quick start script

echo ""
echo "DAKO STUDIOS BOOTCAMP"
echo "====================="
echo ""

if ! command -v python3 &>/dev/null; then
    echo "ERROR: Python 3 not found. Install Python 3.8+ and try again."
    exit 1
fi

# Load .env if present
if [ -f ".env" ]; then
    echo "Loading .env ..."
    # shellcheck disable=SC2046
    export $(grep -v '^#' .env | grep -v '^$' | xargs)
fi

# Virtual environment
if [ ! -d "venv" ]; then
    echo "Creating virtual environment..."
    python3 -m venv venv
fi

# shellcheck disable=SC1091
source venv/bin/activate

echo "Installing dependencies..."
pip install -q -r requirements.txt

mkdir -p data uploads/screenshots

# Fresh install vs existing DB
if [ -n "${DATABASE_URL:-}" ]; then
    echo "Initialising PostgreSQL-compatible database..."
    python3 dako_bootcamp_init_db.py
elif [ -f "data/bootcamp.db" ]; then
    echo "Migrating existing database..."
    python3 migrate_db.py
else
    echo "Initialising database..."
    python3 dako_bootcamp_init_db.py
fi

echo ""
echo "Starting server..."
echo ""
echo "  Student portal:   http://localhost:8000"
echo "  Pricing page:     http://localhost:8000/pricing"
echo "  Coach dashboard:  http://localhost:8000/coach"
echo ""
echo "  Set COACH_EMAIL and COACH_PASSWORD in .env before first run"
echo ""
echo "  To generate lesson content:"
echo "  python generate_lessons.py   (requires ANTHROPIC_API_KEY in .env)"
echo ""
echo "Press Ctrl+C to stop."
echo ""

uvicorn bootcamp_app:app --host 0.0.0.0 --port 8000
