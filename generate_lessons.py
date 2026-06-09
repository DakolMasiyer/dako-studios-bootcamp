#!/usr/bin/env python3
"""
Generate rich lesson content for all 20 bootcamp days.

Priority order for content generation:
  1. NotebookLM MCP (if available in the running session — attach via Claude Code MCP settings)
  2. Anthropic Claude API (ANTHROPIC_API_KEY env var)

Each day receives:
  - lesson_html : 400-600 word HTML lesson (concept, key terms, worked example)
  - video_url   : YouTube embed URL suggestion (coach pastes the real URL later)
  - lesson_status remains 'draft' — coach reviews at /coach/curriculum before publishing

Usage:
    python generate_lessons.py              # all 20 days
    python generate_lessons.py --day 3      # single day
    python generate_lessons.py --force      # overwrite days that already have content
"""
import argparse
import json
import sqlite3
import sys
from pathlib import Path

DB_PATH = Path("data/bootcamp.db")

# ─── Prompt template ──────────────────────────────────────────────────────────

LESSON_PROMPT = """\
You are an expert digital skills educator writing for adult learners in Africa who
may be encountering these concepts for the first time.

Write a LESSON for:
  Day {day}: {title}
  Learning goal: {goal}
  Mission (what students will do after the lesson): {mission_title}

The lesson must include:
1. <h3>What You Will Learn</h3> — 2-3 bullet points (use <ul><li>)
2. <h3>Core Concepts</h3> — 350-450 word plain-language explanation. Use real-world
   analogies relevant to everyday African life. No jargon without a definition.
3. <h3>Key Terms</h3> — exactly 4 terms as a definition list (<dl><dt>term</dt><dd>definition</dd>)
4. <h3>Step-by-Step Example</h3> — a short worked example showing the concept in action
   (numbered <ol>). Use a realistic scenario from the student's daily context.
5. <h3>Before You Start the Mission</h3> — 2-3 sentence bridge that connects the lesson
   to the upcoming mission task.

Rules:
- Output ONLY the HTML fragment (no <html>, <head>, <body> tags)
- Use only: <h3>, <p>, <ul>, <ol>, <li>, <dl>, <dt>, <dd>, <strong>, <em>, <blockquote>
- Do NOT include any CSS, inline styles, or script tags
- Keep total length under 700 words
- Write in clear, warm, encouraging second-person ("you will", "when you")

Also output on the VERY LAST LINE (after the HTML), a YouTube search query the coach
can use to find a supporting video. Format exactly as:
VIDEO_SEARCH: <your search query here>
"""

# ─── DB helpers ───────────────────────────────────────────────────────────────

def _conn():
    c = sqlite3.connect(str(DB_PATH), timeout=30)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    return c

def get_curriculum():
    c = _conn()
    rows = c.execute("SELECT * FROM curriculum ORDER BY day").fetchall()
    c.close()
    return [dict(r) for r in rows]

def save_lesson(day, lesson_html, video_search):
    c = _conn()
    c.execute(
        "UPDATE curriculum SET lesson_html=?, video_url=?, lesson_status='draft' WHERE day=?",
        (lesson_html.strip(), video_search.strip(), day)
    )
    c.commit()
    c.close()

# ─── Generation backends ──────────────────────────────────────────────────────

def generate_with_anthropic(prompt: str) -> str:
    """Use Anthropic SDK — requires ANTHROPIC_API_KEY in environment."""
    try:
        import anthropic
    except ImportError:
        sys.exit("anthropic package not installed. Run: pip install anthropic")

    import os
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        sys.exit("ANTHROPIC_API_KEY not set. Add it to your .env file.")

    client = anthropic.Anthropic(api_key=key)
    message = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=2048,
        messages=[{"role": "user", "content": prompt}]
    )
    return message.content[0].text


def generate_with_notebooklm(prompt: str, notebook_id: str) -> str:
    """
    Use NotebookLM MCP if it is connected in this Claude Code session.
    The MCP tool name may vary — common names used by community MCP servers:
      - notebooklm_query / notebooklm_chat / notebook_query
    This function attempts known tool names and falls back gracefully.

    To wire up: add the NotebookLM MCP server to your Claude Code MCP settings
    and set NOTEBOOKLM_NOTEBOOK_ID in .env.
    """
    # This function is only reachable when called from within a Claude Code
    # agent session that has the NotebookLM MCP attached. For standalone
    # script execution, the Anthropic SDK path is used instead.
    raise NotImplementedError("NotebookLM MCP must be called from within a Claude Code session.")


def parse_response(raw: str):
    """Split LLM response into (lesson_html, video_search_query)."""
    lines = raw.strip().splitlines()
    video_search = ""
    html_lines = []
    for line in lines:
        if line.startswith("VIDEO_SEARCH:"):
            video_search = line.replace("VIDEO_SEARCH:", "").strip()
        else:
            html_lines.append(line)
    return "\n".join(html_lines).strip(), video_search


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Generate bootcamp lesson content")
    parser.add_argument("--day", type=int, help="Generate only this day (1-20)")
    parser.add_argument("--force", action="store_true", help="Overwrite existing lesson content")
    args = parser.parse_args()

    if not DB_PATH.exists():
        sys.exit(f"Database not found at {DB_PATH}. Run dako_bootcamp_init_db.py first.")

    # Load .env if present
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass

    curriculum = get_curriculum()
    if args.day:
        curriculum = [d for d in curriculum if d["day"] == args.day]
        if not curriculum:
            sys.exit(f"Day {args.day} not found in curriculum.")

    print(f"Generating lessons for {len(curriculum)} day(s)...\n")

    for row in curriculum:
        day = row["day"]
        title = row["title"]
        goal = row["goal"]

        if row["lesson_html"] and not args.force:
            print(f"  Day {day:2d}: skipped (already has content — use --force to overwrite)")
            continue

        mission = json.loads(row["mission_data"])
        prompt = LESSON_PROMPT.format(
            day=day,
            title=title,
            goal=goal,
            mission_title=mission.get("title", "complete the day task")
        )

        print(f"  Day {day:2d}: {title} ... ", end="", flush=True)
        try:
            raw = generate_with_anthropic(prompt)
            lesson_html, video_search = parse_response(raw)
            save_lesson(day, lesson_html, video_search)
            word_count = len(lesson_html.split())
            print(f"done ({word_count} words) | video hint: {video_search[:60]}")
        except Exception as e:
            print(f"FAILED — {e}")

    print("\nAll done. Review and publish lessons at: http://localhost:8000/coach/curriculum")
    print("Set lesson_status to 'published' for each day you want students to see.")


if __name__ == "__main__":
    main()
