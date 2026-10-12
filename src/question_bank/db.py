from __future__ import annotations

import re
import sqlite3
from pathlib import Path

SCHEMA_VERSION = 5

_MULTISELECT_RE = re.compile(
    r"<single\b[^>]*>\s*(?:false|0|no)\s*</single>", re.IGNORECASE
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_info (
    version INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS imports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    filename TEXT NOT NULL,
    imported_at TEXT NOT NULL DEFAULT (datetime('now')),
    n_questions INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS questions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    moodle_type TEXT NOT NULL,
    name TEXT,
    stem_html TEXT NOT NULL,
    stem_hash TEXT NOT NULL,
    raw_xml TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    raw_category TEXT,
    default_grade REAL,
    penalty REAL,
    general_feedback_html TEXT,
    multichoice_single INTEGER,
    source_import_id INTEGER REFERENCES imports(id) ON DELETE SET NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_questions_status ON questions(status);
CREATE INDEX IF NOT EXISTS idx_questions_stem_hash ON questions(stem_hash);
CREATE INDEX IF NOT EXISTS idx_questions_type ON questions(moodle_type);

CREATE TABLE IF NOT EXISTS question_answers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    question_id INTEGER NOT NULL REFERENCES questions(id) ON DELETE CASCADE,
    ord INTEGER NOT NULL,
    text_html TEXT NOT NULL,
    fraction REAL NOT NULL,
    feedback_html TEXT,
    tolerance REAL
);

CREATE INDEX IF NOT EXISTS idx_answers_question ON question_answers(question_id);

CREATE TABLE IF NOT EXISTS rejection_notes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    question_id INTEGER NOT NULL REFERENCES questions(id) ON DELETE CASCADE,
    rejected_at TEXT NOT NULL DEFAULT (datetime('now')),
    note_text TEXT
);

CREATE INDEX IF NOT EXISTS idx_rejection_question ON rejection_notes(question_id);

CREATE TABLE IF NOT EXISTS assignments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    points_manually_adjusted INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS assignment_questions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    assignment_id INTEGER NOT NULL REFERENCES assignments(id) ON DELETE CASCADE,
    question_id INTEGER NOT NULL REFERENCES questions(id) ON DELETE CASCADE,
    ord INTEGER NOT NULL,
    point_value REAL,
    UNIQUE(assignment_id, question_id)
);

