import logging
import os

import httpx

logger = logging.getLogger("email_service")

RESEND_API_KEY = os.getenv("RESEND_API_KEY")
FROM_EMAIL = os.getenv("EMAIL_FROM", "Dako Studios Bootcamp <noreply@dako.studio>")
COACH_EMAIL = os.getenv("COACH_EMAIL", "masiyerdakol@gmail.com")
BASE_URL = os.getenv("BASE_URL", "http://localhost:8000")

_STYLE = """
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
  background: #0f0f0f; color: #f0f0f0; padding: 40px 20px;
"""
_CARD = """
  background: #1a1a1a; border: 1px solid #2a2a2a; border-radius: 12px;
  max-width: 520px; margin: 0 auto; padding: 40px 36px;
"""
_RED = "color: #e53e3e; font-weight: 800;"
_BTN = (
    "display: inline-block; background: #e53e3e; color: #fff; font-weight: 700;"
    "padding: 14px 28px; border-radius: 8px; text-decoration: none; margin-top: 24px;"
)
_MUTED = "color: #888; font-size: 13px; margin-top: 24px;"


def _html_wrap(body: str) -> str:
    return f'<div style="{_STYLE}"><div style="{_CARD}">{body}</div></div>'


def send_email(to: str, subject: str, html: str) -> bool:
    if not RESEND_API_KEY:
        logger.debug("RESEND_API_KEY not set — skipping email to %s", to)
        return False
    try:
        r = httpx.post(
            "https://api.resend.com/emails",
            headers={"Authorization": f"Bearer {RESEND_API_KEY}", "Content-Type": "application/json"},
            json={"from": FROM_EMAIL, "to": [to], "subject": subject, "html": html},
            timeout=10,
        )
        r.raise_for_status()
        return True
    except Exception as exc:
        logger.warning("Email send failed (to=%s subject=%r): %s", to, subject, exc)
        return False


def send_welcome(student_name: str, to_email: str) -> bool:
    body = _html_wrap(f"""
      <div style="font-size:32px;margin-bottom:16px">🎉</div>
      <h1 style="font-size:1.5rem;font-weight:800;margin:0 0 8px">
        Welcome to <span style="{_RED}">Dako Studios Bootcamp!</span>
      </h1>
      <p style="color:#bbb;margin:0 0 20px">Hi {student_name}, you're all set to begin your 20-day digital skills journey.</p>
      <p style="color:#ddd;margin:0 0 8px"><strong>Here's how it works:</strong></p>
      <ul style="color:#bbb;padding-left:20px;line-height:1.8">
        <li>📚 <strong>Days 1–3 are completely free</strong> — no card required</li>
        <li>🔓 <strong>Days 4–20</strong> unlock with a single payment</li>
        <li>✍️  Submit daily missions for coach review</li>
        <li>🏆 Complete all 20 days to earn your certificate</li>
      </ul>
      <a href="{BASE_URL}/student/day/1" style="{_BTN}">Start Day 1 →</a>
      <p style="{_MUTED}">Questions? Reply to this email — we're here to help.</p>
    """)
    return send_email(to_email, "Welcome to Dako Studios Bootcamp 🎉", body)


def send_payment_confirmed(student_name: str, to_email: str) -> bool:
    body = _html_wrap(f"""
      <div style="font-size:32px;margin-bottom:16px">✅</div>
      <h1 style="font-size:1.5rem;font-weight:800;margin:0 0 8px">Payment confirmed!</h1>
      <p style="color:#bbb;margin:0 0 20px">
        Hi {student_name}, your payment went through successfully.
        All 20 days of the bootcamp are now unlocked.
      </p>
      <a href="{BASE_URL}/student" style="{_BTN}">Go to Dashboard →</a>
      <p style="{_MUTED}">Thank you for joining Dako Studios Bootcamp.</p>
    """)
    return send_email(to_email, "Payment confirmed — all 20 days unlocked", body)


