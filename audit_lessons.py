#!/usr/bin/env python3
import sqlite3
import re
from pathlib import Path

DB_PATH = Path("data/bootcamp.db")

def count_words(text):
    # Remove HTML tags for word count
    clean_text = re.sub(r'<[^>]+>', ' ', text)
    words = clean_text.split()
    return len(words)

def split_sections(html):
    # Split by <h3> headers
    sections = {}
    pattern = r'<h3>(.*?)</h3>'
    matches = list(re.finditer(pattern, html, re.IGNORECASE))
    
    for i, match in enumerate(matches):
        header = match.group(1).strip()
        start = match.end()
        end = matches[i+1].start() if i + 1 < len(matches) else len(html)
        content = html[start:end].strip()
        sections[header] = content
        
    return sections

# List of typical African analogy keywords
ANALOGY_KEYWORDS = [
    "mpesa", "m-pesa", "mtn", "orange", "airtel", "danfo", "lagos", "nairobi", 
    "accra", "nigeria", "ghana", "kenya", "senegal", "dakar", "kampala", 
    "money", "market", "trader", "matatu", "bodaboda", "boda", "kiosk", "kobo",
    "cedi", "shilling", "naira", "amara", "kofi", "kwame", "chinua", "diallo",
    "chibuike", "chioma"
]

def check_analogies(text):
    found = []
    text_lower = text.lower()
    for kw in ANALOGY_KEYWORDS:
        if kw in text_lower:
            found.append(kw)
    return found

def audit_lesson(day, title, html):
    issues = []
    sections = split_sections(html)
    
    # 1. Check Section Headers
    expected_headers = [
        "What You Will Learn",
        "Core Concepts",
        "Key Terms",
        "Step-by-Step Example",
        "Before You Start the Mission"
    ]
    
    for eh in expected_headers:
        # Match case-insensitive and allow slight variation
        found = False
        for h in sections.keys():
            if eh.lower() in h.lower() or h.lower() in eh.lower():
                found = True
                break
        if not found:
            issues.append(f"Missing section: '{eh}'")
            
    # If key sections are missing, we return early
    if len(sections) < 3:
        return {
            "day": day,
            "title": title,
            "total_words": count_words(html),
            "sections_found": list(sections.keys()),
            "issues": issues + [f"Found only {len(sections)} sections instead of 5."],
            "analogies": []
        }

    # 2. Check "What You Will Learn" bullets
    wywl_content = ""
    for h, c in sections.items():
        if "what you will learn" in h.lower():
            wywl_content = c
            break
    if wywl_content:
        li_count = len(re.findall(r'<li>', wywl_content, re.IGNORECASE))
        if li_count < 2 or li_count > 3:
            issues.append(f"What You Will Learn: has {li_count} bullet points (expected 2-3)")
    else:
        issues.append("What You Will Learn content empty or header not matched exactly")

    # 3. Check "Core Concepts" word count & analogies
    cc_content = ""
    for h, c in sections.items():
        if "core concepts" in h.lower():
            cc_content = c
            break
    cc_words = 0
    analogies_found = []
    if cc_content:
        cc_words = count_words(cc_content)
        if cc_words < 300 or cc_words > 500:
            issues.append(f"Core Concepts: word count is {cc_words} (expected 350-450, tolerating 300-500)")
        analogies_found = check_analogies(cc_content)
        if not analogies_found:
            issues.append("Core Concepts: No African analogies detected (checked keywords like mpesa, MTN, market, etc.)")
    else:
        issues.append("Core Concepts content empty or header not matched exactly")

    # 4. Check "Key Terms" definition list
    kt_content = ""
    for h, c in sections.items():
        if "key terms" in h.lower():
            kt_content = c
            break
    if kt_content:
        dt_count = len(re.findall(r'<dt>', kt_content, re.IGNORECASE))
        dd_count = len(re.findall(r'<dd>', kt_content, re.IGNORECASE))
        if dt_count != 4 or dd_count != 4:
            issues.append(f"Key Terms: has {dt_count} terms and {dd_count} definitions (expected exactly 4)")
    else:
        issues.append("Key Terms content empty or header not matched exactly")

    # 5. Check "Step-by-Step Example" list
    sbs_content = ""
    for h, c in sections.items():
        if "step-by-step" in h.lower() or "example" in h.lower():
            sbs_content = c
            break
    if sbs_content:
        ol_count = len(re.findall(r'<ol>', sbs_content, re.IGNORECASE))
        li_count = len(re.findall(r'<li>', sbs_content, re.IGNORECASE))
        if ol_count == 0:
            issues.append("Step-by-Step Example: Missing ordered list (<ol>)")
        if li_count < 3:
            issues.append(f"Step-by-Step Example: has {li_count} steps (expected at least 3 steps)")
    else:
        issues.append("Step-by-Step Example content empty or header not matched exactly")

    # 6. Check "Before You Start the Mission" bridge
    bys_content = ""
    for h, c in sections.items():
        if "before you start" in h.lower() or "mission" in h.lower():
            bys_content = c
            break
    if bys_content:
        bys_words = count_words(bys_content)
        # 2-3 sentences is about 20-60 words
        if bys_words < 20 or bys_words > 90:
            issues.append(f"Before You Start the Mission: is {bys_words} words (expected 2-3 sentence bridge, ~30-70 words)")
    else:
        # Check if we matched it in one of the other checks
        matched_bys = False
        for h in sections.keys():
            if "mission" in h.lower() or "before" in h.lower():
                matched_bys = True
                break
        if not matched_bys:
            issues.append("Before You Start the Mission content empty or header not matched exactly")

    # 7. Check overall word count
    total_words = count_words(html)
    if total_words > 750:
        issues.append(f"Total length: {total_words} words (expected under 700 words)")

    # 8. Check html tags balance (very simple check for common tags)
    for tag in ['ul', 'ol', 'dl', 'dt', 'dd', 'li', 'p', 'strong', 'em', 'blockquote']:
        open_tags = len(re.findall(r'<' + tag + r'\b', html, re.IGNORECASE))
        close_tags = len(re.findall(r'</' + tag + r'>', html, re.IGNORECASE))
        if open_tags != close_tags:
            issues.append(f"HTML Balance: Unbalanced <{tag}> tag (found {open_tags} open, {close_tags} closed)")

    return {
        "day": day,
        "title": title,
        "total_words": total_words,
        "sections_found": list(sections.keys()),
        "issues": issues,
        "analogies": analogies_found
    }