CREATE INDEX IF NOT EXISTS idx_aq_assignment ON assignment_questions(assignment_id);
CREATE INDEX IF NOT EXISTS idx_aq_question ON assignment_questions(question_id);
"""


def connect(db_path: Path | str) -> sqlite3.Connection:
    p = Path(db_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(p, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    _migrate(conn)
    return conn


def _migrate(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    cur = conn.execute("SELECT version FROM schema_info")
    row = cur.fetchone()
    if row is None:
        conn.execute("INSERT INTO schema_info (version) VALUES (?)", (SCHEMA_VERSION,))
        return
    current = row["version"]
    if current < 2:
        _add_column_if_missing(conn, "assignment_questions", "point_value", "REAL")
        conn.execute("UPDATE schema_info SET version = 2")
        current = 2
    if current < 3:
        # every picked question now defaults to 1 point; backfill any NULLs
        # (rows added before point_value existed, or copied from a question's
        # default_grade back when that was the add-time default).
        conn.execute(
            "UPDATE assignment_questions SET point_value = 1.0 "
            "WHERE point_value IS NULL"
        )
        conn.execute("UPDATE schema_info SET version = 3")
        current = 3
    if current < 4:
        _add_column_if_missing(
            conn,
            "assignments",
            "points_manually_adjusted",
            "INTEGER NOT NULL DEFAULT 0",
        )
        conn.execute(
            """UPDATE assignments SET points_manually_adjusted = 1
               WHERE EXISTS (
                   SELECT 1 FROM assignment_questions aq
                   WHERE aq.assignment_id = assignments.id
                     AND ABS(COALESCE(aq.point_value, 1.0) - 1.0) > 0.000001
               )"""
        )
        auto_assignment_ids = [
            row["id"]
            for row in conn.execute(
                "SELECT id FROM assignments WHERE points_manually_adjusted = 0"
            )
        ]
        for assignment_id in auto_assignment_ids:
            _rebalance_assignment_points(conn, assignment_id)
        conn.execute("UPDATE schema_info SET version = 4")
        current = 4
    if current < 5:
        _add_column_if_missing(conn, "questions", "multichoice_single", "INTEGER")
        for row in conn.execute(
            "SELECT id, raw_xml FROM questions WHERE moodle_type = 'multichoice'"
        ):
            single = 0 if _MULTISELECT_RE.search(row["raw_xml"] or "") else 1
            conn.execute(
                "UPDATE questions SET multichoice_single = ? WHERE id = ?",
                (single, row["id"]),
            )
        conn.execute("UPDATE schema_info SET version = 5")


def _add_column_if_missing(
    conn: sqlite3.Connection, table: str, column: str, type_sql: str
) -> None:
    existing = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
    if column not in existing:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {type_sql}")


def create_import(conn: sqlite3.Connection, filename: str, n_questions: int) -> int:
    cur = conn.execute(
        "INSERT INTO imports (filename, n_questions) VALUES (?, ?)",
        (filename, n_questions),
    )
    return cur.lastrowid


def stem_hash_exists(conn: sqlite3.Connection, stem_hash: str) -> int | None:
    row = conn.execute(
        "SELECT id FROM questions WHERE stem_hash = ? LIMIT 1", (stem_hash,)
    ).fetchone()
    return row["id"] if row else None


def insert_question(
    conn: sqlite3.Connection,
    *,
    moodle_type: str,
    name: str | None,
    stem_html: str,
    stem_hash: str,
    raw_xml: str,
    raw_category: str | None,
    default_grade: float | None,
    penalty: float | None,
    general_feedback_html: str | None,
    source_import_id: int | None,
    answers: list[dict],
    multichoice_single: bool | None = None,
) -> int:
    cur = conn.execute(
        """INSERT INTO questions
           (moodle_type, name, stem_html, stem_hash, raw_xml, raw_category,
            default_grade, penalty, general_feedback_html, multichoice_single,
            source_import_id)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            moodle_type,
            name,
            stem_html,
            stem_hash,
            raw_xml,
            raw_category,
            default_grade,
            penalty,
            general_feedback_html,
            (
                int(multichoice_single)
                if moodle_type == "multichoice"
                and multichoice_single is not None
                else (1 if moodle_type == "multichoice" else None)
            ),
            source_import_id,
        ),
    )
    qid = cur.lastrowid
    for i, a in enumerate(answers):
        conn.execute(
            """INSERT INTO question_answers
               (question_id, ord, text_html, fraction, feedback_html, tolerance)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                qid,
                i,
                a["text_html"],
                a["fraction"],
                a.get("feedback_html"),
                a.get("tolerance"),
            ),
        )
    return qid


def get_question(conn: sqlite3.Connection, question_id: int) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM questions WHERE id = ?", (question_id,)
    ).fetchone()


def get_answers(conn: sqlite3.Connection, question_id: int) -> list[sqlite3.Row]:
    return list(
        conn.execute(
            "SELECT * FROM question_answers WHERE question_id = ? ORDER BY ord",
            (question_id,),
        )
    )


def list_questions(
    conn: sqlite3.Connection,
    status: str | None = None,
    moodle_type: str | None = None,
) -> list[sqlite3.Row]:
    sql = "SELECT * FROM questions"
    clauses: list[str] = []
    params: list = []
    if status is not None:
        clauses.append("status = ?")
        params.append(status)
    if moodle_type is not None:
        clauses.append("moodle_type = ?")
        params.append(moodle_type)
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY id"
    return list(conn.execute(sql, params))


def set_status(
    conn: sqlite3.Connection,
    question_id: int,
    status: str,
    rejection_note: str | None = None,
) -> None:
    if status not in ("pending", "approved", "rejected"):
        raise ValueError(f"invalid status: {status}")
    conn.execute(
        "UPDATE questions SET status = ?, updated_at = datetime('now') WHERE id = ?",
        (status, question_id),
    )
    if status == "rejected":
        conn.execute(
            "INSERT INTO rejection_notes (question_id, note_text) VALUES (?, ?)",
            (question_id, rejection_note),
        )


def delete_question(conn: sqlite3.Connection, question_id: int) -> None:
    """Delete a question and everything that references it (answers, rejection
    notes, assignment_questions via cascade)."""
    assignment_ids = [
        row["assignment_id"]
        for row in conn.execute(
            "SELECT assignment_id FROM assignment_questions WHERE question_id = ?",
            (question_id,),
        )
    ]
    conn.execute("DELETE FROM questions WHERE id = ?", (question_id,))
    for assignment_id in assignment_ids:
        _rebalance_assignment_points_if_automatic(conn, assignment_id)


def update_question(
    conn: sqlite3.Connection,
    question_id: int,
    *,
    name: str | None,
    stem_html: str,
    stem_hash: str,
    raw_xml: str,
    raw_category: str | None,
    general_feedback_html: str | None,
    answers: list[dict],
    multichoice_single: bool | None = None,
) -> None:
    conn.execute(
        """UPDATE questions SET
           name = ?, stem_html = ?, stem_hash = ?, raw_xml = ?,
           raw_category = ?, general_feedback_html = ?,
           multichoice_single = COALESCE(?, multichoice_single),
           updated_at = datetime('now')
           WHERE id = ?""",
        (
            name,
            stem_html,
            stem_hash,
            raw_xml,
            raw_category,
            general_feedback_html,
            (
                int(multichoice_single)
                if multichoice_single is not None
                else None
            ),
            question_id,
        ),
    )
    conn.execute("DELETE FROM question_answers WHERE question_id = ?", (question_id,))
    for i, a in enumerate(answers):
        conn.execute(
            """INSERT INTO question_answers
               (question_id, ord, text_html, fraction, feedback_html, tolerance)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                question_id,
                i,
                a["text_html"],
                a["fraction"],
                a.get("feedback_html"),
                a.get("tolerance"),
            ),
        )


