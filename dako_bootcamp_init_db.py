#!/usr/bin/env python3
"""
Initialize SQLite database for Dako Studios Bootcamp
Creates schema + seeds 20-day digital skills curriculum
Default coach: admin / coach2024
"""
import os
import hashlib
import json
import sqlite3
from pathlib import Path

from db_adapter import db

DB_PATH = Path(os.getenv("SQLITE_PATH", "data/bootcamp.db"))
DB_PATH.parent.mkdir(parents=True, exist_ok=True)

CURRICULUM = {
    1: {
        "title": "What Exactly Is a Computer?",
        "goal": "Demystify the physical machine — separate hardware from software.",
        "mission": {
            "title": "Hardware vs Software Identification",
            "instructions": "1. Walk around your immediate environment.\n2. Open Notepad (Windows) or TextEdit (Mac).\n3. List 4 pieces of physical hardware you can see.\n4. List 2 pieces of software currently running on your screen.\n5. Save the file as hardware_check.txt.",
            "expected_outcome": "A saved text file named hardware_check.txt listing hardware and software."
        }
    },
    2: {
        "title": "Mouse and Keyboard Mastery",
        "goal": "Command precision movements and understand contextual menus.",
        "mission": {
            "title": "Creating Your Digital Folder Structure",
            "instructions": "1. Go to your desktop.\n2. Right-click and create a new folder named MY DIGITAL LIFE.\n3. Inside it, create three sub-folders: Photos, Documents, Bootcamp Projects.\n4. Take a screenshot showing the folder structure.",
            "expected_outcome": "A nested folder structure at Desktop/MY DIGITAL LIFE/ with three sub-folders."
        }
    },
    3: {
        "title": "The File Detective",
        "goal": "Learn where files live and how to move them efficiently.",
        "mission": {
            "title": "File Organization from Downloads",
            "instructions": "1. Open File Explorer (Windows) or Finder (Mac).\n2. Navigate to your Downloads folder.\n3. Move three files to Desktop/MY DIGITAL LIFE/Documents using cut and paste.\n4. Screenshot the organized Documents folder.",
            "expected_outcome": "Downloads folder cleared; files organized in MY DIGITAL LIFE/Documents."
        }
    },
    4: {
        "title": "Typing Like a Professional",
        "goal": "Learn basic document formatting and professional text layout.",
        "mission": {
            "title": "Writing Your Bootcamp Biography",
            "instructions": "1. Open your word processor (Word, Google Docs, or LibreOffice).\n2. Write a 150–200 word biography: who you are, why you're taking this bootcamp, and your career goals.\n3. Format with two paragraphs, proper capitalization, and bold your name.\n4. Save as my_biography.docx in MY DIGITAL LIFE/Documents.",
            "expected_outcome": "A properly formatted biography document saved in the correct directory."
        }
    },
    5: {
        "title": "Week 1 Project — Building Your Core Architecture",
        "goal": "Combine all Week 1 skills into a clean, organised digital workspace.",
        "mission": {
            "title": "Week 1 Review & Organisation",
            "instructions": "1. Clean up your desktop — remove any clutter.\n2. Create a folder called Week 1 Review inside MY DIGITAL LIFE.\n3. Move hardware_check.txt and my_biography.docx into it.\n4. Screenshot your tidy desktop and the folder contents.",
            "expected_outcome": "A clean desktop with logical nested folders. Earn the Digital Explorer badge!"
        }
    },
    6: {
        "title": "How the Internet Actually Works",
        "goal": "Demystify web traffic, browsers, and URLs.",
        "mission": {
            "title": "Manual Browser Navigation & Bookmarking",
            "instructions": "1. Open your web browser.\n2. Click the address bar.\n3. Type https://www.wikipedia.org and press Enter.\n4. Search for 'Computer Network'.\n5. Bookmark the page.\n6. Screenshot showing the bookmark was saved.",
            "expected_outcome": "Successful navigation to Wikipedia with an active bookmark showing."
        }
    },
    7: {
        "title": "Search Like a Pro",
        "goal": "Use targeted, keyword-driven search queries to find information quickly.",
        "mission": {
            "title": "Targeted Search Execution",
            "instructions": "1. Open Google or DuckDuckGo.\n2. Execute these three searches:\n   - 'Ghana capital city'\n   - 'longest river in Africa'\n   - 'Nigeria population 2025'\n3. Write down each answer in a new document.\n4. Save as search_results.txt and screenshot it.",
            "expected_outcome": "A document containing three verified answers from your searches."
        }
    },
    8: {
        "title": "Email Foundations",
        "goal": "Create an email account, compose professionally, and manage your inbox.",
        "mission": {
            "title": "Email Account Setup & First Message",
            "instructions": "1. If you don't have a Gmail account, create one at gmail.com.\n2. Compose a professional introductory email to yourself (or your bootcamp instructor).\n3. Include your name, the bootcamp start date, and one learning goal.\n4. Screenshot the sent email in your Sent folder.",
            "expected_outcome": "Sent email with professional formatting, a clear subject line, and proper structure."
        }
    },
    9: {
        "title": "Email Mastery & Professional Communication",
        "goal": "Master email etiquette, attachments, and professional tone.",
        "mission": {
            "title": "Professional Email with Attachment",
            "instructions": "1. Open your email client.\n2. Compose a new email to yourself.\n3. Attach your my_biography.docx file.\n4. Use a proper greeting, two body paragraphs, and a professional sign-off.\n5. Screenshot the composed email before sending.",
            "expected_outcome": "A successfully sent email with proper formatting and an attachment."
        }
    },
    10: {
        "title": "Document Mastery — Creating Your First Report",
        "goal": "Build a multi-page professional document with headings and structure.",
        "mission": {
            "title": "Writing a Travel Report",
            "instructions": "1. Create a new document.\n2. Write a 300-word travel report about a place you'd like to visit.\n3. Include: an introduction, three body paragraphs, and a conclusion.\n4. Add a title, format headings (H1/H2), and use proper paragraph spacing.\n5. Save as Travel_Report.docx in Bootcamp Projects.",
            "expected_outcome": "A professional multi-paragraph document with proper heading formatting."
        }
    },
    11: {
        "title": "Web Research & Information Synthesis",
        "goal": "Gather information from multiple web sources and synthesise it clearly.",
        "mission": {
            "title": "Research and Synthesis Project",
            "instructions": "1. Pick a topic you're curious about.\n2. Visit 3 different websites and read about it.\n3. Write a 200-word summary that synthesises information from all three sources.\n4. List your sources at the bottom with URLs.\n5. Screenshot the document.",
            "expected_outcome": "A document with researched content and properly cited sources."
        }
    },
    12: {
        "title": "Digital Content & Safe Downloads",
        "goal": "Safely download, manage, and organise digital media files.",
        "mission": {
            "title": "Managing Downloaded Content",
            "instructions": "1. Go to unsplash.com or pixabay.com (free licensed images).\n2. Download 3 images on a theme of your choice.\n3. Create a folder named Downloaded_Images in MY DIGITAL LIFE/Photos.\n4. Rename each file descriptively (e.g., sunset_beach.jpg).\n5. Screenshot the organised folder.",
            "expected_outcome": "An organised folder with 3 properly named image files."
        }
    },
    13: {
        "title": "Introduction to Cybersecurity",
        "goal": "Understand common digital threats and how to defend against them.",
        "mission": {
            "title": "Identifying Phishing & Scam Indicators",
            "instructions": "1. Create a document called security_audit.txt.\n2. Think of a suspicious email you've seen (or imagine one).\n3. List 5 red flags that would indicate it's a phishing attempt (e.g., urgent language, misspelled domain, unknown sender).\n4. Write 2–3 sentences on what you would do if you received such an email.",
            "expected_outcome": "A document demonstrating the ability to spot social engineering indicators."
        }
    },
    14: {
        "title": "Week 3 Project — Hosting a Virtual Meeting",
        "goal": "Master video conferencing platforms for professional remote collaboration.",
        "mission": {
            "title": "Setting Up & Managing a Video Conference",
            "instructions": "1. Go to meet.google.com or zoom.us.\n2. Start an instant meeting.\n3. Copy the meeting link.\n4. Practice turning video on/off and muting/unmuting your microphone.\n5. Test screen sharing by sharing your Desktop.\n6. Screenshot the active meeting with screen share visible.",
            "expected_outcome": "Successful video conference setup with screen sharing demonstrated in screenshot."
        }
    },
    15: {
        "title": "Cloud Storage & Collaborative Ecosystems",
        "goal": "Master cloud syncing, file sharing, and collaborative document editing.",
        "mission": {
            "title": "Uploading to Google Drive & Sharing",
            "instructions": "1. Open Google Drive (drive.google.com).\n2. Upload your Travel_Report.docx.\n3. Right-click the file → Share → 'Anyone with the link can view'.\n4. Copy the share link.\n5. Save the link in a document named cloud_links.txt.\n6. Screenshot showing the share dialog.",
            "expected_outcome": "A working cloud share link saved locally, with screenshot evidence."
        }
    },
    16: {
        "title": "Ironclad Security — Passwords & 2FA",
        "goal": "Build strong account defences and understand two-factor authentication.",
        "mission": {
            "title": "Password Security Audit & 2FA Setup",
            "instructions": "1. In a document, compare these two passwords: Password123! vs T3dh#9!mZ$q2\n2. Write 3 sentences explaining why the second is more secure.\n3. Go to myaccount.google.com/security.\n4. Review your 2-Step Verification status.\n5. Screenshot the security page showing 2-Step Verification is on (or that you've turned it on).",
            "expected_outcome": "Document showing password analysis, plus screenshot of active 2-Step Verification."
        }
    },
    17: {
        "title": "Meet Your AI Copilot",
        "goal": "Understand what AI tools can and cannot do, and start using them productively.",
        "mission": {
            "title": "Experimenting with AI Text Generation",
            "instructions": "1. Go to chatgpt.com or claude.ai.\n2. Ask: 'Explain how the internet works to a complete beginner in under 150 words.'\n3. Copy the response into a document called ai_experiment.docx.\n4. Write 2–3 sentences evaluating: Was the explanation clear? Was it accurate?\n5. Screenshot the AI conversation.",
            "expected_outcome": "Document containing AI-generated content plus your evaluation."
        }
    },
    18: {
        "title": "The Art of Prompt Engineering",
        "goal": "Write structured, role-based prompts that generate high-quality AI output.",
        "mission": {
            "title": "Advanced Prompt Structuring",
            "instructions": "1. Open your AI tool.\n2. Use this exact prompt structure:\n   'Act as a career coach. I just completed a 20-day digital skills bootcamp. Write a professional 3-sentence CV summary highlighting my skills in file management, email communication, cloud tools, and AI tools.'\n3. Copy the response into ai_experiment.docx.\n4. Write one sentence on how the role-based prompt changed the quality vs. a simple question.\n5. Screenshot the AI output.",
            "expected_outcome": "Professional CV summary generated through structured prompting, with your reflection."
        }
    },
    19: {
        "title": "Digital Portfolio Development",
        "goal": "Organise your learning artifacts into a professional showcase folder.",
        "mission": {
            "title": "Building Your Digital Portfolio",
            "instructions": "1. Open Google Drive.\n2. Create a folder named [YourName]_Digital_Portfolio.\n3. Upload these key documents:\n   - my_biography.docx\n   - Travel_Report.docx\n   - ai_experiment.docx\n   - security_audit.txt\n4. Set the folder sharing to 'Anyone with the link can view'.\n5. Copy the folder link into cloud_links.txt.\n6. Screenshot the portfolio folder.",
            "expected_outcome": "A cloud-based portfolio folder with all documents, accessible via share link."
        }
    },
    20: {
        "title": "Final Capstone — Build Your Digital Life Hub",
        "goal": "Synthesise all 20 days into one comprehensive, shareable digital portfolio.",
        "mission": {
            "title": "Graduation Capstone Submission",
            "instructions": "1. Review all files from Days 1–19.\n2. In Google Drive, create a master folder: [YourName]_Dako_Graduation_Hub.\n3. Inside it, create two sub-folders: 01_Foundations and 02_Projects.\n4. Organise all your documents logically across the two folders.\n5. Set the master folder to 'Anyone with the link can view'.\n6. Write a 100-word reflection: What was your biggest learning? What skill will you use first?\n7. Screenshot the final organised hub.",
            "expected_outcome": "Complete, organised digital portfolio submitted and shared. You've earned the Digital Citizen badge!"
        }
    }
}


