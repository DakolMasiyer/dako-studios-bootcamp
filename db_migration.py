from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Callable, Iterable


SCHEMA_BLOCK_RE = re.compile(r'cur\.executescript\("""(.*?)"""\)', re.S)
DATETIME_DEFAULT_RE = re.compile(r"TEXT\s+NOT\s+NULL\s+DEFAULT\s+\(datetime\('now'\)\)", re.I)
AUTOINCREMENT_RE = re.compile(r"INTEGER\s+PRIMARY\s+KEY\s+AUTOINCREMENT", re.I)
CREATE_TABLE_RE = re.compile(r"^CREATE\s+TABLE\s+IF\s+NOT\s+EXISTS\s+([A-Za-z_][A-Za-z0-9_]*)", re.I)

PG_TABLE_ORDER = [
    "cohorts",
    "curriculum",
    "coaches",
    "students",
    "sessions",
    "coach_sessions",
    "rubrics",
    "rubric_sections",
    "rubric_rules",
    "assessments",
    "exams",
    "exam_questions",
    "exam_choices",
    "submissions",
    "submission_answers",
    "submission_files",
    "payments",
    "webhook_logs",
    "assessment_jobs",
    "assessment_results",
    "grading_results",
    "grading_breakdowns",
    "overrides",
    "ai_feedback",
    "ai_feedback_jobs",
    "assessment_attempts",
    "exam_attempt_questions",
]


def extract_schema_sql(source_text: str) -> str:
    match = SCHEMA_BLOCK_RE.search(source_text)
    if not match:
        raise ValueError("Could not locate schema block in bootstrap source")
    return match.group(1)


def split_statements(sql: str) -> list[str]:
    statements = []
    for chunk in sql.split(";"):
        statement = chunk.strip()
        if statement:
            statements.append(statement)
    return statements


def _transform_statement_for_postgres(statement: str) -> str:
    statement = statement.strip()
    if not statement or statement.startswith("PRAGMA "):
        return ""

    statement = AUTOINCREMENT_RE.sub("BIGSERIAL PRIMARY KEY", statement)
    statement = DATETIME_DEFAULT_RE.sub("TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP", statement)

    statement = statement.replace("INSERT OR IGNORE INTO coaches", "INSERT INTO coaches")
    statement = statement.replace("INSERT OR REPLACE INTO curriculum", "INSERT INTO curriculum")
    statement = statement.replace("INSERT OR IGNORE INTO", "INSERT INTO")

    if "INSERT INTO curriculum" in statement and "ON CONFLICT" not in statement:
        statement = re.sub(
            r"^INSERT\s+INTO\s+curriculum\s*\((?P<cols>[^)]*)\)\s*VALUES\s*\((?P<vals>[^)]*)\)$",
            lambda m: _curriculum_upsert_sql(m.group("cols"), m.group("vals")),
            statement,
            flags=re.I | re.S,
        )
    elif "INSERT INTO coaches" in statement and "ON CONFLICT" not in statement:
        statement = re.sub(
            r"^INSERT\s+INTO\s+coaches\s*\((?P<cols>[^)]*)\)\s*VALUES\s*\((?P<vals>[^)]*)\)$",
            r"INSERT INTO coaches (\g<cols>) VALUES (\g<vals>) ON CONFLICT (username) DO NOTHING",
            statement,
            flags=re.I | re.S,
        )

    return statement


def _curriculum_upsert_sql(columns: str, values: str) -> str:
    cols = [part.strip() for part in columns.split(",")]
    update_cols = [col for col in cols if col != "day"]
    if not update_cols:
        return f"INSERT INTO curriculum ({columns}) VALUES ({values}) ON CONFLICT (day) DO NOTHING"
    update_clause = ", ".join(f"{col}=EXCLUDED.{col}" for col in update_cols)
    return f"INSERT INTO curriculum ({columns}) VALUES ({values}) ON CONFLICT (day) DO UPDATE SET {update_clause}"


def _transform_schema_for_postgres(schema_sql: str) -> list[str]:
    statements = []
    for statement in split_statements(schema_sql):
        transformed = _transform_statement_for_postgres(statement)
        if transformed:
            statements.append(transformed)
    create_statements = []
    other_statements = []
    for statement in statements:
        match = CREATE_TABLE_RE.match(statement)
        if match:
            create_statements.append(statement)
        else:
            other_statements.append(statement)

    order = {table: index for index, table in enumerate(PG_TABLE_ORDER)}

    def _sort_key(statement: str) -> tuple[int, str]:
        match = CREATE_TABLE_RE.match(statement)
        if not match:
            return (len(order) + 100, statement)
        table = match.group(1).lower()
        return (order.get(table, len(order) + 50), table)

    create_statements.sort(key=_sort_key)
    return create_statements + other_statements


def _apply_statements(conn, statements: Iterable[str]) -> None:
    for statement in statements:
        conn.execute(statement)


def bootstrap_from_bootstrap_file(source_path: Path, adapter, curriculum: dict, hash_fn: Callable[[str], str]) -> None:
    source_text = source_path.read_text()
    schema_sql = extract_schema_sql(source_text)

    if adapter.backend_name == "postgresql":
        statements = _transform_schema_for_postgres(schema_sql)
        with adapter.transaction(immediate=True) as conn:
            _apply_statements(conn, statements)
            _seed_curriculum(conn, curriculum, backend="postgresql")
            _seed_coach(conn, hash_fn, backend="postgresql")
    else:
        with adapter.transaction(immediate=True) as conn:
            _apply_statements(conn, split_statements(schema_sql))
            _seed_curriculum(conn, curriculum, backend="sqlite")
            _seed_coach(conn, hash_fn, backend="sqlite")


def _seed_curriculum(conn, curriculum: dict, backend: str) -> None:
    if backend == "postgresql":
        sql = (
            "INSERT INTO curriculum (day, title, goal, mission_data) VALUES (?, ?, ?, ?) "
            "ON CONFLICT (day) DO UPDATE SET title=EXCLUDED.title, goal=EXCLUDED.goal, mission_data=EXCLUDED.mission_data"
        )
    else:
        sql = "INSERT OR REPLACE INTO curriculum (day, title, goal, mission_data) VALUES (?,?,?,?)"

    for day, content in curriculum.items():
        conn.execute(
            sql,
            (day, content["title"], content["goal"], json.dumps(content["mission"])),
        )


def _seed_coach(conn, hash_fn: Callable[[str], str], backend: str) -> None:
    if backend == "postgresql":
        sql = (
            "INSERT INTO coaches (username, password_hash, name) VALUES (?, ?, ?) "
            "ON CONFLICT (username) DO NOTHING"
        )
    else:
        sql = "INSERT OR IGNORE INTO coaches (username, password_hash, name) VALUES (?,?,?)"

    conn.execute(sql, ("admin", hash_fn("coach2024"), "Head Coach"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Bootstrap the Dako Studios Bootcamp database for the active backend.")
    parser.add_argument(
        "--source",
        default="dako_bootcamp_init_db.py",
        help="Path to the SQLite bootstrap source used as the schema template.",
    )
    args = parser.parse_args(argv)

    from db_adapter import db
    import dako_bootcamp_init_db as bootstrap

    bootstrap_from_bootstrap_file(Path(args.source), db, bootstrap.CURRICULUM, bootstrap._hash)
    print(f"Database ready: {db.backend_name}")
    print("Curriculum:     20 days loaded")
    print("Coach login:    admin / coach2024")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