def assignments_containing_question(
    conn: sqlite3.Connection, question_id: int
) -> list[sqlite3.Row]:
    return list(
        conn.execute(
            """SELECT a.* FROM assignments a
               JOIN assignment_questions aq ON aq.assignment_id = a.id
               WHERE aq.question_id = ?""",
            (question_id,),
        )
    )


def latest_rejection_note(conn: sqlite3.Connection, question_id: int) -> str | None:
    row = conn.execute(
        """SELECT note_text FROM rejection_notes
           WHERE question_id = ? ORDER BY rejected_at DESC LIMIT 1""",
        (question_id,),
    ).fetchone()
    return row["note_text"] if row else None


def count_by_status(conn: sqlite3.Connection) -> dict[str, int]:
    rows = conn.execute(
        "SELECT status, COUNT(*) AS n FROM questions GROUP BY status"
    ).fetchall()
    return {r["status"]: r["n"] for r in rows}


# ---------- assignments ----------


def create_assignment(conn: sqlite3.Connection, name: str) -> int:
    cur = conn.execute("INSERT INTO assignments (name) VALUES (?)", (name,))
    return cur.lastrowid


def rename_assignment(conn: sqlite3.Connection, assignment_id: int, name: str) -> None:
    conn.execute(
        "UPDATE assignments SET name = ?, updated_at = datetime('now') WHERE id = ?",
        (name, assignment_id),
    )


def delete_assignment(conn: sqlite3.Connection, assignment_id: int) -> None:
    conn.execute("DELETE FROM assignments WHERE id = ?", (assignment_id,))


def get_assignment(conn: sqlite3.Connection, assignment_id: int) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM assignments WHERE id = ?", (assignment_id,)
    ).fetchone()