def send_submission_received(student_name: str, to_email: str, day_num: int) -> bool:
    body = _html_wrap(f"""
      <div style="font-size:32px;margin-bottom:16px">📬</div>
      <h1 style="font-size:1.5rem;font-weight:800;margin:0 0 8px">Submission received!</h1>
      <p style="color:#bbb;margin:0 0 20px">
        Hi {student_name}, we've received your <strong>Day {day_num}</strong> submission.
        Your coach will review it shortly — you'll get an email when it's graded.
      </p>
      <a href="{BASE_URL}/student/day/{day_num}" style="{_BTN}">View your submission →</a>
      <p style="{_MUTED}">Keep it up! You're making great progress.</p>
    """)
    return send_email(to_email, f"We got your Day {day_num} submission — coach review starting", body)


def send_day_passed(student_name: str, to_email: str, day_num: int, next_day: int) -> bool:
    body = _html_wrap(f"""
      <div style="font-size:32px;margin-bottom:16px">🎯</div>
      <h1 style="font-size:1.5rem;font-weight:800;margin:0 0 8px">Day {day_num} passed!</h1>
      <p style="color:#bbb;margin:0 0 20px">
        Hi {student_name}, your coach reviewed your Day {day_num} submission — and you passed!
        Day {next_day} is now unlocked.
      </p>
      <a href="{BASE_URL}/student/day/{next_day}" style="{_BTN}">Start Day {next_day} →</a>
      <p style="{_MUTED}">Great work — keep the momentum going!</p>
    """)
    return send_email(to_email, f"Day {day_num} passed! Day {next_day} is now unlocked", body)


def send_revision_requested(student_name: str, to_email: str, day_num: int, feedback: str) -> bool:
    feedback_html = f'<p style="color:#bbb;border-left:3px solid #e53e3e;padding-left:12px;margin:16px 0">{feedback}</p>' if feedback else ""
    body = _html_wrap(f"""
      <div style="font-size:32px;margin-bottom:16px">🔄</div>
      <h1 style="font-size:1.5rem;font-weight:800;margin:0 0 8px">Day {day_num} needs revision</h1>
      <p style="color:#bbb;margin:0 0 4px">
        Hi {student_name}, your coach reviewed your Day {day_num} submission and has requested a revision.
      </p>
      {feedback_html}
      <a href="{BASE_URL}/student/day/{day_num}" style="{_BTN}">Revise and resubmit →</a>
      <p style="{_MUTED}">Don't worry — revisions are part of the learning process.</p>
    """)
    return send_email(to_email, f"Your Day {day_num} submission needs revision", body)


def send_completion(student_name: str, to_email: str) -> bool:
    body = _html_wrap(f"""
      <div style="font-size:48px;margin-bottom:16px">🏆</div>
      <h1 style="font-size:1.8rem;font-weight:800;margin:0 0 8px">
        Congratulations, {student_name}!
      </h1>
      <p style="color:#bbb;margin:0 0 20px">
        You've completed all 20 days of the Dako Studios Bootcamp.
        Your certificate is ready — you've earned it!
      </p>
      <a href="{BASE_URL}/student" style="{_BTN}">View your certificate →</a>
      <p style="{_MUTED}">We're incredibly proud of what you've accomplished.</p>
    """)
    return send_email(to_email, "Congratulations — you've completed the bootcamp! 🏆", body)


def send_coach_new_submission(student_name: str, day_num: int, submission_id: int) -> bool:
    body = _html_wrap(f"""
      <h2 style="font-size:1.2rem;font-weight:800;margin:0 0 12px">New submission to review</h2>
      <p style="color:#bbb;margin:0 0 8px">
        <strong>{student_name}</strong> submitted <strong>Day {day_num}</strong>.
      </p>
      <a href="{BASE_URL}/coach/dashboard" style="{_BTN}">Review on Coach Dashboard →</a>
    """)
    return send_email(COACH_EMAIL, f"New submission: {student_name} — Day {day_num}", body)