def _hash(pwd):
    return hashlib.sha256(pwd.encode()).hexdigest()


def init_db():
    if db.backend_name == "postgresql":
        from db_migration import bootstrap_from_bootstrap_file

        bootstrap_from_bootstrap_file(Path(__file__), db, CURRICULUM, _hash)
        print("Database ready: PostgreSQL")
        print(f"Curriculum:     20 days loaded")
        print(f"Coach login:    admin / coach2024")
        return

    conn = sqlite3.connect(str(DB_PATH), timeout=30)
    cur = conn.cursor()

    cur.executescript("""
    PRAGMA journal_mode=WAL;
    PRAGMA foreign_keys=ON;

    CREATE TABLE IF NOT EXISTS students (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        name          TEXT    NOT NULL,
        email         TEXT    UNIQUE NOT NULL,
        password_hash TEXT    NOT NULL,
        current_day   INTEGER NOT NULL DEFAULT 1,
        paid_access   INTEGER NOT NULL DEFAULT 0,
        cohort_id     INTEGER REFERENCES cohorts(id),
        created_at    TEXT    NOT NULL DEFAULT (datetime('now'))
    );

    CREATE TABLE IF NOT EXISTS sessions (
        token       TEXT    PRIMARY KEY,
        student_id  INTEGER NOT NULL REFERENCES students(id) ON DELETE CASCADE,
        created_at  TEXT    NOT NULL DEFAULT (datetime('now'))
    );

    CREATE TABLE IF NOT EXISTS coaches (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        username      TEXT    UNIQUE NOT NULL,
        password_hash TEXT    NOT NULL,
        name          TEXT    NOT NULL,
        created_at    TEXT    NOT NULL DEFAULT (datetime('now'))
    );

    CREATE TABLE IF NOT EXISTS coach_sessions (
        token      TEXT    PRIMARY KEY,
        coach_id   INTEGER NOT NULL REFERENCES coaches(id) ON DELETE CASCADE,
        created_at TEXT    NOT NULL DEFAULT (datetime('now'))
    );

    CREATE TABLE IF NOT EXISTS cohorts (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        name       TEXT    NOT NULL,
        start_date TEXT    NOT NULL,
        price_usd  REAL    NOT NULL DEFAULT 49.0,
        currency   TEXT    NOT NULL DEFAULT 'USD',
        max_seats  INTEGER,
        is_open    INTEGER NOT NULL DEFAULT 1,
        created_at TEXT    NOT NULL DEFAULT (datetime('now'))
    );

    CREATE TABLE IF NOT EXISTS curriculum (
        day           INTEGER PRIMARY KEY,
        title         TEXT    NOT NULL,
        goal          TEXT    NOT NULL,
        mission_data  TEXT    NOT NULL,
        lesson_html   TEXT    NOT NULL DEFAULT '',
        video_url     TEXT    NOT NULL DEFAULT '',
        lesson_status TEXT    NOT NULL DEFAULT 'draft'
    );

    CREATE TABLE IF NOT EXISTS assessments (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        title TEXT NOT NULL,
        description TEXT NOT NULL,
        assessment_type TEXT NOT NULL,
        cohort_id INTEGER REFERENCES cohorts(id),
        rubric_id INTEGER REFERENCES rubrics(id),
        max_attempts INTEGER NOT NULL DEFAULT 1,
        opens_at TEXT,
        closes_at TEXT,
        created_at TEXT NOT NULL DEFAULT (datetime('now'))
    );

    CREATE TABLE IF NOT EXISTS exams (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        assessment_id INTEGER NOT NULL REFERENCES assessments(id),
        exam_title TEXT NOT NULL,
        duration_minutes INTEGER NOT NULL,
        randomize_questions INTEGER NOT NULL DEFAULT 0,
        randomize_choices INTEGER NOT NULL DEFAULT 0,
        passing_score REAL NOT NULL,
        created_at TEXT NOT NULL DEFAULT (datetime('now'))
    );

    CREATE TABLE IF NOT EXISTS exam_questions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        exam_id INTEGER NOT NULL REFERENCES exams(id),
        question_key TEXT NOT NULL,
        question_type TEXT NOT NULL,
        question_text TEXT NOT NULL,
        points_possible REAL NOT NULL,
        question_order INTEGER NOT NULL,
        created_at TEXT NOT NULL DEFAULT (datetime('now'))
    );

    CREATE TABLE IF NOT EXISTS exam_choices (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        question_id INTEGER NOT NULL REFERENCES exam_questions(id) ON DELETE CASCADE,
        choice_key TEXT NOT NULL,
        choice_text TEXT NOT NULL,
        is_correct INTEGER NOT NULL DEFAULT 0,
        choice_order INTEGER NOT NULL
    );

    CREATE TABLE IF NOT EXISTS exam_attempt_questions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        assessment_attempt_id INTEGER NOT NULL REFERENCES assessment_attempts(id) ON DELETE CASCADE,
        question_id INTEGER NOT NULL REFERENCES exam_questions(id),
        rendered_order INTEGER NOT NULL,
        randomized_seed TEXT NOT NULL,
        created_at TEXT NOT NULL DEFAULT (datetime('now')),
        UNIQUE(assessment_attempt_id, question_id)
    );

    CREATE TABLE IF NOT EXISTS assessment_attempts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        assessment_id INTEGER NOT NULL REFERENCES assessments(id),
        student_id INTEGER NOT NULL REFERENCES students(id),
        submission_id INTEGER REFERENCES submissions(id),
        attempt_number INTEGER NOT NULL,
        session_status TEXT NOT NULL DEFAULT 'active',
        started_at TEXT NOT NULL DEFAULT (datetime('now')),
        expires_at TEXT,
        submitted_at TEXT,
        autosave_at TEXT,
        remaining_seconds INTEGER,
        created_at TEXT NOT NULL DEFAULT (datetime('now'))
    );

    CREATE TABLE IF NOT EXISTS submissions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        assessment_id INTEGER NOT NULL REFERENCES assessments(id),
        student_id INTEGER NOT NULL REFERENCES students(id),
        submission_status TEXT NOT NULL DEFAULT 'draft',
        attempt_number INTEGER NOT NULL DEFAULT 1,
        submitted_at TEXT,
        grading_status TEXT NOT NULL DEFAULT 'pending',
        final_score REAL,
        feedback_summary TEXT,
        created_at TEXT NOT NULL DEFAULT (datetime('now')),
        updated_at TEXT NOT NULL DEFAULT (datetime('now'))
    );

    CREATE TABLE IF NOT EXISTS submission_answers (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        submission_id INTEGER NOT NULL REFERENCES submissions(id) ON DELETE CASCADE,
        question_key TEXT NOT NULL,
        answer_text TEXT,
        answer_json TEXT,
        uploaded_file_path TEXT,
        created_at TEXT NOT NULL DEFAULT (datetime('now'))
    );

    CREATE TABLE IF NOT EXISTS submission_files (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        submission_id INTEGER NOT NULL REFERENCES submissions(id) ON DELETE CASCADE,
        original_filename TEXT NOT NULL,
        stored_path TEXT NOT NULL,
        mime_type TEXT NOT NULL,
        file_size INTEGER NOT NULL,
        uploaded_at TEXT NOT NULL DEFAULT (datetime('now'))
    );

    CREATE TABLE IF NOT EXISTS payments (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        student_id  INTEGER NOT NULL REFERENCES students(id),
        cohort_id   INTEGER REFERENCES cohorts(id),
        amount      REAL    NOT NULL,
        currency    TEXT    NOT NULL,
        tx_ref      TEXT    UNIQUE NOT NULL,
        flw_ref     TEXT    UNIQUE,
        status      TEXT    NOT NULL DEFAULT 'pending',
        verification_status TEXT NOT NULL DEFAULT 'pending',
        webhook_event_id TEXT,
        webhook_received_at TEXT,
        reconciliation_attempts INTEGER NOT NULL DEFAULT 0,
        last_reconciliation_error TEXT,
        raw_provider_payload TEXT,
        created_at  TEXT    NOT NULL DEFAULT (datetime('now')),
        updated_at  TEXT    NOT NULL DEFAULT (datetime('now')),
        verified_at TEXT
    );

    CREATE TABLE IF NOT EXISTS webhook_logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        tx_ref TEXT,
        flw_ref TEXT,
        event_type TEXT,
        payload_json TEXT NOT NULL,
        status TEXT NOT NULL,
        created_at TEXT NOT NULL DEFAULT (datetime('now'))
    );

    CREATE TABLE IF NOT EXISTS assessment_jobs (
        id           TEXT PRIMARY KEY,
        submission_id INTEGER NOT NULL REFERENCES submissions(id) ON DELETE CASCADE,
        status       TEXT NOT NULL DEFAULT 'pending',
        created_at   TEXT NOT NULL DEFAULT (datetime('now')),
        started_at   TEXT,
        completed_at TEXT,
        worker_id TEXT,
        fence_token INTEGER DEFAULT 0,
        retry_count INTEGER DEFAULT 0,
        last_error TEXT,
        run_at TEXT,
        last_heartbeat_at TEXT
    );

    CREATE TABLE IF NOT EXISTS assessment_results (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        job_id       TEXT NOT NULL REFERENCES assessment_jobs(id) ON DELETE CASCADE,
        verdict      TEXT NOT NULL,
        feedback     TEXT,
        graded_by    TEXT,
        created_at   TEXT NOT NULL DEFAULT (datetime('now'))
    );

    CREATE TABLE IF NOT EXISTS rubrics (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        description TEXT NOT NULL,
        rubric_version INTEGER NOT NULL DEFAULT 1,
        pass_threshold REAL NOT NULL,
        created_at TEXT NOT NULL DEFAULT (datetime('now'))
    );

    CREATE TABLE IF NOT EXISTS rubric_sections (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        rubric_id INTEGER NOT NULL REFERENCES rubrics(id) ON DELETE CASCADE,
        section_name TEXT NOT NULL,
        weight_percentage REAL NOT NULL,
        max_score REAL NOT NULL,
        created_at TEXT NOT NULL DEFAULT (datetime('now'))
    );

    CREATE TABLE IF NOT EXISTS rubric_rules (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        section_id INTEGER NOT NULL REFERENCES rubric_sections(id) ON DELETE CASCADE,
        rule_key TEXT NOT NULL,
        rule_description TEXT NOT NULL,
        scoring_type TEXT NOT NULL,
        points_possible REAL NOT NULL,
        created_at TEXT NOT NULL DEFAULT (datetime('now'))
    );

    CREATE TABLE IF NOT EXISTS grading_results (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        submission_id INTEGER NOT NULL REFERENCES submissions(id) ON DELETE CASCADE,
        rubric_id INTEGER NOT NULL REFERENCES rubrics(id),
        total_score REAL NOT NULL,
        pass_fail_status TEXT NOT NULL,
        grading_status TEXT NOT NULL DEFAULT 'completed',
        graded_at TEXT NOT NULL DEFAULT (datetime('now')),
        rubric_version INTEGER NOT NULL
    );

    CREATE TABLE IF NOT EXISTS grading_breakdowns (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        grading_result_id INTEGER NOT NULL REFERENCES grading_results(id) ON DELETE CASCADE,
        section_id INTEGER NOT NULL REFERENCES rubric_sections(id),
        awarded_score REAL NOT NULL,
        max_score REAL NOT NULL,
        feedback_text TEXT,
        created_at TEXT NOT NULL DEFAULT (datetime('now'))
    );

    CREATE TABLE IF NOT EXISTS overrides (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        grading_result_id INTEGER NOT NULL REFERENCES grading_results(id) ON DELETE CASCADE,
        new_total_score REAL NOT NULL,
        new_pass_fail_status TEXT NOT NULL,
        override_reason TEXT NOT NULL,
        reviewer_attribution TEXT NOT NULL,
        created_at TEXT NOT NULL DEFAULT (datetime('now'))
    );

    CREATE TABLE IF NOT EXISTS ai_feedback (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        grading_result_id INTEGER NOT NULL REFERENCES grading_results(id) ON DELETE CASCADE,
        generated_feedback TEXT NOT NULL,
        strengths_summary TEXT NOT NULL,
        weaknesses_summary TEXT NOT NULL,
        improvement_suggestions TEXT NOT NULL,
        ai_model_name TEXT NOT NULL,
        generated_at TEXT NOT NULL DEFAULT (datetime('now')),
        feedback_status TEXT NOT NULL DEFAULT 'visible'
    );

    CREATE TABLE IF NOT EXISTS ai_feedback_jobs (
        id TEXT PRIMARY KEY,
        grading_result_id INTEGER NOT NULL REFERENCES grading_results(id) ON DELETE CASCADE,
        status TEXT NOT NULL DEFAULT 'pending',
        created_at TEXT NOT NULL DEFAULT (datetime('now')),
        started_at TEXT,
        completed_at TEXT,
        worker_id TEXT,
        fence_token INTEGER DEFAULT 0,
        retry_count INTEGER DEFAULT 0,
        last_error TEXT,
        run_at TEXT,
        last_heartbeat_at TEXT
    );
    """)

    # Seed curriculum
    for day, content in CURRICULUM.items():
        cur.execute(
            "INSERT OR REPLACE INTO curriculum (day, title, goal, mission_data) VALUES (?,?,?,?)",
            (day, content["title"], content["goal"], json.dumps(content["mission"]))
        )

    # Default coach account (idempotent)
    cur.execute(
        "INSERT OR IGNORE INTO coaches (username, password_hash, name) VALUES (?,?,?)",
        ("admin", _hash("coach2024"), "Head Coach")
    )

    conn.commit()
    conn.close()

    print(f"Database ready: {DB_PATH}")
    print(f"Curriculum:     20 days loaded")
    print(f"Coach login:    admin / coach2024")


if __name__ == "__main__":
    init_db()