def list_assignments(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return list(
        conn.execute(
            """SELECT a.*, COUNT(aq.id) AS n_questions
               FROM assignments a
               LEFT JOIN assignment_questions aq ON aq.assignment_id = a.id
               GROUP BY a.id
               ORDER BY a.id DESC"""
        )
    )


def assignment_question_ids(
    conn: sqlite3.Connection, assignment_id: int
) -> list[int]:
    rows = conn.execute(
        """SELECT question_id FROM assignment_questions
           WHERE assignment_id = ? ORDER BY ord""",
        (assignment_id,),
    )
    return [r["question_id"] for r in rows]


def list_assignment_questions(
    conn: sqlite3.Connection, assignment_id: int
) -> list[sqlite3.Row]:
    return list(
        conn.execute(
            """SELECT q.*, aq.ord AS aq_ord, aq.point_value AS aq_point_value
               FROM assignment_questions aq
               JOIN questions q ON q.id = aq.question_id
               WHERE aq.assignment_id = ?
               ORDER BY aq.ord""",
            (assignment_id,),
        )
    )


def add_question_to_assignment(
    conn: sqlite3.Connection, assignment_id: int, question_id: int
) -> bool:
    """Append one question, balancing automatic assignments to 100 points."""
    added = _append_question_to_assignment(conn, assignment_id, question_id)
    if added:
        _rebalance_assignment_points_if_automatic(conn, assignment_id)
        _touch_assignment(conn, assignment_id)
    return added


def add_questions_to_assignment(
    conn: sqlite3.Connection, assignment_id: int, question_ids: list[int]
) -> list[int]:
    """Append multiple questions in order and return the IDs actually added."""
    added = [
        question_id
        for question_id in question_ids
        if _append_question_to_assignment(conn, assignment_id, question_id)
    ]
    if added:
        _rebalance_assignment_points_if_automatic(conn, assignment_id)
        _touch_assignment(conn, assignment_id)
    return added


def _append_question_to_assignment(
    conn: sqlite3.Connection, assignment_id: int, question_id: int
) -> bool:
    existing = conn.execute(
        "SELECT 1 FROM assignment_questions WHERE assignment_id = ? AND question_id = ?",
        (assignment_id, question_id),
    ).fetchone()
    if existing:
        return False
    max_row = conn.execute(
        "SELECT COALESCE(MAX(ord), -1) AS m FROM assignment_questions WHERE assignment_id = ?",
        (assignment_id,),
    ).fetchone()
    next_ord = (max_row["m"] + 1) if max_row else 0
    conn.execute(
        """INSERT INTO assignment_questions
           (assignment_id, question_id, ord, point_value)
           VALUES (?, ?, ?, 1.0)""",
        (assignment_id, question_id, next_ord),
    )
    return True


def _rebalance_assignment_points_if_automatic(
    conn: sqlite3.Connection, assignment_id: int
) -> None:
    assignment = get_assignment(conn, assignment_id)
    if assignment is not None and not assignment["points_manually_adjusted"]:
        _rebalance_assignment_points(conn, assignment_id)


def _rebalance_assignment_points(
    conn: sqlite3.Connection, assignment_id: int
) -> None:
    rows = conn.execute(
        """SELECT id FROM assignment_questions
           WHERE assignment_id = ? ORDER BY ord""",
        (assignment_id,),
    ).fetchall()
    if not rows:
        return
    cents_each, extra_cents = divmod(10_000, len(rows))
    for index, row in enumerate(rows):
        cents = cents_each + (1 if index < extra_cents else 0)
        conn.execute(
            "UPDATE assignment_questions SET point_value = ? WHERE id = ?",
            (cents / 100, row["id"]),
        )


def _touch_assignment(conn: sqlite3.Connection, assignment_id: int) -> None:
    conn.execute(
        "UPDATE assignments SET updated_at = datetime('now') WHERE id = ?",
        (assignment_id,),
    )


def set_assignment_question_points(
    conn: sqlite3.Connection,
    assignment_id: int,
    question_id: int,
    point_value: float | None,
) -> None:
    conn.execute(
        """UPDATE assignment_questions SET point_value = ?
           WHERE assignment_id = ? AND question_id = ?""",
        (point_value, assignment_id, question_id),
    )
    conn.execute(
        """UPDATE assignments
           SET points_manually_adjusted = 1, updated_at = datetime('now')
           WHERE id = ?""",
        (assignment_id,),
    )


def remove_question_from_assignment(
    conn: sqlite3.Connection, assignment_id: int, question_id: int
) -> None:
    conn.execute(
        "DELETE FROM assignment_questions WHERE assignment_id = ? AND question_id = ?",
        (assignment_id, question_id),
    )
    # compact the ord column to keep it contiguous
    rows = conn.execute(
        "SELECT id FROM assignment_questions WHERE assignment_id = ? ORDER BY ord",
        (assignment_id,),
    ).fetchall()
    for i, r in enumerate(rows):
        conn.execute(
            "UPDATE assignment_questions SET ord = ? WHERE id = ?", (i, r["id"])
        )
    _rebalance_assignment_points_if_automatic(conn, assignment_id)
    _touch_assignment(conn, assignment_id)