def main():
    if not DB_PATH.exists():
        print(f"Error: Database not found at {DB_PATH}")
        return
        
    conn = sqlite3.connect(str(DB_PATH))
    cur = conn.cursor()
    
    rows = cur.execute("SELECT day, title, lesson_html, video_url FROM curriculum ORDER BY day").fetchall()
    
    print("=" * 60)
    print("BOOTCAMP CURRICULUM AUDIT REPORT")
    print("=" * 60)
    
    total_issues = 0
    passed_days = []
    failed_days = []
    
    for row in rows:
        day, title, html, video = row
        res = audit_lesson(day, title, html)
        
        print(f"\nDay {day:2d}: {title}")
        print(f"  Total Words: {res['total_words']}")
        print(f"  Video URL / Query: {video}")
        print(f"  Analogies Found: {', '.join(res['analogies']) if res['analogies'] else 'None'}")
        
        if res["issues"]:
            print("  [FAIL] Issues found:")
            for issue in res["issues"]:
                print(f"    - {issue}")
                total_issues += 1
            failed_days.append(day)
        else:
            print("  [PASS] No structural or word count issues.")
            passed_days.append(day)
            
    print("\n" + "=" * 60)
    print("AUDIT SUMMARY")
    print("=" * 60)
    print(f"Total days analyzed: {len(rows)}")
    print(f"Passed: {len(passed_days)} days ({passed_days})")
    print(f"Failed: {len(failed_days)} days ({failed_days})")
    print(f"Total issue count: {total_issues}")
    
    conn.close()

if __name__ == "__main__":
    main()
